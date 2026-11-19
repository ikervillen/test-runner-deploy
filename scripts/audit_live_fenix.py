"""Read-only reconciliation of live Fenix files and the two published Delta layers."""

import csv
import hashlib
import io
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pyarrow.dataset as ds

from ingesta_delta_lake import cont_zc, lista_ar, lista_defectos, lista_rech, mp9
from ingesta_delta_lake.associations import interval_decision, overlaps
from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.config import load_config
from ingesta_delta_lake.physical_assignments import outgoing
from ingesta_delta_lake.pipeline_intake import classify_name
from ingesta_delta_lake.pipeline_publications import SilverSnapshot
from ingesta_delta_lake.pipeline_rows import business_key, portable, review_hash
from ingesta_delta_lake.sources import SILVER_EXPORTS
from ingesta_delta_lake.sources import Snapshot as Capture

ROOT = Path("/pipelines/live")
STEP = timedelta(minutes=10)
ZONE = ZoneInfo("Europe/Madrid")
RELATION = "MP9_MOD_LINEA_ZF"
TABLES = {
    "LISTA_DEFECTOS": (lista_defectos, "FECHA", "mia_rejections", "periodo_fin"),
    "LISTA_RECH": (lista_rech, "FECHA", "mia_inspections", "periodo_fin"),
    "CONT_ZC": (cont_zc, "FECHA", "zc_observations", "periodo_fin"),
    "LISTA_AR": (lista_ar, "FECHA_ON", "ar_intervals", "fecha_on"),
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def relation_rows(data, encoding):
    for row in csv.reader(io.StringIO(data.decode(encoding)), delimiter=";", strict=True):
        if not row:
            continue
        if len(row) == 10 and row[-1] == "":
            row.pop()
        if len(row) != 9:
            raise ValueError("Unexpected MP9_MOD_LINEA_ZF CSV shape")
        identity, model, start, end, branch, machine, status, ok, parent = row
        start = mp9.timestamp(start)
        end = mp9.timestamp(end) if end.strip() else None
        if status not in {"1", "2"} or ok not in {"0", "1"} or (end and end < start):
            raise ValueError("Invalid MP9_MOD_LINEA_ZF status or interval")
        yield (
            int(identity),
            int(model),
            start,
            end,
            int(branch),
            int(machine),
            int(status),
            int(parent) if parent.strip() else None,
        )


def captures(source):
    config = load_config(source)
    checkpoint = ROOT / "checkpoint"
    window = read_json(checkpoint / "worker.json").get("source_window")
    if not window:
        raise RuntimeError("Live aun no ha detectado un tramo completo")
    end = datetime.fromisoformat(window[0])
    start = end - 2 * STEP
    prefixes = {date.strftime("%d%m%Y_") for date in (start, start + STEP)}
    files, relations, relation_checks = [], [], []
    with closing(
        sqlite3.connect(f"{(checkpoint / 'intake.sqlite').as_uri()}?mode=ro", uri=True)
    ) as db:
        for origin, folder in config.sources:
            candidates = []
            for path in folder.iterdir():
                if path.name[:9] not in prefixes and not path.name.upper().endswith(
                    RELATION + ".CSV"
                ):
                    continue
                candidate, _ = classify_name(origin, folder, path.name)
                if candidate:
                    candidates.append(candidate)
            recent = sorted(
                (c for c in candidates if c.family == RELATION), key=lambda c: c.producer_time or ""
            )[-2:]
            for candidate in candidates:
                path = candidate.path
                relation = candidate in recent
                timed = candidate.producer_time and "T" in candidate.producer_time
                if not relation and (not timed or not start <= candidate.clock < end):
                    continue
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue
                entry = db.execute(
                    "SELECT descriptor, captured_at, published_at, state FROM inputs "
                    "WHERE source_path=? ORDER BY rowid DESC LIMIT 1",
                    (str(path),),
                ).fetchone()
                desc = json.loads(entry[0]) if entry else {}
                bronze = (
                    desc.get("size") == stat.st_size
                    and desc.get("mtime_ns") == stat.st_mtime_ns
                    and Path(desc["bronze_path"]).is_file()
                )
                error = None
                parsed = []
                issues = 0
                try:
                    if bronze and relation:
                        if desc["size"] > config.max_file_bytes:
                            raise IntegrityError("MP9_MOD_LINEA_ZF excede el limite de captura")
                        raw = Path(desc["bronze_path"]).read_bytes()
                        if hashlib.sha256(raw).hexdigest() != desc["sha256"]:
                            raise IntegrityError("Huella distinta en Bronze")
                        parsed_relation = list(relation_rows(raw, config.encoding))
                        relations.extend((origin, *row) for row in parsed_relation)
                    if bronze and candidate.family in TABLES:
                        module, date, _, _ = TABLES[candidate.family]
                        result = module.parse([Capture(**desc)], config.encoding)
                        issues = len(result.issues)
                        for values in result.entries:
                            row = {
                                "origen_fenix": origin,
                                **dict(zip(module.FIELDS, values[1:], strict=True)),
                            }
                            parsed.append(
                                (
                                    row[date],
                                    business_key(candidate.family, row),
                                    review_hash(candidate.family, row),
                                )
                            )
                except (OSError, ValueError, csv.Error, IntegrityError) as exc:
                    error = str(exc)
                if relation:
                    relation_checks.append(
                        {
                            "Linea": origin,
                            "Archivo": path.name,
                            "Bronze": bool(bronze),
                            "Ultima": candidate == recent[-1],
                            "Error": error,
                        }
                    )
                if timed and start <= candidate.clock < end:
                    files.append(
                        {
                            "line": origin,
                            "name": path.name,
                            "family": candidate.family,
                            "block": (
                                start if candidate.clock < start + STEP else start + STEP
                            ).isoformat(),
                            "mtime": stat.st_mtime,
                            "bronze": entry[1] if bronze else None,
                            "silver": entry[2] if bronze else None,
                            "state": entry[3] if entry else "SIN_CAPTURA",
                            "error": error,
                            "issues": issues,
                            "rows": sorted(set(parsed)),
                        }
                    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "lines": sorted(dict(config.sources)),
        "files": files,
        "relations": relations,
        "relation_checks": relation_checks,
    }


def relation_index(snapshot, evidence):
    """Only reconcile explicit Fenix associations. Open overlaps remain ambiguous."""
    if "CATALOGO_PRODUCCIONES" not in snapshot.manifest.get(
        "controls", {}
    ) or "SIP_LINEA_ZF" not in snapshot.manifest.get("tables", {}):
        return {}, {}, {}, {"SIN_CATALOGO_O_TOPOLOGIA": 1}
    composition = snapshot.control("CATALOGO_PRODUCCIONES")["extra"]["composition"]
    episodes, productions = defaultdict(set), {}
    for identity, record in composition.items():
        if record.get("coverage") != "VALID":
            continue
        root = record["root"]
        productions[identity] = root
        for zf in record["zf"]:
            episodes[(zf["origen_fenix"], zf["ID_MAQUINA"], zf["ID_MODELO"], zf["FECHA_ON"])].add(
                (identity, zf["FECHA_OFF"])
            )
    topology = defaultdict(list)
    for batch in snapshot.batches("SIP_LINEA_ZF", batch_size=1024):
        for row in portable(batch.to_pylist()):
            topology[row["origen_fenix"], row["id_linea_zf_fenix"]].append(row)
    candidates = defaultdict(set)
    for origin, identity, model, start, end, branch, machine, status, parent in evidence:
        candidates[origin, identity].add((model, start, end, branch, machine, status, parent))
    registry, blocked, pending = defaultdict(list), defaultdict(set), Counter()

    def reject(reason, origin, rows):
        pending[reason] += 1
        for _, start, end, branch, _, status, _ in rows:
            if status != 2:
                continue
            mapped = {r["codigo_ramal"] for r in topology[origin, branch]}
            for branch_number in mapped or {None}:
                blocked[origin, branch_number].add((start, end))

    for (origin, identity), variants in candidates.items():
        if not any(v[5] == 2 for v in variants):
            continue
        immutable = {
            (m, a, r, machine, status, parent) for m, a, _, r, machine, status, parent in variants
        }
        ends = {v[2] for v in variants if v[2] is not None}
        if len(immutable) != 1 or len(ends) > 1:
            reject("REVISION_CONTRADICTORIA", origin, variants)
            continue
        model, start, branch, machine, _, parent = next(iter(immutable))
        end = next(iter(ends), None)
        zc = candidates.get((origin, parent), ()) if parent is not None else ()
        if not any(v[0] == model and v[3:6] == (branch, machine, 1) for v in zc):
            reject("SIN_VINCULO_ZC", origin, [(model, start, end, branch, machine, 2, parent)])
            continue
        linked = episodes.get((origin, machine, model, start), ())
        if len(linked) != 1:
            reject(
                "SIN_EPISODIO_MP9_UNICO", origin, [(model, start, end, branch, machine, 2, parent)]
            )
            continue
        production, mp9_end = next(iter(linked))
        if end and mp9_end and end != mp9_end:
            reject(
                "CIERRE_DISTINTO_EN_MP9", origin, [(model, start, end, branch, machine, 2, parent)]
            )
            continue
        end = end or mp9_end
        accepted = False
        for row in topology[origin, branch]:
            if row["id_maquina_fenix"] != machine or not overlaps(
                start, end, row["vigente_desde"], row["vigente_hasta"]
            ):
                continue
            left = max(start, row["vigente_desde"])
            bounds = [t for t in (end, row["vigente_hasta"]) if t is not None]
            registry[origin, row["codigo_ramal"]].append(
                {
                    "id": f"{origin}:{identity}:{left}",
                    "start": left,
                    "end": min(bounds) if bounds else None,
                    "production": production,
                    "machine": machine,
                    "model": model,
                }
            )
            accepted = True
        if not accepted:
            reject(
                "SIN_TOPOLOGIA_VIGENTE", origin, [(model, start, end, branch, machine, 2, parent)]
            )
    return registry, productions, blocked, dict(pending)


def delta_rows(table, field, dates, columns=None):
    return (
        table.to_pyarrow_dataset()
        .to_table(
            filter=ds.field(field).isin(sorted(dates)),
            columns=columns,
            use_threads=False,
        )
        .to_pylist()
    )


def first_presence(current, expected, dates, start):
    from app.gold.publications import Snapshot, envelope

    if not any(expected.values()):
        return {}, True
    previous, seen, first = current, {}, {}
    for _ in range(48):
        created = datetime.fromisoformat(previous.manifest["created_at"])
        refs = {r["name"]: r["delta_version"] for r in previous.manifest["tables"]}
        for family, ids in expected.items():
            _, _, table, date = TABLES[family]
            if table not in refs or not ids:
                continue
            version = (table, refs[table])
            if version not in seen:
                seen[version] = ids & {
                    r["id_observacion"]
                    for r in delta_rows(
                        previous.table(table), date, dates[family], ["id_observacion"]
                    )
                }
            for identity in seen[version]:
                first[family, identity] = created
        pointer = previous.manifest.get("previous_publication_id")
        if created <= start or not pointer:
            return first, True
        try:
            _, manifest = envelope(current.root, pointer)
            previous = Snapshot(current.root, manifest)
        except FileNotFoundError:
            break
    return first, False


def report(data):
    from app.gold.attribution import source_id
    from app.gold.evidence import Evidence
    from app.gold.publications import Snapshot

    snapshot = SilverSnapshot.open(
        SimpleNamespace(exchange=ROOT / "exchange", silver=ROOT / "silver")
    )
    current = Snapshot.open(ROOT / "gold/published")
    evidence = Evidence.open(ROOT / "gold/inputs", current.manifest["input_publication_id"])
    health_path = ROOT / "gold/checkpoint/health.json"
    health = read_json(health_path) if health_path.exists() else {}
    relation = relation_index(snapshot, data["relations"])
    files = data["files"]
    silver_rows, expected, dates = {}, defaultdict(set), {}
    for family, (_, field, _, _) in TABLES.items():
        dates[family] = {
            datetime.fromisoformat(t)
            for f in files
            if f["family"] == family
            for t, _, _ in f["rows"]
        }
        if not dates[family] or family not in snapshot.manifest["tables"]:
            continue
        for raw in delta_rows(snapshot.table(family), field, dates[family]):
            row = portable(raw)
            identity = source_id(family, row)
            silver_rows[family, business_key(family, row)] = (
                review_hash(family, row),
                identity,
                row,
            )
            expected[family].add(identity)
    published = {}
    gold_tables = {r["name"] for r in current.manifest["tables"]}
    for family, (_, _, table, field) in TABLES.items():
        if dates[family] and table in gold_tables:
            published[family] = {
                r["id_observacion"]
                for r in delta_rows(current.table(table), field, dates[family], ["id_observacion"])
            }
    limit = datetime.fromisoformat(data["start"]).replace(tzinfo=ZONE).astimezone(UTC)
    first, complete_trace = first_presence(current, expected, dates, limit)
    missing = {
        key
        for key, (_, identity, _) in silver_rows.items()
        if identity not in published.get(key[0], set())
    }
    missing_ids = {(family, silver_rows[family, key][1]) for family, key in missing}
    decisions = {}
    if missing_ids:
        ref = next(
            (r for r in evidence.document["tables"] if r["name"] == "observation_assignments"), None
        )
        if ref:
            for batch in ds.dataset(str(evidence.root / ref["uri"]), format="parquet").to_batches(
                columns=["source_family", "source_id", "status"],
                use_threads=False,
            ):
                for row in batch.to_pylist():
                    key = row["source_family"], row["source_id"]
                    if key in missing_ids:
                        decisions[key] = row["status"]
    return assemble(
        data,
        silver_rows,
        published,
        first,
        complete_trace,
        decisions,
        relation,
        health,
        current,
        evidence,
    )


def assemble(
    data, silver, published, first, complete_trace, decisions, relation, health, current, evidence
):
    registry, productions, blocked, relation_errors = relation
    current_relation = {
        item["Linea"]
        for item in data["relation_checks"]
        if item["Ultima"] and item["Bronze"] and not item["Error"]
    }
    groups = defaultdict(list)
    for file in data["files"]:
        groups[file["block"], file["line"]].append(file)
    results, details, proposed = [], [], Counter()
    start = datetime.fromisoformat(data["start"])
    for block in (start, start + STEP):
        label = f"{block:%d/%m %H:%M}-{block + STEP:%H:%M}"
        for origin in data["lines"]:
            files = groups[block.isoformat(), origin]
            f_b, b_s, s_g = [], [], []
            preexisting = 0
            keys, exact, gold = set(), set(), set()
            pending = Counter()
            for f in files:
                family = f["family"]
                if f["bronze"]:
                    f_b.append(datetime.fromisoformat(f["bronze"]).timestamp() - f["mtime"])
                else:
                    pending["SIN_BRONZE_VERSION_ACTUAL"] += 1
                if f["silver"]:
                    b_s.append(
                        (
                            datetime.fromisoformat(f["silver"])
                            - datetime.fromisoformat(f["bronze"])
                        ).total_seconds()
                    )
                elif family in SILVER_EXPORTS:
                    pending[f"SIN_RECIBO_SILVER:{f['state']}"] += 1
                if f["issues"] or f["error"]:
                    pending[f"INCIDENCIA_CSV:{family}"] += 1
                for timestamp, key, content in f["rows"]:
                    identity_key = family, key, content
                    if identity_key in keys:
                        continue
                    keys.add(identity_key)
                    current_row = silver.get((family, key))
                    if current_row is None or current_row[0] != content:
                        pending[f"SIN_FILA_EXACTA_SILVER:{family}"] += 1
                        continue
                    exact.add(identity_key)
                    _, identity, row = current_row
                    if identity not in published.get(family, set()):
                        reason = decisions.get((family, identity), "SIN_DECISION_EN_ENTRADA_GOLD")
                        pending[f"{reason}:{family}"] += 1
                    else:
                        gold.add(identity_key)
                        when = first.get((family, identity))
                        if when and f["silver"]:
                            latency = (when - datetime.fromisoformat(f["silver"])).total_seconds()
                            if latency < 0:
                                preexisting += 1
                            else:
                                s_g.append(latency)
                    if family in ("LISTA_DEFECTOS", "LISTA_RECH"):
                        begin = (datetime.fromisoformat(timestamp) - STEP).isoformat()
                        items = registry.get((origin, row["LINEA"]), [])
                        status, targets, _ = interval_decision(items, begin, timestamp)
                        if status == "MIXED_MODELS":
                            transition = outgoing(items, begin, timestamp, productions)
                            if transition:
                                status, targets, _ = transition
                        incomplete = (
                            *blocked.get((origin, row["LINEA"]), ()),
                            *blocked.get((origin, None), ()),
                        )
                        if origin not in current_relation or any(
                            overlaps(a, b, begin, timestamp) for a, b in incomplete
                        ):
                            status, targets = "EVIDENCIA_INCOMPLETA", []
                        matching = [
                            item
                            for item in items
                            if item["production"] in targets
                            and overlaps(item["start"], item["end"], begin, timestamp)
                        ]
                        machines = ",".join(map(str, sorted({i["machine"] for i in matching})))
                        models = ",".join(map(str, sorted({i["model"] for i in matching})))
                        proposed[
                            origin, row["LINEA"], status, machines, models, ",".join(targets)
                        ] += 1
            for reason, count in sorted(pending.items()):
                details.append(
                    {"Tramo": label, "Linea": origin, "Motivo": reason, "Cantidad": count}
                )
            silver_csv = [f for f in files if f["family"] in SILVER_EXPORTS]
            results.append(
                {
                    "Tramo": label,
                    "Linea": origin,
                    "Bronze": f"{sum(bool(f['bronze']) for f in files)}/{len(files)}",
                    "CSV_Silver": f"{sum(bool(f['silver']) for f in silver_csv)}/{len(silver_csv)}",
                    "Filas_Silver": f"{len(exact)}/{len(keys)}",
                    "Filas_Gold": f"{len(gold)}/{len(exact)}",
                    "Fenix_Bronze_s": round(max(f_b), 1) if f_b else None,
                    "Bronze_Silver_s": round(max(b_s), 1) if b_s else None,
                    "Silver_Gold_s": round(max(s_g), 1) if s_g else None,
                    "YaEstabanGold": preexisting,
                }
            )
    return {
        "resumen": results,
        "pendientes": details,
        "capturas_relacion": data["relation_checks"],
        "relacion": [
            {
                "Linea": o,
                "Ramal": b,
                "Decision": d,
                "Maquina": m,
                "Modelo": model,
                "Produccion": p,
                "Filas": n,
            }
            for (o, b, d, m, model, p), n in sorted(proposed.items())
        ],
        "incidencias_relacion": relation_errors,
        "ciclo_gold": health.get("status"),
        "error_gold": health.get("error"),
        "gold_utc": current.manifest["created_at"],
        "silver_usado": (evidence.document.get("sources") or [None])[0],
        "cadena_gold_completa": complete_trace,
    }


if __name__ == "__main__":
    payload = (
        captures(Path(sys.argv[2])) if sys.argv[1] == "capture" else report(globals()["INPUT"])
    )
    print(json.dumps(payload, ensure_ascii=True))
