"""Published MIA statistics for closed productions and live calculation cuts."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app.domain.score_math import _sample_sigma

from .common import digest

LIVE_SAMPLE = "live-mia-cut-v1"
LIVE_EFFECTIVE = "live-mia-effective-v1"
HISTORICAL = "closed-mia-population-v1"


def _set(identity, level, group, rule, catalog, values):
    return {
        "reference_id": identity,
        "nivel": level,
        "valor": group,
        "seleccion_id": "all",
        "reference_rule_id": rule,
        "catalog_version": catalog,
        "estado": "READY" if values else "WAITING_INPUT",
    }


def _values(identity, series_by_defect, minimum):
    values = []
    for (space, code), series in sorted(series_by_defect.items()):
        ready = len(series) >= minimum
        mean = sum(series) / len(series) if series else None
        values.append(
            {
                "reference_id": identity,
                "espacio_defecto": space,
                "codigo_defecto": code,
                "muestras": len(series),
                "media_percent": mean if ready else None,
                "sigma_percent": _sample_sigma(series, mean)
                if ready and mean is not None
                else None,
                "estado": "READY" if ready else "WAITING_INPUT",
            }
        )
    return values


def _key(row):
    return row["espacio_defecto"], row["codigo_defecto"]


def _segment(model, segments):
    row = segments.get(model, {})
    if row.get("estado_correspondencia") == "CONFIRMED":
        return row.get("segmento_calidad")
    return None


def historical_statistics(productions, segments, rates, *, catalog, minimum):
    """Publish independent MODEL and SEGMENT populations of complete, distinct runs."""
    groups = defaultdict(dict)
    for p in productions:
        identity = p["id_produccion"]
        if (
            p["estado_produccion"] != "CLOSED"
            or p["estado_dato"] != "VALID"
            or identity not in rates
        ):
            continue
        model = p["codigo_modelo_botella"]
        if model:
            groups["MODEL", model][identity] = rates[identity]
        segment = _segment(model, segments)
        if segment:
            groups["SEGMENT", segment][identity] = rates[identity]
    result = {"sets": [], "values": [], "members": []}
    for (level, group), members in sorted(groups.items()):
        identity = digest([HISTORICAL, level, group, catalog, minimum, sorted(members.items())])
        series = defaultdict(list)
        for pid, rows in sorted(members.items()):
            result["members"].append(
                {
                    "reference_id": identity,
                    "id_produccion": pid,
                    "rates_version": digest(rows),
                }
            )
            by_defect = {_key(row): row["percent"] for row in rows if row["percent"] is not None}
            for key, rate in by_defect.items():
                series[key].append(rate)
        result["sets"].append(_set(identity, level, group, HISTORICAL, catalog, series))
        result["values"].extend(_values(identity, series, minimum))
    return result


def historical_index(sets, values, *, minimum):
    groups = {
        row["reference_id"]: (row["nivel"], row["valor"])
        for row in sets
        if row["reference_rule_id"] == HISTORICAL
    }
    return {
        (*groups[row["reference_id"]], *_key(row)): row
        for row in values
        if row["reference_id"] in groups and row["estado"] == "READY" and row["muestras"] >= minimum
    }


def _sample_id(model, cutoff):
    return f"{LIVE_SAMPLE}:{cutoff.isoformat(timespec='seconds')}:{digest(model)}"


def live_statistics(
    samples, cutoff, *, previous_sets, previous_values, historical, segments, catalog
):
    """One sample per model and calculation cut. Replaying a cut replaces its measurements."""
    series, replaced = defaultdict(lambda: defaultdict(list)), defaultdict(set)
    models = {_sample_id(model, cutoff): model for model in samples}
    eligible = {}
    for row in previous_sets:
        if row["reference_rule_id"] != LIVE_SAMPLE:
            continue
        identity = row["reference_id"]
        # The ISO timestamp is the fixed-width beginning of a sample ID.
        when = datetime.fromisoformat(identity[len(LIVE_SAMPLE) + 1 :][:19])
        if when <= cutoff:
            eligible[identity] = row["valor"]
            if when == cutoff:
                models[identity] = row["valor"]
    for row in sorted(previous_values, key=lambda r: r["reference_id"]):
        identity = row["reference_id"]
        if identity not in eligible:
            continue
        model = eligible[identity]
        if identity in models:
            replaced[model].add(_key(row))
        elif row["estado"] == "READY":
            series[model][_key(row)].append(row["media_percent"])
    stored = {"sets": [], "values": [], "members": []}
    effective = {}
    for identity, model in sorted(models.items()):
        current = samples.get(model, {})
        stored["sets"].append(_set(identity, "MODEL", model, LIVE_SAMPLE, catalog, current))
        for key in sorted(current.keys() | replaced[model]):
            rate = current.get(key)
            stored["values"].extend(_values(identity, {key: [] if rate is None else [rate]}, 1))
            if rate is not None:
                series[model][key].append(rate)
        segment = _segment(model, segments)
        values = []
        for key in sorted(current):
            selected = historical.get(("MODEL", model, *key))
            if selected is None and segment:
                selected = historical.get(("SEGMENT", segment, *key))
            if selected is None:
                selected = _values(identity, {key: series[model][key]}, 1)[0]
            values.append(selected)
        effective_id = digest([LIVE_EFFECTIVE, identity, catalog, values])
        effective[model] = {
            "sets": [_set(effective_id, "MODEL", model, LIVE_EFFECTIVE, catalog, values)],
            "members": [],
            "values": [{**row, "reference_id": effective_id} for row in values],
        }
    return stored, effective
