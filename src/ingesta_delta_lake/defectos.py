"""Contrato sip_defecto_aiala_v1: identidad ID/código, texto y exclusiones auditadas."""

import csv
import hashlib
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .common import IntegrityError, digest
from .common import parse_int64 as _int64
from .sources import Snapshot

CONTRACT = "sip_defecto_aiala_v1"
FIELDS = ("id_defecto_fenix", "codigo_aviso_rechazo", "descripcion", "descripcion_corta")
NO_USAR = re.compile(r"(?:[0-9]+\s*:\s*)?NO USAR", re.IGNORECASE)


@dataclass
class Parsed:
    pairs: dict[tuple[int, int, str, str], list[dict]] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    exclusions: dict[tuple[str, ...], list[dict]] = field(default_factory=dict)
    rows_read: int = 0
    excluded_rows: int = 0
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
                if len(row) == 6 and row[-1] == "":
                    row = row[:-1]
                ref = {**evidence, "row": reader.line_num, "raw": raw}
                if len(row) != 5:
                    parsed.invalid_rows += 1
                    parsed.issues.append({"reason": "STRUCTURE", "evidence": ref})
                    continue
                description, short = row[2].strip(), row[3].strip()
                if any(NO_USAR.fullmatch(v) for v in (description, short)):
                    parsed.excluded_rows += 1
                    # Tipos e identidad se validan sobre las entradas no excluidas.
                    # MEDIDA no participa ni siquiera en la identidad de la exclusión.
                    key = tuple(v.strip() for v in row[:4])
                    target = parsed.exclusions
                    category = "excluded"
                else:
                    try:
                        identifier, code = _int64(row[0]), _int64(row[1])
                        if not description or not short:
                            raise ValueError("Las dos descripciones son obligatorias")
                    except ValueError as exc:
                        parsed.invalid_rows += 1
                        parsed.issues.append(
                            {"reason": "INVALID_VALUE", "detail": str(exc), "evidence": ref}
                        )
                        continue
                    parsed.valid_rows += 1
                    key = (identifier, code, description, short)
                    target = parsed.pairs
                    category = "valid"
                # Conservar el raw íntegro; comprimir solo repeticiones exactamente iguales.
                ref_key = (category, key, tuple(raw))
                if ref_key in seen_refs:
                    seen_refs[ref_key]["duplicate_count"] += 1
                else:
                    ref["duplicate_count"] = 0
                    seen_refs[ref_key] = ref
                    target.setdefault(key, []).append(ref)
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


def decide(parsed: Parsed, existing: dict[int, tuple]) -> tuple[list[dict], list[dict]]:
    by_id = defaultdict(set)
    by_code = defaultdict(set)
    for identifier, value in existing.items():
        by_id[identifier].add(value)
        by_code[value[0]].add(identifier)
    for identifier, code, description, short in parsed.pairs:
        by_id[identifier].add((code, description, short))
        by_code[code].add(identifier)
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
            issues.append(
                {
                    "reason": "ASSOCIATION_CONFLICT",
                    **candidate,
                    "accepted": accepted,
                    "values_for_id": sorted(by_id[identifier]),
                    "ids_for_code": sorted(by_code[code]),
                    "evidence": refs,
                }
            )
        elif identifier not in existing:
            inserts.append({**candidate, "evidence": refs})
    return inserts, issues


def content_hash(rows: dict[int, tuple]) -> str:
    return digest(sorted(rows.items()))


def statistics(parsed: Parsed) -> dict:
    return {
        "rows_read": parsed.rows_read,
        "unique_pairs": len(parsed.pairs),
        "valid_rows": parsed.valid_rows,
        "consolidated_repetitions": parsed.valid_rows - len(parsed.pairs),
        "excluded_rows": parsed.excluded_rows,
        "excluded_entries": len(parsed.exclusions),
        "invalid_rows": parsed.invalid_rows,
    }
