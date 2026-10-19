"""Tramos AR originales: identidad compuesta, cierres únicos y referencias por código."""

import csv
import hashlib
import io
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa

from .common import IntegrityError, canonical, digest, parse_int64
from .mp9 import timestamp

CONTRACT = "lista_ar_v1"
FIELDS = (
    "COD_AR",
    "FECHA_ON",
    "MAQ_IS",
    "CAVIDAD",
    "STATUS",
    "TIPO",
    "MODELO",
    "FECHA_OFF",
    "COLOR",
    "PROCESO",
    "MOLDE",
    "ID_Agrupación",
)
KEY_FIELDS = tuple(f for f in FIELDS if f != "FECHA_OFF")
INTEGER_FIELDS = {"COD_AR", "MAQ_IS", "STATUS", "TIPO", "MOLDE", "ID_Agrupación"}
NULLABLE = {"CAVIDAD", "FECHA_OFF", "ID_Agrupación"}
REFERENCES = {"COD_AR": "SIP_DEFECTO", "STATUS": "SIP_DEFECTO_STATUS", "TIPO": "SIP_DEFECTO_AR"}
SCHEMA = pa.schema(
    [
        pa.field(
            f,
            pa.timestamp("us")
            if f in {"FECHA_ON", "FECHA_OFF"}
            else pa.int64()
            if f in INTEGER_FIELDS
            else pa.string(),
            f in NULLABLE,
        )
        for f in FIELDS
    ]
    + [pa.field("origen_fenix", pa.string(), False)]
)


def key(row):
    return (row["origen_fenix"], *(row[f] for f in KEY_FIELDS))


def business_hash(row):
    return digest([row[f] for f in FIELDS])


def content_hash(rows):
    return digest(
        [
            (k, business_hash(r))
            for k, r in sorted(rows.items(), key=lambda item: canonical(item[0]))
        ]
    )


def value(field_name, token):
    text = token.strip()
    if not text and field_name in NULLABLE:
        return None
    if field_name in INTEGER_FIELDS:
        return parse_int64(token)
    if field_name in {"FECHA_ON", "FECHA_OFF"}:
        return timestamp(token)
    if not text:
        raise ValueError("Texto obligatorio vacío")
    return text


@dataclass
class Parsed:
    entries: dict[tuple, list[dict]] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    invalid_keys: set[tuple] = field(default_factory=set)
    rows_read: int = 0
    invalid_rows: int = 0

    def add(self, row, refs):
        entry = (row["origen_fenix"], *(row[f] for f in FIELDS))
        target = self.entries.setdefault(entry, [])
        for ref in refs:
            if ref not in target:
                target.append(ref)


def parse(snapshots, encoding):
    result = Parsed()
    for snap in {(s.origin, s.sha256): s for s in snapshots}.values():
        data = Path(snap.bronze_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != snap.sha256:
            raise IntegrityError("La evidencia LISTA_AR no coincide con Bronze")
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
            for raw in reader:
                if not raw:
                    continue
                result.rows_read += 1
                tokens = raw[:-1] if len(raw) == 13 and raw[-1] == "" else raw
                ref = {**evidence, "row": reader.line_num, "raw": list(raw)}
                if len(tokens) != 12:
                    result.invalid_rows += 1
                    result.issues.append({"reason": "STRUCTURE", "evidence": ref})
                    continue
                column = "FECHA_ON"
                try:
                    row = {"origen_fenix": snap.origin}
                    for column, token in zip(FIELDS, tokens, strict=True):
                        row[column] = value(column, token)
                    column = "FECHA_OFF"
                    if row[column] is not None and row[column] < row["FECHA_ON"]:
                        raise ValueError("FECHA_OFF anterior a FECHA_ON")
                except ValueError as exc:
                    result.invalid_rows += 1
                    issue = {
                        "reason": "INVALID_VALUE",
                        "column": column,
                        "detail": str(exc),
                        "origen_fenix": snap.origin,
                        "invalid_payload_hash": digest(tokens),
                        "evidence": ref,
                    }
                    try:
                        identity = (
                            snap.origin,
                            *(value(f, tokens[FIELDS.index(f)]) for f in KEY_FIELDS),
                        )
                        result.invalid_keys.add(identity)
                        issue.update(key=identity, record_key=digest(identity))
                    except ValueError:
                        pass
                    result.issues.append(issue)
                    continue
                entry = (snap.origin, *(row[f] for f in FIELDS))
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


def reference_gaps(row, references):
    return [
        {
            "field": f,
            "family": family,
            "code": row[f],
            "cause": "DICTIONARY_REVIEW" if references[family]["reviews"] else "CODE_MISSING",
        }
        for f, family in REFERENCES.items()
        if references[family]["reviews"] or row[f] not in references[family]["codes"]
    ]


def decide(parsed, existing, references, held):
    grouped = defaultdict(list)
    for (origin, *values), refs in parsed.entries.items():
        row = {"origen_fenix": origin, **dict(zip(FIELDS, values, strict=True))}
        grouped[key(row)].append((row, refs))
    inserts, updates, issues, matches = [], [], list(parsed.issues), 0
    for identity, variants in sorted(grouped.items(), key=lambda item: canonical(item[0])):
        rows = sorted((r for r, _ in variants), key=business_hash)
        refs = [ref for _, items in variants for ref in items]
        old = existing.get(identity)
        closures = {
            r["FECHA_OFF"] for r in [*rows, *([old] if old else [])] if r["FECHA_OFF"] is not None
        }
        detail = {
            "record_key": digest(identity),
            "key": identity,
            "accepted": old,
            "accepted_hash": business_hash(old) if old else None,
            "candidates": rows,
            "candidate_hashes": [business_hash(r) for r in rows],
            "evidence": refs,
        }
        if len(closures) > 1 or identity in held or identity in parsed.invalid_keys:
            issues.append(
                {
                    **detail,
                    "reason": "CLOSURE_CONFLICT" if len(closures) > 1 else "REVIEW_REQUIRED",
                    "changed_fields": ["FECHA_OFF"] if len(closures) > 1 else [],
                }
            )
            continue
        candidate = {**rows[0], "FECHA_OFF": next(iter(closures), None)}
        gaps = reference_gaps(candidate, references)
        if gaps:
            issues.append(
                {
                    "reason": "REFERENCE_PENDING",
                    "record_key": digest(identity),
                    "key": identity,
                    "candidate": candidate,
                    "candidate_hash": business_hash(candidate),
                    "accepted": old,
                    "references": gaps,
                    "evidence": refs,
                }
            )
            continue
        if old is None:
            inserts.append({**candidate, "record_key": digest(identity), "evidence": refs})
        elif old != candidate:
            updates.append(
                {**candidate, "record_key": digest(identity), "before": old, "evidence": refs}
            )
        else:
            matches += 1
    return inserts, updates, issues, matches
