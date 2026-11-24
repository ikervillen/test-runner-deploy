from datetime import datetime, timedelta

import pytest

from app.gold.mia_statistics import historical_index, historical_statistics, live_statistics

DEFECT = ("fenix_mia", "1")
OTHER = ("fenix_mia", "2")
CUT = datetime(2026, 11, 19, 16, 10)
SEGMENTS = {
    model: {"segmento_calidad": "wine", "estado_correspondencia": "CONFIRMED"}
    for model in ("A", "B", "C")
}


def cycle(samples, offset, settings=None, previous=None):
    previous = previous or {"sets": [], "values": []}
    return live_statistics(
        samples,
        CUT + timedelta(minutes=offset),
        previous_sets=previous["sets"],
        previous_values=previous["values"],
        historical=settings or {},
        segments=SEGMENTS,
        catalog="catalog",
    )


def test_first_sample_second_sample_and_same_cut_correction():
    stored, effective = cycle({"A": {DEFECT: 1.0}}, 0)
    first = effective["A"]["values"][0]
    assert (first["muestras"], first["media_percent"], first["sigma_percent"]) == (1, 1, 0)
    second, effective = cycle({"A": {DEFECT: 3.0}}, 10, previous=stored)
    assert effective["A"]["values"][0]["media_percent"] == 2
    assert effective["A"]["values"][0]["sigma_percent"] == pytest.approx(2**0.5)
    combined = {k: stored[k] + second[k] for k in ("sets", "values")}
    updated, effective = cycle({"A": {DEFECT: 5.0}}, 10, previous=combined)
    row = effective["A"]["values"][0]
    assert row["muestras"] == 2
    assert row["media_percent"] == 3
    assert row["sigma_percent"] == pytest.approx(8**0.5)
    assert updated["sets"][0]["reference_id"] == second["sets"][0]["reference_id"]

    removed, effective = cycle({}, 10, previous=combined)
    assert removed["values"][0]["estado"] == "WAITING_INPUT"
    assert removed["values"][0]["media_percent"] is None
    assert effective["A"]["values"] == []


def test_historical_model_then_segment_then_live_per_defect():
    productions = [
        {
            "id_produccion": f"p{i}",
            "codigo_modelo_botella": model,
            "estado_produccion": state,
            "estado_dato": "VALID",
        }
        for i, (model, state) in enumerate(
            [("A", "CLOSED"), ("A", "CLOSED"), ("B", "CLOSED"), ("C", "OPEN")],
        )
    ]
    rates = {
        "p0": [{"espacio_defecto": "fenix_mia", "codigo_defecto": "1", "percent": 1}],
        "p1": [{"espacio_defecto": "fenix_mia", "codigo_defecto": "1", "percent": 3}],
        "p2": [{"espacio_defecto": "fenix_mia", "codigo_defecto": "1", "percent": 8}],
        "p3": [{"espacio_defecto": "fenix_mia", "codigo_defecto": "1", "percent": 99}],
    }
    stats = historical_statistics(productions, SEGMENTS, rates, catalog="catalog", minimum=2)
    index = historical_index(stats["sets"], stats["values"], minimum=2)
    assert ("MODEL", "B", *DEFECT) not in index
    assert ("MODEL", "C", *DEFECT) not in index
    assert index["SEGMENT", "wine", *DEFECT]["muestras"] == 3
    _, effective = cycle({"A": {DEFECT: 9, OTHER: 1}, "B": {DEFECT: 10}}, 0, settings=index)
    a_main, a_other = effective["A"]["values"]
    assert (a_main["muestras"], a_main["media_percent"]) == (2, 2)
    assert (a_other["muestras"], a_other["media_percent"], a_other["sigma_percent"]) == (1, 1, 0)
    b_main = effective["B"]["values"][0]
    assert (b_main["muestras"], b_main["media_percent"]) == (3, 4)


def test_hourly_revision_uses_only_statistical_samples_known_at_its_cut():
    first, _ = cycle({"A": {DEFECT: 1.0}}, 0)
    exact, _ = cycle({"A": {DEFECT: 3.0}}, 10, previous=first)
    future, _ = cycle({"A": {DEFECT: 999.0}}, 20, previous=first)
    _, effective = live_statistics(
        {"A": {DEFECT: 100.0}},
        CUT + timedelta(minutes=10),
        previous_sets=first["sets"] + exact["sets"] + future["sets"],
        previous_values=first["values"] + exact["values"] + future["values"],
        historical={},
        segments=SEGMENTS,
        catalog="catalog",
        reuse_cut_sample=True,
    )
    row = effective["A"]["values"][0]
    assert (row["muestras"], row["media_percent"]) == (2, 2.0)
