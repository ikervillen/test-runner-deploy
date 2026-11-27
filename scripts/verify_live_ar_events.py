"""Read-only reconciliation and independent recomputation for exact AR reference cases."""

import base64
import json
import sys
from datetime import datetime
from functools import cache
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.dataset as ds
from app.domain.score_math import _aql_band_score, _dar_score, _zero_aql_score
from app.gold.common import digest, read_json
from app.gold.evidence import Evidence
from app.gold.independent_inputs import ar_reference_controls
from app.gold.metrics import AR_CATEGORIES
from app.gold.publications import Snapshot

from ingesta_delta_lake.common import IntegrityError as SilverIntegrityError
from ingesta_delta_lake.pipeline_publications import SilverSnapshot

ROOT = Path("/pipelines/live/gold")
FIELDS = (
    "origen_fenix",
    "modelo_original",
    "codigo_ar_original",
    "cavidad",
    "id_agrupacion",
    "molde",
    "status_original",
    "tipo_original",
    "fecha_on",
    "fecha_off",
)
FIELD = ds.field


def identifier(value):
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def result_rows(snapshot, name, result, column="id_resultado"):
    return snapshot.rows(
        name,
        expression=FIELD(column).cast("string") == result,
        file_pruning_predicate=[(column, "=", result)],
        max_rows=1000,
    )


def exact_window(row, expected):
    return (
        row["id_produccion"] == expected["production"]
        and row["inicio"] == expected["start"]
        and row["corte"] == expected["cutoff"]
        and row["tipo_ventana"] == "HOUR"
        and row["seleccion_id"] == "all"
    )


def reconcile(bases, capacity, aql, dar):
    by_kind = {kind: {} for kind in ("CATEGORY", "DAR_GROUP", "DAR_TOTAL")}
    for row in bases:
        by_kind[row["ambito"]][row["codigo_ambito"]] = row["tiempo_afectado_us"]
    if capacity <= 0 or len(aql) != len(AR_CATEGORIES) or len(dar) != len(by_kind["DAR_GROUP"]):
        return by_kind, False
    aql_minutes = {row["categoria"]: row["affected_cavity_minutes"] for row in aql}
    dar_minutes = {row["grupo_id"]: row["affected_minutes"] for row in dar}
    return by_kind, (
        aql_minutes
        == {key: round(by_kind["CATEGORY"].get(key, 0) / 60_000_000, 4) for key in AR_CATEGORIES}
        and all(row["total_cavity_minutes"] == round(capacity / 60_000_000, 4) for row in aql)
        and dar_minutes
        == {key: round(value / 60_000_000, 4) for key, value in by_kind["DAR_GROUP"].items()}
    )


def math_matches(config, config_id, basis, capacity, aql, dar, components):
    by_component = {row["componente"]: row for row in components}
    if capacity <= 0 or len(by_component) != len(components):
        return False

    def value_matches(row, value, weight):
        return row and (row["score"], row["peso"], row["contribucion"]) == (
            round(value, 4),
            weight,
            round(value * weight, 4),
        )

    by_category = {row["categoria"]: row for row in aql}
    for key in AR_CATEGORIES:
        exposure = basis["CATEGORY"].get(key, 0) / capacity * 100
        limit = float(config["aql"]["category_limits_percent"][key])
        ratio = exposure / limit if limit > 0 else None
        value = (
            _aql_band_score(ratio)
            if ratio is not None
            else _zero_aql_score(exposure, config["aql"])
        )
        weight = float(config["weights"]["categories"][key])
        row = by_category.get(key)
        if not (
            value_matches(row, value, weight)
            and value_matches(by_component.get(key), value, weight)
            and row["aql_percent"] == limit
            and row["exposure_percent"] == round(exposure, 4)
            and row["ratio"] == (round(ratio, 4) if ratio is not None else None)
        ):
            return False
    amount = basis["DAR_TOTAL"].get("DAR", 0)
    value = _dar_score(amount / capacity * 100, config["dar"])
    return bool(
        value_matches(by_component.get("DAR"), value, float(config["weights"]["dar"]))
    ) and all(
        row["threshold_rule_id"] == config_id
        and row["score"]
        == round(_dar_score(basis["DAR_GROUP"][row["grupo_id"]] / capacity * 100, config["dar"]), 4)
        and row["exposure_percent"]
        == round(basis["DAR_GROUP"][row["grupo_id"]] / capacity * 100, 4)
        and row["share_percent"]
        == (round(basis["DAR_GROUP"][row["grupo_id"]] / amount * 100, 4) if amount else 0.0)
        for row in dar
    )


def original_configuration(root, expected, score):
    try:
        original = Snapshot.open(
            root / "published",
            publication_id=expected["original_publication"],
            require_gold_contract=True,
        )
        rows = result_rows(original, "quality_scores", expected["result"])
        if len(rows) != 1 or not exact_window(rows[0], expected):
            return None
        identity = rows[0]["config_id"]
        if not identifier(identity) or score["config_id"] != identity:
            return None
        config = read_json(root / "published/configs" / f"{identity}.json")
        return (
            config["score"] if digest([config["score"], config["catalogs"]]) == identity else None
        )
    except (OSError, ValueError, KeyError):
        return None


def inspect(root, case):
    snapshot = Snapshot.open(root / "published", require_gold_contract=True)

    @cache
    def pinned_source(identity):
        if not identifier(identity):
            return {}, {}, False
        where = root / "inputs"
        if not (where / f"{identity}.json").is_file():
            where = root / "published/evidence"
        try:
            doc = Evidence.open(where, identity, families=set()).document
        except (OSError, ValueError, KeyError):
            return {}, {}, False
        ref = doc.get("silver_refs", {}).get("LISTA_AR", {})
        controls = doc.get("ar_reference_controls", {})
        try:
            published = SilverSnapshot.open(
                SimpleNamespace(exchange=root.parent / "exchange"),
                publication_id=doc["sources"][0],
            )
            silver = published.manifest["tables"]["LISTA_AR"]
            valid = (
                doc.get("ar_attribution_rule") == "ar-reference-scope-v1"
                and bool(controls)
                and all(controls.values())
                and controls == ar_reference_controls(published.manifest)
                and (ref.get("table_id"), ref.get("version"))
                == (silver["table_id"], silver["version"])
                and all(
                    doc["bundle"]["cases"].get(family, []) == published.control(family)["cases"]
                    for family in controls
                )
            )
        except (OSError, ValueError, SilverIntegrityError, KeyError, IndexError):
            valid = False
        return ref, controls, valid

    evidence_id = snapshot.manifest["input_publication_id"]
    ref, controls, pin_ok = pinned_source(evidence_id)
    result = dict(
        publication=snapshot.publication_id,
        evidence=evidence_id,
        table=ref.get("table_id"),
        version=ref.get("version"),
        controls=controls,
        gold_controls_verified=pin_ok,
        reference_checks_ok=False,
    )
    if not case:
        return {**result, "reason": "REFERENCE_CASE_FILE_REQUIRED"}
    scopes = case["SCOPES"]
    examples = [tuple(row) for row in case["EXAMPLES"]]
    expected_owner = case["OWNERS"]
    if (
        len(examples) != 8
        or len(set(examples)) != 8
        or any(len(row) != len(FIELDS) for row in examples)
        or set(expected_owner) != {row[1] for row in examples}
        or len(scopes) != len(set(expected_owner.values()))
        or {row["production"] for row in scopes} != set(expected_owner.values())
    ):
        raise ValueError(
            "Exactly eight complete events and one exact scope per production are required"
        )
    seen = set()
    for row in scopes:
        if not identifier(row["result"]) or not identifier(row["original_publication"]):
            raise ValueError("Original publication and result require exact identities")
        seen.add(row["result"])
        for key in ("start", "cutoff"):
            row[key] = datetime.fromisoformat(row[key])
        if not 0 < (row["cutoff"] - row["start"]).total_seconds() <= 3600:
            raise ValueError("An expected scope must be a single hourly close")
    if len(seen) != len(scopes):
        raise ValueError("Duplicate expected result")
    earliest, latest = min(row[-2] for row in examples), max(row[-2] for row in examples)
    lo, hi = datetime.fromisoformat(earliest), datetime.fromisoformat(latest)
    if (hi - lo).total_seconds() > 86400:
        raise ValueError("Reference audit exceeds its date budget")
    origins = sorted({row[0] for row in examples})
    rows = snapshot.rows(
        "ar_intervals",
        expression=(FIELD("fecha_on") >= lo)
        & (FIELD("fecha_on") <= hi)
        & FIELD("origen_fenix").cast("string").isin(origins),
        file_pruning_predicate=[
            ("fecha_on", ">=", lo),
            ("fecha_on", "<=", hi),
            ("origen_fenix", "in", origins),
        ],
        max_rows=5000,
    )
    observed = {}
    for row in rows:
        key = tuple(row[k].isoformat() if isinstance(row[k], datetime) else row[k] for k in FIELDS)
        if key in examples:
            observed.setdefault(key, []).append(row["id_produccion"])
    events = [
        {
            "event": key,
            "owners": observed.get(key, []),
            "ok": observed.get(key) == [expected_owner[key[1]]],
        }
        for key in examples
    ]

    findings = []
    for expected in scopes:
        identity = expected["result"]
        scores = result_rows(snapshot, "quality_scores", identity)
        status = result_rows(snapshot, "publication_scope_status", identity, "scope_id")
        if not (
            len(scores) == len(status) == 1
            and exact_window(scores[0], expected)
            and status[0]["source"] == "quality_scores"
            and status[0]["id_produccion"] == expected["production"]
            and status[0]["period_start"] == expected["start"]
            and status[0]["period_end"] == expected["cutoff"]
        ):
            findings.append(dict(result=identity, reason="EXACT_HOURLY_RESULT_MISSING"))
            continue
        score = scores[0]
        owner = expected["production"]
        filters = [
            ("id_produccion", "=", owner),
            ("inicio", "=", expected["start"]),
            ("corte", "=", expected["cutoff"]),
        ]

        def at_window(table, owner=owner, expected=expected, filters=filters):
            return [
                row
                for row in snapshot.rows(
                    table,
                    expression=(FIELD("id_produccion").cast("string") == owner)
                    & (FIELD("inicio") == expected["start"])
                    & (FIELD("corte") == expected["cutoff"]),
                    file_pruning_predicate=filters,
                    max_rows=1000,
                )
                if exact_window(row, expected)
            ]

        exposure = at_window("ar_window_exposures")
        capacity = exposure[0]["tiempo_disponible_us"] if len(exposure) == 1 else 0
        bases = at_window("ar_window_bases")
        aql = result_rows(snapshot, "aql_score_details", identity)
        dar = result_rows(snapshot, "dar_score_details", identity)
        components = result_rows(snapshot, "quality_score_components", identity)
        amounts, reconciled = reconcile(bases, capacity, aql, dar)
        config = original_configuration(root, expected, score)
        used_id = status[0]["evidence_reference"]
        used_ref, used_controls, used_ok = pinned_source(used_id)
        findings.append(
            dict(
                production=owner,
                result=identity,
                start=expected["start"],
                cutoff=expected["cutoff"],
                bases=amounts,
                reconciled=reconciled,
                original_configuration_verified=config is not None,
                math_verified=reconciled
                and config is not None
                and math_matches(
                    config, score["config_id"], amounts, capacity, aql, dar, components
                ),
                evidence=used_id,
                silver_ref=used_ref,
                controls=used_controls,
                controls_verified=used_ok,
            )
        )
    return {
        **result,
        "events": events,
        "scopes": findings,
        "reference_checks_ok": pin_ok
        and all(row["ok"] for row in events)
        and all(row.get("math_verified") and row.get("controls_verified") for row in findings),
    }


if __name__ == "__main__":
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    payload = json.loads(base64.b64decode(sys.argv[1])) if len(sys.argv) > 1 else None
    print(json.dumps(inspect(ROOT, payload), default=str))
