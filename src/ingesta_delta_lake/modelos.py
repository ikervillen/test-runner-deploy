"""Contrato sip_modelo_aiala_v1; estado descartado y correspondencia uno a uno."""

import csv
import hashlib
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .common import IntegrityError, blank_input, digest
from .sources import Snapshot

CONTRACT = "sip_modelo_aiala_v1"


@dataclass
class Parsed:
    pairs: dict[tuple[int, str], list[dict]] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    rows_read: int = 0


def parse(snapshots: list[Snapshot], encoding: str) -> Parsed:
    parsed = Parsed()
    # Las recepciones repetidas se auditan todas; el mismo contenido/origen se parsea una vez.
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
                if len(row) == 4 and row[-1] == "":
                    row = row[:-1]
                ref = {**evidence, "row": reader.line_num, "raw": raw}
                if len(row) != 3:
                    parsed.issues.append({"reason": "STRUCTURE", "evidence": ref})
                    continue
                token = row[0].strip()
                if not re.fullmatch(r"[+-]?[0-9]+", token):
                    parsed.issues.append({"reason": "INVALID_ID", "evidence": ref})
                    continue
                identifier = int(token)
                if not -(2**63) <= identifier < 2**63 or not row[1].strip():
                    parsed.issues.append({"reason": "INVALID_VALUE", "evidence": ref})
                    continue
                # Preservar texto, letras, separadores y ceros; no normalizar por número.
                ref_key = (identifier, row[1], tuple(raw))
                if ref_key in seen_refs:
                    seen_refs[ref_key]["duplicate_count"] += 1
                else:
                    ref["duplicate_count"] = 0
                    seen_refs[ref_key] = ref
                    parsed.pairs.setdefault((identifier, row[1]), []).append(ref)
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


def decide(parsed: Parsed, existing: dict[int, str]) -> tuple[list[dict], list[dict]]:
    by_id = defaultdict(set)
    by_code = defaultdict(set)
    for identifier, code in [*existing.items(), *parsed.pairs]:
        by_id[identifier].add(code)
        by_code[code].add(identifier)
    issues = list(parsed.issues)
    inserts = []
    for (identifier, code), refs in sorted(parsed.pairs.items()):
        if len(by_id[identifier]) > 1 or len(by_code[code]) > 1:
            issues.append(
                {
                    "reason": "ASSOCIATION_CONFLICT",
                    "id_modelo_fenix": identifier,
                    "codigo_modelo_botella": code,
                    "accepted_code": existing.get(identifier),
                    "codes_for_id": sorted(by_id[identifier]),
                    "ids_for_code": sorted(by_code[code]),
                    "evidence": refs,
                }
            )
        elif identifier not in existing:
            inserts.append(
                {"id_modelo_fenix": identifier, "codigo_modelo_botella": code, "evidence": refs}
            )
    return inserts, issues


def content_hash(rows: dict[int, str]) -> str:
    return digest(sorted(rows.items()))
