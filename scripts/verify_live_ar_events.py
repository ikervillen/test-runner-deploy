"""Read the eight incident events and their original hour from the current Gold pin."""

import base64
import json
import sys
from datetime import datetime
from pathlib import Path

import pyarrow.dataset as ds
from app.gold.evidence import Evidence
from app.gold.publications import Snapshot

ROOT = Path("/pipelines/live/gold")


def inspect(root, case):
    snapshot = Snapshot.open(root / "published", require_gold_contract=True)
    evidence_id = snapshot.manifest["input_publication_id"]
    where = root / "inputs"
    if not (where / f"{evidence_id}.json").is_file():
        where = root / "published/evidence"
    evidence = Evidence.open(where, evidence_id, families=set())
    reference = evidence.document.get("silver_refs", {}).get("LISTA_AR", {})
    if not case:
        return dict(
            publication=snapshot.publication_id,
            evidence=evidence_id,
            attribution_rule=evidence.document.get("ar_attribution_rule"),
            table=reference.get("table_id"),
            version=reference.get("version"),
            reference_checks_ok=False,
            reason="REFERENCE_CASE_FILE_REQUIRED",
        )
    start, cutoff, earliest = (
        datetime.fromisoformat(case[k]) for k in ("start", "cutoff", "earliest")
    )
    examples = [tuple(event) for event in case["EXAMPLES"]]
    owners_by_model = case["OWNERS"]
    origins = sorted({key[0] for key in examples})
    if not 1 <= len(examples) <= 16 or not 0 < (cutoff - earliest).total_seconds() <= 86400:
        raise ValueError("Reference audit exceeds its scope")
    field = ds.field
    events = snapshot.rows(
        "ar_intervals",
        expression=(field("fecha_on") >= earliest)
        & (field("fecha_on") < cutoff)
        & field("origen_fenix").cast("string").isin(origins),
        file_pruning_predicate=[
            ("fecha_on", ">=", earliest),
            ("fecha_on", "<", cutoff),
            ("origen_fenix", "in", origins),
        ],
        max_rows=5000,
    )
    keyed = {
        (
            r["origen_fenix"],
            r["modelo_original"],
            r["codigo_ar_original"],
            r["cavidad"],
            r["id_agrupacion"],
            r["molde"],
            r["status_original"],
            r["tipo_original"],
            r["fecha_on"].isoformat(),
        ): r
        for r in events
    }
    matched = [keyed[key] for key in examples if key in keyed]
    owners = sorted({r["id_produccion"] for r in matched})
    summary = []
    productions = {}
    if owners:
        production = field("id_produccion").cast("string").isin(owners)
        pruning = [("id_produccion", "in", owners)]
        productions = {
            r["id_produccion"]: (r["codigo_modelo_botella"], r["id_maquina_fenix"])
            for r in snapshot.rows(
                "productions",
                expression=production,
                file_pruning_predicate=pruning,
                max_rows=5000,
            )
        }
        exact = production & (field("inicio") == start)
        period = [*pruning, ("inicio", "=", start)]
        scores = snapshot.rows(
            "quality_scores",
            expression=exact
            & (field("tipo_ventana").cast("string") == "HOUR")
            & (field("seleccion_id").cast("string") == "all"),
            file_pruning_predicate=period,
            max_rows=5000,
        )
        bases = snapshot.rows(
            "ar_window_bases",
            expression=exact,
            file_pruning_predicate=period,
            max_rows=5000,
        )
        for score in scores:
            owner, identity = score["id_produccion"], score["id_resultado"]
            by_kind = {"CATEGORY": {}, "DAR_GROUP": {}}
            for row in bases:
                if (
                    row["id_produccion"] == owner
                    and row["corte"] == score["corte"]
                    and row["seleccion_id"] == "all"
                    and row["tipo_ventana"] == "HOUR"
                    and row["ambito"] in by_kind
                ):
                    by_kind[row["ambito"]][row["codigo_ambito"]] = round(
                        row["tiempo_afectado_us"] / 60_000_000, 4
                    )
            tables = {
                name: snapshot.rows(
                    name,
                    expression=field("id_resultado").cast("string") == identity,
                    file_pruning_predicate=[("id_resultado", "=", identity)],
                    max_rows=1000,
                )
                for name in ("aql_score_details", "dar_score_details", "quality_score_components")
            }
            aql = tables["aql_score_details"]
            dar = tables["dar_score_details"]
            checks = (
                any(m > 0 for m in by_kind["CATEGORY"].values())
                and all(
                    any(
                        r["categoria"] == k and abs(r["affected_cavity_minutes"] - v) < 0.0002
                        for r in aql
                    )
                    for k, v in by_kind["CATEGORY"].items()
                )
                and all(
                    any(r["grupo_id"] == k and abs(r["affected_minutes"] - v) < 0.0002 for r in dar)
                    for k, v in by_kind["DAR_GROUP"].items()
                )
                and any(r["componente"] == "DAR" for r in tables["quality_score_components"])
            )
            summary.append(
                dict(
                    owner=owner,
                    cutoff=score["corte"],
                    bases=by_kind,
                    score=score["total_penalty"],
                    details=tables,
                    reconciled=checks,
                )
            )
    return dict(
        publication=snapshot.publication_id,
        evidence=evidence_id,
        attribution_rule=evidence.document.get("ar_attribution_rule"),
        table=reference.get("table_id"),
        version=reference.get("version"),
        matched=[
            dict(
                event=key,
                owner=keyed[key]["id_produccion"],
                catalog=productions.get(keyed[key]["id_produccion"]),
                zone=keyed[key]["zona"],
            )
            for key in examples
            if key in keyed
        ],
        not_matched=[key for key in examples if key not in keyed],
        scopes=summary,
        reference_checks_ok=(
            len(matched) == len(examples)
            and all(
                row["id_produccion"]
                == owners_by_model.get(row["modelo_original"], row["id_produccion"])
                for row in matched
            )
            and set(owners) == {row["owner"] for row in summary if row["reconciled"]}
        ),
    )


if __name__ == "__main__":
    case = json.loads(base64.b64decode(sys.argv[1])) if len(sys.argv) > 1 else None
    print(json.dumps(inspect(ROOT, case), default=str))
