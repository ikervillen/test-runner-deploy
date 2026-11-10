"""Contrato de procedencias: seis campos conservados, ACTIVO sin efecto de negocio."""

import csv
import hashlib
import io
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .common import IntegrityError, blank_input, digest, parse_int64
from .sources import Snapshot

CONTRACT = "sip_defecto_status_aiala_v1"
FIELDS = (
    "id_status_fenix",
    "codigo_status",
    "descripcion",
    "descripcion_corta",
    "codigo_origen",
    "codigo_zona",
)
type Entry = tuple[int, int, str, str, int, int]
type Value = tuple[int, str, str, int, int]


@dataclass
class Parsed:
    pairs: dict[Entry, list[dict]] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    rows_read: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0


def parse(snapshots: list[Snapshot], encoding: str) -> Parsed:
    parsed = Parsed()
    revisions = {(s.origin, s.sha256): s for s in snapshots}
    for snapshot in revisions.values():
        data = Path(snapshot.bronze_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != snapshot.sha256:
            raise IntegrityError("La entrada preparada no coincide con su huella Bronze")
        evidence = {
            "origin": snapshot.origin,
            "sha256": snapshot.sha256,
            "source_path": snapshot.source_path,
            "bronze_path": snapshot.bronze_path,
        }
        if blank_input(data):
            continue
        try:
            text = data.decode(encoding, errors="strict")
        except UnicodeError as exc:
            parsed.issues.append(
                {
                    "reason": "ENCODING",
                    "raw_hex": data.hex(),
                    "detail": str(exc),
                    "evidence": evidence,
                }
            )
            continue
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=";", strict=True)
        seen_refs = {}
        try:
            for row in reader:
                if not row:
                    continue
                parsed.rows_read += 1
                raw = list(row)
                if len(row) == 8 and row[-1] == "":
                    row = row[:-1]
                ref = {**evidence, "row": reader.line_num, "raw": raw}
                if len(row) != 7:
                    parsed.invalid_rows += 1
                    parsed.issues.append({"reason": "STRUCTURE", "evidence": ref})
                    continue
                # Fenix: ID, CODIGO, DESCRIPCION, ORIGEN, DESCRIPCION_CORTA, ZONA, ACTIVO.
                # El esquema Silver acordado coloca ambas descripciones antes de los códigos.
                try:
                    key: Entry = (
                        parse_int64(row[0]),
                        parse_int64(row[1]),
                        row[2].strip(),
                        row[4].strip(),
                        parse_int64(row[3]),
                        parse_int64(row[5]),
                    )
                    if not key[2] or not key[3]:
                        raise ValueError("Las dos descripciones son obligatorias")
                except ValueError as exc:
                    parsed.invalid_rows += 1
                    parsed.issues.append(
                        {"reason": "INVALID_VALUE", "detail": str(exc), "evidence": ref}
                    )
                    continue
                parsed.valid_rows += 1
                # ACTIVO permanece en la evidencia, nunca en la identidad o hash de negocio.
                ref_key = (key, tuple(raw))
                if ref_key in seen_refs:
                    seen_refs[ref_key]["duplicate_count"] += 1
                else:
                    ref["duplicate_count"] = 0
                    seen_refs[ref_key] = ref
                    parsed.pairs.setdefault(key, []).append(ref)
        except csv.Error as exc:
            parsed.issues.append(
                {
                    "reason": "CSV",
                    "detail": str(exc),
                    "raw_text": text,
                    "row": reader.line_num,
                    "evidence": evidence,
                }
            )
    return parsed


def decide(parsed: Parsed, existing: dict[int, Value]) -> tuple[list[dict], list[dict]]:
    by_id = defaultdict(set)
    by_code = defaultdict(set)
    for identifier, value in existing.items():
        by_id[identifier].add(value)
        by_code[value[0]].add(identifier)
    for identifier, *rest in parsed.pairs:
        value = tuple(rest)
        by_id[identifier].add(value)
        by_code[value[0]].add(identifier)
    inserts, issues = [], list(parsed.issues)
    for key, refs in sorted(parsed.pairs.items()):
        identifier, code, *_ = key
        candidate = dict(zip(FIELDS, key, strict=True))
        if len(by_id[identifier]) > 1 or len(by_code[code]) > 1:
            related = {identifier, *by_code[code]}
            accepted = [
                dict(zip(FIELDS, (i, *existing[i]), strict=True))
                for i in sorted(related)
                if i in existing
            ]
            changed = {
                str(old["id_status_fenix"]): [k for k in FIELDS if old[k] != candidate[k]]
                for old in accepted
            }
            issues.append(
                {
                    "reason": "ASSOCIATION_CONFLICT",
                    **candidate,
                    "accepted": accepted,
                    "changed_fields": changed,
                    "values_for_id": sorted(by_id[identifier]),
                    "ids_for_code": sorted(by_code[code]),
                    "evidence": refs,
                }
            )
        elif identifier not in existing:
            inserts.append({**candidate, "evidence": refs})
    return inserts, issues


def content_hash(rows: dict[int, Value]) -> str:
    return digest(sorted(rows.items()))


def statistics(parsed: Parsed) -> dict:
    return {
        "rows_read": parsed.rows_read,
        "unique_pairs": len(parsed.pairs),
        "valid_rows": parsed.valid_rows,
        "consolidated_repetitions": parsed.valid_rows - len(parsed.pairs),
        "invalid_rows": parsed.invalid_rows,
    }
