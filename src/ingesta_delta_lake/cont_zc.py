"""CONT_ZC: conservación exacta de 17 campos y rendimiento por observación física."""

import csv
import hashlib
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from pathlib import Path

import pyarrow as pa

from .common import IntegrityError, blank_input, digest, note_observed_time, parse_int64
from .live_epoch import before_epoch, epoch
from .mp9 import timestamp

CONTRACT = "cont_zc_v1"
FIELDS = (
    "MAQ_IS",
    "FECHA",
    "CONT_ZC_1",
    "CONT_ZC_2",
    "MAQUINA_A",
    "MAQUINA_B",
    "TUNEL",
    "RUEDA",
    "IMP_ARCA",
    "TEORICO_A",
    "TEORICO_B",
    "EXPULSOR",
    "CONT_ZC_4",
    "VEL_A",
    "VEL_B",
    "CONT_ZC_3",
    "SALIDA_MAQ_IS",
)
SPEEDS = {"VEL_A", "VEL_B"}
SCHEMA = pa.schema(
    [
        pa.field(
            f,
            pa.timestamp("us")
            if f == "FECHA"
            else pa.decimal128(12, 2)
            if f in SPEEDS
            else pa.int64(),
            False,
        )
        for f in FIELDS
    ]
    + [pa.field("origen_fenix", pa.string(), False)]
)


def speed(token):
    """DECIMAL(12,2), sin float ni redondeo; ceros de escala adicionales son equivalentes."""
    text = token.strip()
    if not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", text):
        raise ValueError("Velocidad decimal inválida; usar punto decimal sin exponente")
    number = text.lstrip("+-")
    integer, _, fraction = number.partition(".")
    integer = integer.lstrip("0") or "0"
    significant_fraction = fraction.rstrip("0")
    if len(integer) > 10 or len(significant_fraction) > 2:
        raise ValueError("Velocidad no representable exactamente en DECIMAL(12,2)")
    if text.startswith("-") and (integer != "0" or significant_fraction):
        raise ValueError("Velocidad negativa")
    return integer + "." + significant_fraction.ljust(2, "0")


def key(row):
    return row["origen_fenix"], row["MAQ_IS"], row["FECHA"]


def row_hash(row):
    return digest([row[f] for f in FIELDS])


def content_hash(rows):
    return digest([(k, row_hash(r)) for k, r in sorted(rows.items())])


def value(column, token):
    if column == "FECHA":
        return timestamp(token)
    if column in SPEEDS:
        return speed(token)
    result = parse_int64(token)
    if column == "SALIDA_MAQ_IS" and result not in (0, 1):
        raise ValueError("SALIDA_MAQ_IS debe ser 0 o 1")
    if column != "MAQ_IS" and result < 0:
        raise ValueError("Contador negativo")
    return result


@dataclass
class Parsed:
    entries: dict[tuple, list[dict]] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    invalid_keys: set[tuple] = field(default_factory=set)
    rows_read: int = 0
    invalid_rows: int = 0
    observed: dict[str, str] = field(default_factory=dict)


def parse(snapshots, encoding):
    result = Parsed()
    floor = epoch()
    for snap in {(s.origin, s.sha256): s for s in snapshots}.values():
        data = Path(snap.bronze_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != snap.sha256:
            raise IntegrityError("CONT_ZC no coincide con su evidencia Bronze")
        evidence = {
            "origin": snap.origin,
            "source_path": snap.source_path,
            "bronze_path": snap.bronze_path,
            "sha256": snap.sha256,
            "mtime_ns": snap.mtime_ns,
            "received_at": snap.received_at,
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
            for raw in reader:
                if not raw:
                    continue
                if before_epoch(raw, 1, floor):
                    continue
                result.rows_read += 1
                tokens = raw[:-1] if len(raw) == 18 and raw[-1] == "" else raw
                ref = {**evidence, "row": reader.line_num, "raw": list(raw)}
                if len(tokens) != 17:
                    result.invalid_rows += 1
                    result.issues.append({"reason": "STRUCTURE", "evidence": ref})
                    continue
                column = "MAQ_IS"
                try:
                    row = {"origen_fenix": snap.origin}
                    for column, token in zip(FIELDS, tokens, strict=True):
                        row[column] = value(column, token)
                except ValueError as exc:
                    result.invalid_rows += 1
                    issue = {
                        "reason": "INVALID_VALUE",
                        "column": column,
                        "detail": str(exc),
                        "invalid_payload_hash": digest(tokens),
                        "origen_fenix": snap.origin,
                        "evidence": ref,
                    }
                    try:
                        identity = (snap.origin, parse_int64(tokens[0]), timestamp(tokens[1]))
                        issue.update(key=identity, record_key=digest(identity))
                        result.invalid_keys.add(identity)
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
                    "raw_text": text,
                    "row": reader.line_num,
                    "detail": str(exc),
                    "evidence": evidence,
                }
            )
    return result


def _arrival(refs):
    """Later file, then later row. The repeated minute keeps this occurrence."""

    def rank(ref):
        return (
            ref.get("mtime_ns") or 0,
            str(ref.get("received_at") or ""),
            str(ref.get("source_path") or ""),
            ref.get("row") or 0,
        )

    return max((rank(ref) for ref in refs), default=(0, "", "", 0))


def decide(parsed, existing, held):
    """Same origin, machine and minute: the later record replaces the earlier one.

    A repeated local minute is not a review. Invalid values and keys already held
    for another reason still block that identity.
    """
    groups = defaultdict(list)
    for (origin, *values), refs in parsed.entries.items():
        row = {"origen_fenix": origin, **dict(zip(FIELDS, values, strict=True))}
        groups[key(row)].append((row, refs))
    inserts, issues, matches = [], list(parsed.issues), 0
    for identity, variants in sorted(groups.items()):
        old = existing.get(identity)
        ordered = sorted(variants, key=lambda item: _arrival(item[1]))
        winner, winner_refs = ordered[-1]
        refs = [ref for _, items in ordered for ref in items]
        if identity in held or identity in parsed.invalid_keys:
            candidates = sorted((r for r, _ in variants), key=row_hash)
            compared = [*candidates, *([old] if old else [])]
            issues.append(
                {
                    "reason": "REVIEW_REQUIRED",
                    "record_key": digest(identity),
                    "key": identity,
                    "accepted": old,
                    "accepted_hash": row_hash(old) if old else None,
                    "candidates": candidates,
                    "candidate_hashes": [row_hash(r) for r in candidates],
                    "changed_fields": [f for f in FIELDS if len({r[f] for r in compared}) > 1],
                    "evidence": refs,
                }
            )
            continue
        if old is not None and row_hash(old) == row_hash(winner):
            matches += 1
            continue
        inserts.append({**winner, "record_key": digest(identity), "evidence": winner_refs})
    return inserts, issues, matches


def affected(issues):
    keys, origins = set(), set()
    for issue in issues:
        if "key" in issue:
            keys.add(tuple(issue["key"]))
        else:
            refs = issue.get("evidence", {})
            refs = refs if isinstance(refs, list) else [refs]
            origins.update(r.get("origin") for r in refs)
    return keys, origins


def yield_summary(rows, issues=()):
    """Suma rueda/teóricos del mismo conjunto resuelto; nunca diferencia contadores."""
    selected = list(rows)
    if len({key(r) for r in selected}) != len(selected):
        raise IntegrityError("El rendimiento no admite observaciones físicas duplicadas")
    held, unknown = affected(issues)
    resolved = [r for r in selected if key(r) not in held and r["origen_fenix"] not in unknown]
    eligible = [r for r in resolved if r["TEORICO_A"] + r["TEORICO_B"] > 0]
    zero = [r for r in resolved if r["TEORICO_A"] + r["TEORICO_B"] == 0]
    wheel = sum(r["RUEDA"] for r in eligible)
    theoretical = sum(r["TEORICO_A"] + r["TEORICO_B"] for r in eligible)
    with localcontext() as ctx:
        ctx.prec = 50
        ctx.rounding = ROUND_HALF_EVEN
        percent = (
            format((Decimal(100) * wheel / theoretical).quantize(Decimal("0.0001")), "f")
            if theoretical
            else None
        )
    dates = sorted({r["FECHA"] for r in selected})
    return {
        "observations": len(selected),
        "resolved_observations": len(resolved),
        "review_observations": len(selected) - len(resolved),
        "eligible_observations": len(eligible),
        "excluded_zero_theoretical": len(zero),
        "excluded_zero_theoretical_wheel": sum(r["RUEDA"] for r in zero),
        "eligible_wheel": wheel,
        "eligible_theoretical": theoretical,
        "rate_percent": percent,
        "rate_numerator": 100 * wheel,
        "rate_denominator": theoretical,
        "above_100_observations": sum(
            r["RUEDA"] > r["TEORICO_A"] + r["TEORICO_B"] for r in eligible
        ),
        "above_100_window": bool(theoretical and wheel > theoretical),
        "first_sample_end": dates[0] if dates else None,
        "last_sample_end": dates[-1] if dates else None,
        "sample_timestamp_role": "period_end",
        "nominal_period_minutes": 10,
    }
