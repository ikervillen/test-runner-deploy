"""Contrato temporal MP9; fechas locales de ensayo, sin asignar UTC implícitamente."""

import csv
import hashlib
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pyarrow as pa

from .common import IntegrityError, digest, parse_int64
from .sources import Snapshot

CONTRACT = "mp9_cambio_modelo_v2"
FIELDS = ("ID", "ID_MODELO", "FECHA_ON", "FECHA_OFF", "ID_MAQUINA", "STATUS", "ID_ST1_REL", "ZF_OK")
SCHEMA = pa.schema(
    [
        pa.field("ID", pa.int64(), False),
        pa.field("ID_MODELO", pa.int64(), False),
        pa.field("FECHA_ON", pa.timestamp("us"), False),
        pa.field("FECHA_OFF", pa.timestamp("us"), True),
        pa.field("ID_MAQUINA", pa.int64(), False),
        pa.field("STATUS", pa.int64(), False),
        pa.field("ID_ST1_REL", pa.int64(), True),
        pa.field("ZF_OK", pa.int64(), False),
        pa.field("origen_fenix", pa.string(), False),
    ]
)
type Key = tuple[str, int]


def key(row: dict) -> Key:
    return row["origen_fenix"], row["ID"]


def business_hash(row: dict) -> str:
    return digest([row[f] for f in FIELDS])


def content_hash(rows: dict[Key, dict]) -> str:
    return digest([(k, business_hash(r)) for k, r in sorted(rows.items())])


def timestamp(token: str) -> str:
    value = token.strip()
    if not re.fullmatch(r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}\s+[0-9]{1,2}:[0-9]{2}:[0-9]{2}", value):
        raise ValueError("Fecha local inválida; se requiere día/mes/año hora:minuto:segundo")
    return datetime.strptime(value, "%d/%m/%Y %H:%M:%S").isoformat(timespec="seconds")


@dataclass
class Parsed:
    entries: dict[tuple, list[dict]] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    rows_read: int = 0
    invalid_rows: int = 0
    invalid_keys: set[Key] = field(default_factory=set)

    def add(self, row: dict, refs: list[dict]) -> None:
        entry = (row["origen_fenix"], *(row[f] for f in FIELDS))
        target = self.entries.setdefault(entry, [])
        for ref in refs:
            if ref not in target:
                target.append(ref)


def parse(snapshots: list[Snapshot], encoding: str) -> Parsed:
    result = Parsed()
    for snap in {(s.origin, s.sha256): s for s in snapshots}.values():
        data = Path(snap.bronze_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != snap.sha256:
            raise IntegrityError("La evidencia MP9 no coincide con su huella Bronze")
        evidence = {
            "origin": snap.origin,
            "sha256": snap.sha256,
            "source_path": snap.source_path,
            "bronze_path": snap.bronze_path,
        }
        try:
            text = data.decode(encoding, errors="strict")
        except UnicodeError as exc:
            result.issues.append(
                {
                    "reason": "ENCODING",
                    "raw_hex": data.hex(),
                    "detail": str(exc),
                    "evidence": evidence,
                }
            )
            continue
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=";", strict=True)
        seen = {}
        try:
            for row in reader:
                if not row:
                    continue
                result.rows_read += 1
                raw = list(row)
                ref = {**evidence, "row": reader.line_num, "raw": raw}
                if len(row) == 11 and row[-1] == "":
                    row = row[:-1]
                if len(row) != 10:
                    result.invalid_rows += 1
                    result.issues.append({"reason": "STRUCTURE", "evidence": ref})
                    continue
                try:
                    values = (
                        parse_int64(row[0]),
                        parse_int64(row[1]),
                        timestamp(row[2]),
                        timestamp(row[3]) if row[3].strip() else None,
                        parse_int64(row[4]),
                        parse_int64(row[6]),
                        parse_int64(row[7]) if row[7].strip() else None,
                        parse_int64(row[9]),
                    )
                    if values[5] not in (1, 2) or values[7] not in (0, 1):
                        raise ValueError("STATUS o ZF_OK fuera del contrato")
                    if values[3] is not None and values[3] < values[2]:
                        raise ValueError("FECHA_OFF es anterior a FECHA_ON")
                except ValueError as exc:
                    result.invalid_rows += 1
                    identity = {}
                    try:
                        identifier = parse_int64(row[0])
                        result.invalid_keys.add((snap.origin, identifier))
                        identity = {"ID": identifier, "origen_fenix": snap.origin}
                    except ValueError:
                        pass
                    result.issues.append(
                        {"reason": "INVALID_VALUE", **identity, "detail": str(exc), "evidence": ref}
                    )
                    continue
                entry = (snap.origin, *values)
                ref_key = (entry, tuple(raw))
                if ref_key in seen:
                    seen[ref_key]["duplicate_count"] += 1
                else:
                    ref["duplicate_count"] = 0
                    seen[ref_key] = ref
                    result.entries.setdefault(entry, []).append(ref)
        except csv.Error as exc:
            result.issues.append(
                {
                    "reason": "CSV",
                    "detail": str(exc),
                    "raw_text": text,
                    "row": reader.line_num,
                    "evidence": evidence,
                }
            )
    return result


def decide(parsed: Parsed, existing: dict[Key, dict], models: set[int], held: set[Key]):
    grouped = defaultdict(list)
    for (origin, *values), refs in parsed.entries.items():
        row = {**dict(zip(FIELDS, values, strict=True)), "origen_fenix": origin}
        grouped[key(row)].append((row, refs))
    issues = list(parsed.issues)
    resolved = {}
    evidence = {}
    for identity, variants in sorted(grouped.items()):
        if identity in parsed.invalid_keys:
            continue  # Una versión inválida impide publicar alternativas del mismo ID.
        rows = sorted((r for r, _ in variants), key=business_hash)
        refs = [ref for _, items in variants for ref in items]
        old = existing.get(identity)
        compared = [*rows, *([old] if old else [])]
        changed = [f for f in FIELDS if f != "FECHA_OFF" and len({r[f] for r in compared}) > 1]
        closures = {r["FECHA_OFF"] for r in compared if r["FECHA_OFF"] is not None}
        if len(closures) > 1:
            changed.append("FECHA_OFF")
        if changed or identity in held:
            issues.append(
                {
                    "reason": "CONFLICT" if changed else "REVIEW_REQUIRED",
                    "ID": identity[1],
                    "origen_fenix": identity[0],
                    "changed_fields": changed,
                    "accepted": old,
                    "candidates": rows,
                    "evidence": refs,
                }
            )
            continue
        # El cierre único prevalece sobre la ausencia de cierre, nunca sobre otro cierre.
        resolved[identity] = {**rows[0], "FECHA_OFF": next(iter(closures), None)}
        evidence[identity] = refs

    valid = {}
    for identity, candidate in resolved.items():
        reason = None
        relation = candidate["ID_ST1_REL"]
        if candidate["ID_MODELO"] not in models:
            reason = "MODEL_PENDING"
        elif candidate["STATUS"] == 2:
            target_key = (identity[0], relation)
            target = resolved.get(target_key, existing.get(target_key))
            if relation == candidate["ID"]:
                reason = "RELATION_CONFLICT"
            elif relation is None or target is None or target_key in held:
                reason = "ZC_PENDING"
            elif (
                target["STATUS"] != 1
                or target["ID_MODELO"] != candidate["ID_MODELO"]
                or target["ID_MAQUINA"] != candidate["ID_MAQUINA"]
            ):
                reason = "RELATION_CONFLICT"
        if reason:
            issues.append(
                {
                    "reason": reason,
                    "ID": identity[1],
                    "origen_fenix": identity[0],
                    "candidate": candidate,
                    "accepted": existing.get(identity),
                    "evidence": evidence[identity],
                }
            )
        else:
            valid[identity] = candidate
    # Una ZF no puede depender de una ZC candidata rechazada o pendiente de revisión.
    for identity, candidate in list(valid.items()):
        if candidate["STATUS"] != 2:
            continue
        target = (identity[0], candidate["ID_ST1_REL"])
        if target not in existing and target not in valid:
            issues.append(
                {
                    "reason": "ZC_PENDING",
                    "ID": identity[1],
                    "origen_fenix": identity[0],
                    "candidate": candidate,
                    "accepted": existing.get(identity),
                    "evidence": evidence[identity],
                }
            )
            del valid[identity]

    inserts, updates, matches = [], [], 0
    for identity, row in sorted(valid.items()):
        old = existing.get(identity)
        if old is None:
            inserts.append({**row, "evidence": evidence[identity]})
        elif old != row:
            updates.append({**row, "before": old, "evidence": evidence[identity]})
        else:
            matches += 1
    return inserts, updates, issues, matches
