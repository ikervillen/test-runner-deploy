import runpy
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.domain.score_math import _aql_band_score, _dar_score, _zero_aql_score
from app.gold.common import read_json
from app.gold.metrics import AR_CATEGORIES


@pytest.fixture
def verifier():
    native = pytest.importorskip("ingesta_delta_lake")
    return runpy.run_path(
        str(Path(native.__file__).parents[2] / "scripts" / "verify_live_ar_events.py")
    )


def test_scope_requires_owner_close_and_hourly_selection(verifier):
    start = datetime(2026, 11, 27, 7)
    cutoff = start + timedelta(hours=1)
    expected = {"production": "owner", "start": start, "cutoff": cutoff}
    row = dict(
        id_produccion="owner", inicio=start, corte=cutoff, tipo_ventana="HOUR", seleccion_id="all"
    )
    exact = verifier["exact_window"]
    assert exact(row, expected)
    assert not exact(dict(row, corte=cutoff - timedelta(minutes=10)), expected)
    assert not exact(dict(row, inicio=start - timedelta(hours=1)), expected)
    assert not exact(dict(row, id_produccion="other"), expected)
    assert not exact(dict(row, seleccion_id="branch"), expected)


def test_matching_bases_and_details_do_not_prove_mathematical_score(verifier):
    config = read_json(Path(__file__).parents[3] / "config/customer_score.json")
    capacity = 600_000_000
    affected = 120_000_000
    bases = [
        dict(ambito=kind, codigo_ambito=key, tiempo_afectado_us=affected)
        for kind, key in (("CATEGORY", "Grave"), ("DAR_GROUP", "group"), ("DAR_TOTAL", "DAR"))
    ]
    aql, components = [], []
    for key in AR_CATEGORIES:
        exposure = 20.0 if key == "Grave" else 0.0
        limit = float(config["aql"]["category_limits_percent"][key])
        ratio = exposure / limit if limit > 0 else None
        points = (
            _aql_band_score(ratio)
            if ratio is not None
            else _zero_aql_score(exposure, config["aql"])
        )
        weight = float(config["weights"]["categories"][key])
        values = dict(score=round(points, 4), peso=weight, contribucion=round(points * weight, 4))
        aql.append(
            dict(
                categoria=key,
                affected_cavity_minutes=2.0 if key == "Grave" else 0.0,
                total_cavity_minutes=10.0,
                aql_percent=limit,
                exposure_percent=exposure,
                ratio=round(ratio, 4) if ratio is not None else None,
                **values,
            )
        )
        components.append(dict(componente=key, **values))
    points = _dar_score(20.0, config["dar"])
    weight = float(config["weights"]["dar"])
    dar = [
        dict(
            grupo_id="group",
            affected_minutes=2.0,
            exposure_percent=20.0,
            share_percent=100.0,
            threshold_rule_id="rule",
            score=round(points, 4),
        )
    ]
    components.append(
        dict(
            componente="DAR",
            score=round(points, 4),
            peso=weight,
            contribucion=round(points * weight, 4),
        )
    )
    by_kind, ok = verifier["reconcile"](bases, capacity, aql, dar)
    assert ok
    validate = verifier["math_matches"]
    assert validate(config, "rule", by_kind, capacity, aql, dar, components)
    dar[0]["score"] += 1
    assert verifier["reconcile"](bases, capacity, aql, dar)[1]
    assert not validate(config, "rule", by_kind, capacity, aql, dar, components)
