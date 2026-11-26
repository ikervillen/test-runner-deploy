"""Re-evaluate the reported AV31 scopes from the original Gold run receipts."""

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from time import perf_counter, process_time

import pyarrow.dataset as ds

from app.gold.common import IntegrityError, digest, read_json
from app.gold.evidence import Evidence
from app.gold.lanes import HourChunk
from app.gold.metrics import Window
from app.gold.pipeline import build
from app.gold.publications import Snapshot, envelope


def snapshot(root, identity):
    _, manifest = envelope(root, identity)
    return Snapshot(root.resolve(), manifest)


def receipts(root, source_ids, limit):
    candidates = defaultdict(list)
    with os.scandir(root / "runs") as files:
        for number, entry in enumerate(files, 1):
            if number > limit:
                raise IntegrityError("Run receipt lookup exceeded its explicit file budget")
            if not entry.name.endswith(".json") or not entry.is_file():
                continue
            receipt = read_json(Path(entry.path))
            identity = receipt.get("input_publication_id")
            if identity in source_ids and receipt.get("status") == "ACKNOWLEDGED":
                candidates[identity].append(receipt)
    return {
        key: sorted(items, key=lambda r: r["calculation_time"]) for key, items in candidates.items()
    }


def original_run(root, scope, candidates):
    for receipt in candidates:
        current = snapshot(root, receipt["publication_id"])
        scopes = current.rows(
            "publication_scope_status",
            expression=ds.field("scope_id").cast("string") == scope["scope_id"],
            file_pruning_predicate=[("scope_id", "=", scope["scope_id"])],
            max_rows=128,
        )
        if any(
            r["evidence_reference"] == scope["evidence_reference"]
            and r["reason_code"] == scope["reason_code"]
            and r["period_end"].isoformat()
            == datetime.fromisoformat(scope["period_end"]).isoformat()
            for r in scopes
        ):
            return receipt, current
    raise IntegrityError("No acknowledged original run certifies this scope and effective cut")


def evaluate(root, incident, candidates, budget):
    scope = incident["scope"]
    left, end, cut = map(
        datetime.fromisoformat, (incident["inicio"], incident["fin_hora"], scope["period_end"])
    )
    identity = scope["evidence_reference"]
    window = Window(incident["produccion"], "HOUR", left, end, cut)
    if window.identity != scope["scope_id"]:
        raise IntegrityError("Incident does not match the scope identity")
    receipt, confirmed = original_run(root, scope, candidates.get(identity, ()))
    config_id = receipt["config_id"]
    settings = read_json(root / "configs" / f"{config_id}.json")
    if config_id != digest([settings["score"], settings["catalogs"]]):
        raise IntegrityError("The original run's effective configuration differs")
    locations = (root.parent / "inputs", root / "evidence")
    path = next((loc for loc in locations if (loc / f"{identity}.json").is_file()), None)
    if path is None:
        raise IntegrityError("The original immutable evidence is missing")
    evidence = Evidence.open(path, identity)
    # Same-cut MIA samples already validated by the original commit are eligible.
    reference = (
        snapshot(root, receipt["reference_publication_id"])
        if receipt.get("reference_publication_id")
        else None
    )
    stats = {}
    before, cpu = perf_counter(), process_time()
    tables, _ = build(
        evidence,
        bundle=settings["catalogs"],
        config=settings["score"],
        cutoff=datetime.fromisoformat(receipt["cutoff"]),
        calculation_time=datetime.fromisoformat(receipt["calculation_time"]).replace(tzinfo=None),
        previous=confirmed,
        reference_snapshot=reference,
        lane="live",
        current_slot_seconds=600,
        mia_reference_mode="live",
        review_hour=HourChunk(window.production_id, left, end, cut),
        statistics=stats,
        max_rows_per_source=budget,
        max_total_rows=budget,
    )
    if tables is None:
        raise IntegrityError("No output for the requested window")
    try:
        status = next(
            r for r in tables["publication_scope_status"] if r["scope_id"] == window.identity
        )
        score = next(
            (r for r in tables["quality_scores"] if r["id_resultado"] == window.identity), None
        )
        parts = {
            r["componente"]: r["contribucion"]
            for r in tables["quality_score_components"]
            if r["id_resultado"] == window.identity
        }
        details = {
            name: [r for r in tables[name] if r["id_resultado"] == window.identity]
            for name in (
                "aql_score_details",
                "dar_score_details",
                "mia_score_details",
                "zc_score_details",
            )
        }
        bases = {
            name: tables[name]
            for name in (
                "ar_window_exposures",
                "ar_window_bases",
                "mia_window_exposures",
                "zc_window_bases",
            )
        }
        return {
            "original_publication": confirmed.publication_id,
            "silver": evidence.document["sources"],
            "config_id": config_id,
            "estado_nuevo": status["estado"],
            "motivos_residuales": status["reason_code"],
            "componentes": parts,
            "total_oficial": score["total_penalty"]
            if score and status["estado"] == "READY"
            else None,
            "referencia_mia": score["reference_id"] if score else None,
            "bases": bases,
            "detalle": details,
            "seconds": round(perf_counter() - before, 3),
            "cpu_seconds": round(process_time() - cpu, 3),
            "lecturas": dict(evidence.read_stats),
            "estado_anterior": scope["estado"],
            "writes_delta": 0,
        }
    finally:
        tables.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, type=Path, help="Gold published directory")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--max-receipts", type=int, default=20000)
    parser.add_argument("--max-rows", type=int, default=500000)
    args = parser.parse_args()
    incident = read_json(args.report)["cierres"]
    affected = [
        row
        for row in incident
        if row["posicion"] in {"AV31A", "AV31B"}
        and row["scope"]["reason_code"] == "AR_CAVITY_UNRESOLVED"
    ]
    if len({r["scope_id"] for r in affected}) != 16:
        raise IntegrityError("Expected 16 distinct AV31 scopes")
    candidates = receipts(
        args.gold, {r["scope"]["evidence_reference"] for r in affected}, args.max_receipts
    )
    for row in sorted(affected, key=lambda r: (r["inicio"], r["posicion"])):
        try:
            result = evaluate(args.gold, row, candidates, args.max_rows)
        except (OSError, ValueError, KeyError, StopIteration) as exc:
            result = {"estado_nuevo": "NO_VERIFICADO", "error": f"{type(exc).__name__}: {exc}"}
        print(
            json.dumps(
                {
                    "scope_id": row["scope_id"],
                    "position": row["posicion"],
                    "production": row["produccion"],
                    "cutoff": row["corte_esperado"],
                    "evidence": row["scope"]["evidence_reference"],
                    **result,
                },
                default=str,
            )
        )


if __name__ == "__main__":
    main()
