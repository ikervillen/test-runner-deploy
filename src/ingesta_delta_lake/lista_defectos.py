"""Observaciones MIA originales; claves K/S/G tipadas y evidencia sin preagregación."""

import csv
import hashlib
import io
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa

from .common import IntegrityError, blank_input, canonical, digest, note_observed_time, parse_int64
from .mp9 import timestamp

CONTRACT = "lista_defectos_v2"
FIELDS = (
    "FECHA",
    "MOLDE",
    "LINEA",
    "COD_MAQ_ZF",
    "COD_DEF",
    "INSPECC",
    "RECH",
    *(f"RECH{i}" for i in range(1, 11)),
    "CAVIDAD",
)
K_FIELDS = (*FIELDS[:6], "CAVIDAD")
S_FIELDS = (*FIELDS[:5], "CAVIDAD")
G_FIELDS = (*FIELDS[:4], "CAVIDAD")
SCHEMA = pa.schema(
    [
        pa.field("FECHA", pa.timestamp("us"), False),
        *(pa.field(f, pa.int64(), False) for f in FIELDS[1:17]),
        pa.field("CAVIDAD", pa.string(), True),
        pa.field("origen_fenix", pa.string(), False),
    ]
)


def context(row, fields):
    return (row["origen_fenix"], *(row[f] for f in fields))


def key(row):
    return context(row, K_FIELDS)


def signature(row):
    return context(row, S_FIELDS)


def group(row):
    return context(row, G_FIELDS)


def row_hash(row):
    return digest([row[f] for f in FIELDS])


def content_hash(rows):
    return digest(
        [(k, row_hash(r)) for k, r in sorted(rows.items(), key=lambda item: canonical(item[0]))]
    )


def from_entry(entry):
    return {"origen_fenix": entry[0], **dict(zip(FIELDS, entry[1:], strict=True))}


@dataclass
class Parsed:
    entries: dict[tuple, list[dict]] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    invalid_groups: set[tuple] = field(default_factory=set)
    rows_read: int = 0
    invalid_rows: int = 0
    observed: dict[str, str] = field(default_factory=dict)


def parse(snapshots, encoding):
    result = Parsed()
    for snap in {(s.origin, s.sha256): s for s in snapshots}.values():
        data = Path(snap.bronze_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != snap.sha256:
            raise IntegrityError("La revisión LISTA_DEFECTOS no coincide con Bronze")
        evidence = {
            "origin": snap.origin,
            "sha256": snap.sha256,
            "source_path": snap.source_path,
            "bronze_path": snap.bronze_path,
        }
        if blank_input(data):
            note_observed_time(result.observed, snap.origin, snap.producer_time)
            continue
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
            for raw_row in reader:
                if not raw_row:
                    continue
                result.rows_read += 1
                row = raw_row[:-1] if len(raw_row) == 19 and raw_row[-1] == "" else raw_row
                ref = {**evidence, "row": reader.line_num, "raw": list(raw_row)}
                if len(row) != 18:
                    result.invalid_rows += 1
                    result.issues.append({"reason": "STRUCTURE", "evidence": ref})
                    continue
                column = "FECHA"
                try:
                    values = {"FECHA": timestamp(row[0])}
                    for column, token in zip(FIELDS[1:17], row[1:17], strict=True):
                        value = parse_int64(token)
                        if column not in {"LINEA", "COD_MAQ_ZF", "COD_DEF"} and value < 0:
                            raise ValueError("Molde y cantidades deben ser no negativos")
                        values[column] = value
                    values["CAVIDAD"] = row[17].strip() or None
                except ValueError as exc:
                    result.invalid_rows += 1
                    issue = {
                        "reason": "INVALID_VALUE",
                        "column": column,
                        "detail": str(exc),
                        "invalid_payload_hash": digest(row),
                        "origen_fenix": snap.origin,
                        "evidence": ref,
                    }
                    try:
                        g = (
                            snap.origin,
                            timestamp(row[0]),
                            *(parse_int64(x) for x in row[1:4]),
                            row[17].strip() or None,
                        )
                        result.invalid_groups.add(g)
                        issue["group"] = g
                    except ValueError:
                        pass
                    result.issues.append(issue)
                    continue
                entry = (snap.origin, *(values[f] for f in FIELDS))
                ref_key = (entry, tuple(raw_row))
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


def indexes(rows):
    by_key, by_signature, by_group = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        by_key[key(row)].append(row)
        by_signature[signature(row)].append(row)
        by_group[group(row)].append(row)
    return by_key, by_signature, by_group


def decide(parsed, existing, held_keys=(), held_signatures=(), held_groups=()):
    candidates = [(from_entry(e), refs) for e, refs in parsed.entries.items()]
    all_k, all_s, all_g = indexes([*existing.values(), *(r for r, _ in candidates)])
    old_k, old_s, old_g = indexes(existing.values())
    held_keys, held_signatures = set(held_keys), set(held_signatures)
    held_groups = set(held_groups) | parsed.invalid_groups
    inserts, issues, matches = [], list(parsed.issues), 0
    for row, refs in sorted(
        candidates, key=lambda item: (canonical(key(item[0])), row_hash(item[0]))
    ):
        k, s, g = key(row), signature(row), group(row)
        if k in existing and row_hash(row) == row_hash(existing[k]):
            matches += 1
            continue
        reason = None
        if len({row_hash(r) for r in all_k[k]}) > 1:
            reason, accepted, compared = "ROW_CONFLICT", old_k[k], all_k[k]
        elif len({r["INSPECC"] for r in all_s[s]}) > 1:
            reason, accepted, compared = "COUNTER_REVISION", old_s[s], all_s[s]
        elif len({r["INSPECC"] for r in all_g[g]}) > 1:
            reason, accepted, compared = "INSPECTION_GROUP_CONFLICT", old_g[g], all_g[g]
        elif k in held_keys or s in held_signatures or g in held_groups:
            reason, accepted, compared = "REVIEW_REQUIRED", old_k[k], all_k[k]
        if reason:
            unique = {row_hash(r): {f: r[f] for f in SCHEMA.names} for r in compared}
            issues.append(
                {
                    "reason": reason,
                    "record_key": digest(k),
                    "key": k,
                    "signature": s,
                    "group": g,
                    "candidate": row,
                    "candidate_hash": row_hash(row),
                    "accepted": sorted(accepted, key=row_hash),
                    "accepted_hashes": sorted({row_hash(r) for r in accepted}),
                    "compared": [unique[h] for h in sorted(unique)],
                    "changed_fields": [f for f in FIELDS if any(row[f] != r[f] for r in compared)],
                    "inspection_values": sorted({r["INSPECC"] for r in all_g[g]}),
                    "evidence": refs,
                }
            )
        else:
            inserts.append({**row, "record_key": digest(k), "evidence": refs})
    return inserts, issues, matches


def inspection_groups(rows, issues):
    by_group = indexes(rows.values())[2]
    affected = {tuple(i["group"]) for i in issues if "group" in i}
    unknown_origins = set()
    for issue in issues:
        if "group" not in issue:
            refs = issue.get("evidence", {})
            refs = refs if isinstance(refs, list) else [refs]
            unknown_origins.update(r.get("origin") for r in refs)
    result = []
    for g, records in sorted(by_group.items(), key=lambda item: canonical(item[0])):
        values = sorted({r["INSPECC"] for r in records})
        resolved = len(values) == 1 and g not in affected and g[0] not in unknown_origins
        result.append(
            {
                "group_id": digest(g),
                "group": g,
                "status": "RESOLVED" if resolved else "REVIEW",
                "INSPECC": values[0] if resolved else None,
                "accepted_inspection_values": values,
                "defect_rows": len(records),
            }
        )
    return result
