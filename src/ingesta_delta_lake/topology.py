"""Contratos mínimos de máquina física y ramal, con vigencias explícitas."""

import csv
import hashlib
import io
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa

from .common import IntegrityError, digest, parse_int64
from .mp9 import timestamp

MACHINES = "SIP_MAQUINA_MOLDEO"
BRANCHES = "SIP_LINEA_ZF"
FAMILIES = {MACHINES, BRANCHES}
FIELDS = {
    MACHINES: (
        "id_maquina_fenix",
        "nombre_maquina",
        "vigente_desde",
        "vigente_hasta",
        "codigo_linea_zc",
    ),
    BRANCHES: (
        "id_linea_zf_fenix",
        "codigo_ramal",
        "id_maquina_fenix",
        "vigente_desde",
        "vigente_hasta",
        "origen_fenix",
    ),
}
CONTRACTS = {MACHINES: "sip_maquina_moldeo_v1", BRANCHES: "sip_linea_zf_v1"}
SCHEMAS = {
    f: pa.schema(
        [
            pa.field(
                n,
                pa.timestamp("us")
                if n.startswith("vigente_")
                else pa.string()
                if n in {"nombre_maquina", "origen_fenix"}
                else pa.int64(),
                n == "vigente_hasta",
            )
            for n in names
        ]
    )
    for f, names in FIELDS.items()
}


def key(family, row):
    return (*scope(family, row), row["vigente_desde"])


def scope(family, row):
    return (
        (row["id_maquina_fenix"],)
        if family == MACHINES
        else (row["origen_fenix"], row["id_linea_zf_fenix"])
    )


def row_hash(family, row):
    return digest([row[f] for f in FIELDS[family]])


def content_hash(family, rows):
    return digest([(k, row_hash(family, r)) for k, r in sorted(rows.items())])


def date(text):
    value = text.strip()
    return timestamp(value if " " in value else value + " 0:00:00")


def overlap(a, b):
    return (a["vigente_hasta"] is None or b["vigente_desde"] < a["vigente_hasta"]) and (
        b["vigente_hasta"] is None or a["vigente_desde"] < b["vigente_hasta"]
    )


def conflict_keys(family, rows):
    groups = defaultdict(list)
    for r in rows.values():
        groups[("id", *scope(family, r))].append(r)
        if family == BRANCHES:
            groups[("ramal", r["origen_fenix"], r["codigo_ramal"])].append(r)
    bad = set()
    for items in groups.values():
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                if overlap(a, b):
                    bad.update((key(family, a), key(family, b)))
    return bad


def affected(family, cases, row):
    for case in cases:
        p = case.get("payload", case)
        if p.get("scope") is None or digest(p["scope"]) == digest(scope(family, row)):
            return True
    return False


@dataclass
class Parsed:
    entries: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    count: int = 0


def parse(family, snapshots, encoding):
    result = Parsed()
    for snap in {(s.origin, s.sha256): s for s in snapshots}.values():
        data = Path(snap.bronze_path).read_bytes()
        if hashlib.sha256(data).hexdigest() != snap.sha256:
            raise IntegrityError("Evidencia de topología alterada")
        base = {
            "origin": snap.origin,
            "bronze_path": snap.bronze_path,
            "sha256": snap.sha256,
            "source_path": snap.source_path,
        }
        try:
            text = data.decode(encoding, errors="strict")
        except UnicodeError:
            result.issues.append({"reason": "ENCODING", "scope": None, "evidence": base})
            continue
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=";", strict=True)
        try:
            for raw in reader:
                if not raw:
                    continue
                result.count += 1
                ref = {**base, "row": reader.line_num, "raw": list(raw)}
                r = (
                    raw[:-1]
                    if raw[-1] == "" and len(raw) == (11 if family == MACHINES else 8)
                    else raw
                )
                unit = None
                try:
                    identity = parse_int64(r[0])
                    unit = (identity,) if family == MACHINES else (snap.origin, identity)
                    if len(r) != (10 if family == MACHINES else 7):
                        raise ValueError("Número de columnas de topología incompatible")
                    if family == MACHINES:
                        values = (
                            identity,
                            r[1].strip(),
                            date(r[7]),
                            date(r[8]) if r[8].strip() else None,
                            parse_int64(r[9]),
                        )
                        if not values[1]:
                            raise ValueError("Nombre de máquina vacío")
                    else:
                        values = (
                            identity,
                            parse_int64(r[1]),
                            parse_int64(r[4]),
                            date(r[5]),
                            date(r[6]) if r[6].strip() else None,
                            snap.origin,
                        )
                    row = dict(zip(FIELDS[family], values, strict=True))
                    if (
                        row["vigente_hasta"] is not None
                        and row["vigente_hasta"] <= row["vigente_desde"]
                    ):
                        raise ValueError("Vigencia vacía o invertida")
                except (ValueError, IndexError) as exc:
                    result.issues.append(
                        {
                            "reason": "INVALID_VALUE",
                            "scope": unit,
                            "detail": str(exc),
                            "evidence": ref,
                        }
                    )
                    continue
                fingerprint = row_hash(family, row)
                entry = result.entries.setdefault(fingerprint, (row, []))
                entry[1].append(ref)
        except csv.Error as exc:
            result.issues.append(
                {"reason": "CSV", "scope": None, "detail": str(exc), "evidence": base}
            )
    return result


def machine_for_period(machines, cases, machine, start, end):
    candidates = [
        r
        for r in machines.values()
        if r["id_maquina_fenix"] == machine
        and not affected(MACHINES, cases, r)
        and r["vigente_desde"] <= start
        and (r["vigente_hasta"] is None or (end is not None and end <= r["vigente_hasta"]))
    ]
    return candidates[0] if len(candidates) == 1 else None


def decide(family, parsed, old, cases, machines, machine_cases, codes):
    issues = list(parsed.issues)
    grouped = defaultdict(list)
    for row, refs in parsed.entries.values():
        grouped[key(family, row)].append((row, refs))
    proposed = dict(old)
    evidence = {}
    for k, variants in grouped.items():
        rows = [r for r, _ in variants]
        refs = [v for _, items in variants for v in items]
        compared = [*rows, *([old[k]] if k in old else [])]
        ends = {r["vigente_hasta"] for r in compared if r["vigente_hasta"] is not None}
        changed = [
            f for f in FIELDS[family] if f != "vigente_hasta" and len({r[f] for r in compared}) > 1
        ]
        if len(ends) > 1:
            changed.append("vigente_hasta")
        row = {**rows[0], "vigente_hasta": next(iter(ends), None)}
        reason = None
        if changed or affected(family, [*cases, *parsed.issues], row):
            reason = "CONFLICT" if changed else "REVIEW_REQUIRED"
        elif family == MACHINES:
            origins = {v["origin"] for v in refs}
            if any(o in codes and codes[o] != row["codigo_linea_zc"] for o in origins):
                reason = "LINE_CONFLICT"
        else:
            machine = machine_for_period(
                machines,
                machine_cases,
                row["id_maquina_fenix"],
                row["vigente_desde"],
                row["vigente_hasta"],
            )
            if machine is None:
                reason = "MACHINE_PENDING"
            elif (
                row["origen_fenix"] in codes
                and machine["codigo_linea_zc"] != codes[row["origen_fenix"]]
            ):
                reason = "LINE_CONFLICT"
        if reason:
            issues.append(
                {
                    "reason": reason,
                    "scope": scope(family, row),
                    "key": k,
                    "accepted": old.get(k),
                    "candidates": rows,
                    "changed_fields": changed,
                    "evidence": refs,
                }
            )
            continue
        proposed[k] = row
        evidence[k] = refs
    for k in conflict_keys(family, proposed):
        if k not in evidence:
            continue
        row = proposed[k]
        issues.append(
            {
                "reason": "OVERLAP",
                "scope": scope(family, row),
                "key": k,
                "accepted": old.get(k),
                "candidates": [row],
                "evidence": evidence[k],
            }
        )
        if k in old:
            proposed[k] = old[k]
        else:
            del proposed[k]
        del evidence[k]
    if conflict_keys(family, proposed):
        raise IntegrityError("Topología aceptada contiene solapamientos")
    inserts = [{**r, "evidence": evidence[k]} for k, r in proposed.items() if k not in old]
    updates = [
        {**r, "before": old[k], "evidence": evidence[k]}
        for k, r in proposed.items()
        if k in old and r != old[k]
    ]
    return inserts, updates, issues
