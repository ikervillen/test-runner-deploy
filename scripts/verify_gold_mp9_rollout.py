"""Collect one read-only report from the running independent Gold worker."""

import json
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pyarrow.dataset as ds
from app.gold.attribution import source_id
from app.gold.evidence import Evidence
from app.gold.publications import Snapshot

from ingesta_delta_lake.pipeline_publications import SilverSnapshot
from ingesta_delta_lake.pipeline_rows import portable

ROOT = Path("/pipelines")
PUBLISHED = ROOT / "live/gold/published"
HEALTH = ROOT / "live/gold/checkpoint/health.json"
SPILL = str(HEALTH.parent / "facts-work")
WINDOW = timedelta(minutes=10)
ZONE = ZoneInfo("Europe/Madrid")
MP9 = "MP9_CAMBIO_MODELO"
CATALOGUE = "CATALOGO_PRODUCCIONES"
ASSIGNMENTS = "ASIGNACION_PRODUCCION_RAMAL"
CLOSURES = {
    ("AV21", 1538): (1537, "2026-10-20T07:27:48"),
    ("AV22", 1303): (1302, "2026-10-21T08:33:50"),
}
FAMILIES = {"LISTA_DEFECTOS": "mia_rejections", "LISTA_RECH": "mia_inspections"}


def silver(lane):
    root = ROOT / lane
    return SilverSnapshot.open(SimpleNamespace(exchange=root / "exchange", silver=root / "silver"))


def rows(table, *, filter, columns=None):
    return (
        table.to_pyarrow_dataset()
        .to_table(filter=filter, columns=columns, use_threads=False)
        .to_pylist()
    )


def closures(live, history):
    historical = {}
    if MP9 in history.manifest["tables"]:
        historical = {
            (row["origen_fenix"], row["ID"]): portable(row["FECHA_OFF"])
            for row in rows(
                history.table(MP9),
                filter=ds.field("ID").isin([identifier for _, identifier in CLOSURES]),
                columns=["origen_fenix", "ID", "FECHA_OFF"],
            )
        }
    controls = live.manifest["controls"]
    catalogue = live.control(CATALOGUE)["extra"] if CATALOGUE in controls else {}
    association = (
        live.control(ASSIGNMENTS)["extra"].get("association") if ASSIGNMENTS in controls else None
    )
    registry = (association or {}).get("registry", {})
    plants = {
        name.split(":")[1] for name in catalogue.get("composition", {}) if name.count(":") == 3
    }
    result = {}
    for key, (root, end) in CLOSURES.items():
        origin, identifier = key
        identities = [f"fenix-production-v1:{plant}:{origin}:{root}" for plant in plants]
        composition = catalogue.get("composition", {})
        catalog_end = next(
            (
                episode["FECHA_OFF"]
                for identity in identities
                for episode in composition.get(identity, {}).get("zf", [])
                if episode["ID"] == identifier
            ),
            None,
        )
        episodes = [
            (d["branch"], d["end"])
            for d in registry.values()
            if d["kind"] == "ZF_ASSOCIATION" and d.get("production") in identities
        ]
        ok = (
            historical.get(key) == end
            and catalog_end == end
            and bool(episodes)
            and all(until is not None and until <= end for _, until in episodes)
        )
        result[f"{origin}/{identifier}"] = {
            "history": historical.get(key),
            "catalogue": catalog_end,
            "assignments": episodes,
            "ok": ok,
        }
    return result


def first_confirmed(silver_snapshot, started, observed):
    for family in FAMILIES:
        ref = silver_snapshot.manifest["tables"].get(family)
        if not ref:
            continue
        confirmed = datetime.fromisoformat(ref["confirmed_at"])
        for mark in sorted(set(ref.get("live_until", {}).values())):
            end = datetime.fromisoformat(mark)
            if end.replace(tzinfo=ZONE) < started:
                continue
            for row in rows(silver_snapshot.table(family), filter=ds.field("FECHA") == end):
                origin = row["origen_fenix"]
                identity = source_id(family, portable(row))
                observed[end, family, origin].setdefault(identity, confirmed)


def published_ids(gold, observed, found):
    created = datetime.fromisoformat(gold.manifest["created_at"])
    for family, table in FAMILIES.items():
        ends = sorted(
            {
                key[0]
                for key, items in observed.items()
                if key[1] == family and items.keys() - found.keys()
            }
        )
        if ends:
            for row in rows(
                gold.table(table),
                filter=ds.field("periodo_fin").isin(ends),
                columns=["id_observacion"],
            ):
                found.setdefault(row["id_observacion"], created)


def summarize(observed, found, now):
    result = []
    for (end, family, origin), items in sorted(observed.items()):
        deadline = (end + WINDOW).replace(tzinfo=ZONE)
        latest = max((found[i] for i in items if i in found), default=None)
        latency = max(
            ((found[i] - t).total_seconds() for i, t in items.items() if i in found), default=None
        )
        late = sum(found[i] > deadline if i in found else now > deadline for i in items)
        result.append(
            {
                "cutoff": end.isoformat(),
                "line": origin,
                "family": family,
                "gold": sum(i in found for i in items),
                "silver": len(items),
                "late": late,
                "latest_gold_utc": latest.isoformat() if latest else None,
                "max_silver_to_gold_s": latency,
            }
        )
    return result


def pending_reasons(observed, found, ends):
    missing = {
        i for key, items in observed.items() if key[0] in ends for i in items if i not in found
    }
    if not missing:
        return {}
    gold = Snapshot.open(PUBLISHED)
    evidence = Evidence.open(PUBLISHED.parent / "inputs", gold.manifest["input_publication_id"])
    ref = next(r for r in evidence.document["tables"] if r["name"] == "observation_assignments")
    dataset = ds.dataset(str(evidence.root / ref["uri"]), format="parquet")
    decisions = {}
    for batch in dataset.to_batches(columns=["source_id", "status"], use_threads=False):
        decisions.update(
            {r["source_id"]: r["status"] for r in batch.to_pylist() if r["source_id"] in missing}
        )
    return dict(Counter(decisions.get(i, "SIN_DECISION_EN_ENTRADA_GOLD") for i in missing))


def main():
    started = datetime.fromisoformat(sys.argv[1])
    pending = sys.argv[2].split(",") if len(sys.argv) > 2 and sys.argv[2] else []
    observed = defaultdict(dict)
    found, previous = {}, None
    previous_silver = previous_gold = closure_vector = None
    mp9 = {}
    ends = set()
    cutoff = time.monotonic() + 75 * 60
    while time.monotonic() < cutoff:
        try:
            now = datetime.now(UTC)
            live = silver("live")
            gold = Snapshot.open(PUBLISHED, require_gold_contract=True)
            silver_vector = [live.manifest["tables"].get(f) for f in FAMILIES]
            if silver_vector != previous_silver:
                first_confirmed(live, started, observed)
                previous_silver = silver_vector
                previous_gold = None
            if gold.publication_id != previous_gold:
                published_ids(gold, observed, found)
                previous_gold = gold.publication_id
            ended = summarize(observed, found, now)
            ends = {
                datetime.fromisoformat(row["cutoff"])
                for row in ended
                if datetime.fromisoformat(row["cutoff"]).replace(tzinfo=ZONE) + WINDOW <= now
            }
            recovered = {
                operation: json.loads((PUBLISHED / "operations" / f"{operation}.json").read_text())[
                    "state"
                ]
                for operation in pending
            }
            history = silver("history")
            revision = (
                history.manifest["tables"].get(MP9),
                live.manifest["controls"].get(CATALOGUE),
                live.manifest["controls"].get(ASSIGNMENTS),
            )
            if revision != closure_vector:
                mp9 = closures(live, history)
                closure_vector = revision
            health = json.loads(HEALTH.read_text())
            progress = (
                len(ends),
                len(found),
                all(item["ok"] for item in mp9.values()),
                health["status"],
                recovered,
                health.get("spill_directory"),
            )
            if progress != previous:
                print(
                    json.dumps(
                        {
                            "tramos_cerrados": len(ends),
                            "observaciones_gold": len(found),
                            "mp9": mp9,
                            "gold": health["status"],
                            "error": health.get("error"),
                            "recuperacion": recovered,
                            "sqlite_temporal": health.get("spill_directory"),
                        }
                    )
                )
                previous = progress
            if (
                len(ends) >= 3
                and any(end.minute == 0 for end in ends)
                and all(state == "ACKNOWLEDGED" for state in recovered.values())
                and all(item["ok"] for item in mp9.values())
                and health["status"] in {"PUBLISHED", "UNCHANGED"}
                and health.get("spill_directory") == SPILL
            ):
                break
        except FileNotFoundError as exc:
            print(f"Esperando una publicacion confirmada: {exc.filename}")
        time.sleep(20)

    rows_report = [
        r
        for r in summarize(observed, found, datetime.now(UTC))
        if datetime.fromisoformat(r["cutoff"]) in ends
    ]
    ok = (
        previous is not None
        and len(ends) >= 3
        and any(end.minute == 0 for end in ends)
        and previous[2]
        and previous[3] in {"PUBLISHED", "UNCHANGED"}
        and all(state == "ACKNOWLEDGED" for state in previous[4].values())
        and previous[5] == SPILL
        and all(r["late"] == 0 and r["silver"] == r["gold"] for r in rows_report)
    )
    reasons = pending_reasons(observed, found, ends)
    print(
        json.dumps({"completo": ok, "tramos": rows_report, "motivos_pendientes": reasons}, indent=2)
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
