import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.gold.boundaries import derive
from app.gold.common import IntegrityError
from app.gold.metrics import Window, ar_bases, live_windows, mia_bases, zc_bases
from app.gold.references import population
from app.gold.schemas import make_table
from app.gold.scoring import calculate

START = datetime(2026, 10, 14, 10)
END = START + timedelta(hours=1)


def window():
    return Window("p", "HOUR", START, END, END)


def inspection(machine, value, minute=10, branch=1):
    return {
        "id_produccion": "p",
        "linea": "AV22",
        "ramal": branch,
        "codigo_maquina_inspeccion": machine,
        "periodo_inicio": START + timedelta(minutes=minute - 10),
        "periodo_fin": START + timedelta(minutes=minute),
        "inspeccionadas": value,
    }


def test_maximum_is_before_sum_and_after_machine_filter():
    rows = [inspection(1, 96), inspection(2, 89), inspection(1, 96, 20), inspection(1, 15, 10, 2)]
    assert mia_bases([], rows, window())["exposure"]["inspeccionadas"] == 207
    assert (
        mia_bases([], rows, window(), selection={"machine_codes": [2]})["exposure"][
            "inspeccionadas"
        ]
        == 89
    )


def test_defect_filter_never_filters_the_inspection_source():
    row = {
        **inspection(1, 96),
        "rechazos": 4,
        "espacio_defecto": "fenix_mia",
        "codigo_defecto": "1",
    }
    value = mia_bases([row], [inspection(1, 96)], window(), defect_codes={"other"})
    assert value["rates"] == []
    assert value["exposure"]["inspeccionadas"] == 96


def capacity():
    return [
        {
            "id_produccion": "p",
            "estado_capacidad": "CONFIRMED",
            "vigente_desde": START,
            "vigente_hasta": None,
            "cavidades_asignadas": 24,
        }
    ]


def event(start, end):
    return {
        "id_produccion": "p",
        "fecha_on": start,
        "fecha_off": end,
        "cavidad": "1A",
        "categoria": "Grave",
        "grupo_dar": "ovalizacion",
    }


def test_open_ar_contributes_45_minutes_without_fabricating_a_close():
    row = event(START + timedelta(minutes=15), None)
    value = ar_bases([row], capacity(), window())
    assert row["fecha_off"] is None
    assert value["exposure"]["tiempo_disponible_us"] == 1440 * 60_000_000
    assert (
        next(r for r in value["amounts"] if r["ambito"] == "CATEGORY")["tiempo_afectado_us"]
        == 45 * 60_000_000
    )


def test_overlap_is_union_and_closure_recalculates_without_extra_event():
    rows = [
        event(START, START + timedelta(minutes=30)),
        event(START + timedelta(minutes=20), START + timedelta(minutes=50)),
    ]
    value = ar_bases(rows, capacity(), window())
    assert (
        next(r for r in value["amounts"] if r["ambito"] == "CATEGORY")["tiempo_afectado_us"]
        == 50 * 60_000_000
    )
    assert len(rows) == 2


@pytest.mark.parametrize("origin", ["AV31", "AV32"])
def test_detached_moulds_count_by_episode_and_keep_the_capacity(origin):
    production = {"planta": "AIALA", "origen_fenix": origin, "codigo_modelo_botella": "2939/577"}
    detached = {
        **event(START, None),
        "origen_fenix": origin,
        "zona": "ZF",
        "codigo_defecto": "331",
        "cavidad": "99Z",
    }
    rows = [{**detached, "molde": m} for m in (3, 11, 20)]
    rows[-1] = {**rows[-1], "codigo_defecto": "255", "grupo_dar": None}
    rows.append({**rows[0], "fecha_on": START + timedelta(minutes=15), "grupo_dar": "other"})
    result = ar_bases(rows, capacity(), window(), production=production)
    minutes = {
        (r["ambito"], r["codigo_ambito"]): r["tiempo_afectado_us"] / 60_000_000
        for r in result["amounts"]
    }
    assert result["exposure"]["tiempo_disponible_us"] == 1440 * 60_000_000
    assert minutes == {
        ("CATEGORY", "Grave"): 180,
        ("DAR_GROUP", "ovalizacion"): 120,
        ("DAR_GROUP", "other"): 45,
        ("DAR_TOTAL", "DAR"): 120,
    }
    assert (
        ar_bases(rows[:2], capacity(), window(), production=production)["amounts"][0][
            "tiempo_afectado_us"
        ]
        == 120 * 60_000_000
    )


@pytest.mark.parametrize(
    ("cavity", "reason"),
    [
        (None, "AR_CAVITY_UNRESOLVED"),
        ("", "AR_CAVITY_UNRESOLVED"),
        ("-", "AR_CAVITY_UNRESOLVED"),
        ("99Z", "AR_MOULD_IDENTITY_PENDING"),
    ],
)
def test_missing_ar_identity_remains_pending(cavity, reason):
    with pytest.raises(IntegrityError) as error:
        ar_bases([{**event(START, None), "cavidad": cavity}], capacity(), window())
    assert error.value.code == reason


def test_accredited_cavity_change_and_conflicting_tandem_assignment():
    from app.gold.ar_units import conflicts, record_episode

    production = {"planta": "AIALA", "origen_fenix": "AV31", "codigo_modelo_botella": "2939/577"}
    detached = {
        **event(START, START + timedelta(minutes=30)),
        "origen_fenix": "AV31",
        "zona": "ZF",
        "codigo_defecto": "331",
        "cavidad": "99Z",
        "molde": 3,
    }
    physical = {**detached, "cavidad": "1A", "fecha_off": START + timedelta(minutes=45)}
    result = ar_bases([detached, physical], capacity(), window(), production=production)
    assert result["amounts"][0]["tiempo_afectado_us"] == 45 * 60_000_000
    other = {**detached, "id_produccion": "q"}
    episodes = {}
    record_episode(episodes, detached, production)
    record_episode(episodes, other, {**production, "codigo_modelo_botella": "1358/242"})
    assert not conflicts(episodes)
    record_episode(episodes, other, production)
    with pytest.raises(IntegrityError) as error:
        ar_bases(
            [detached], capacity(), window(), production=production, conflicts=conflicts(episodes)
        )
    assert error.value.code == "AR_MOULD_ATTRIBUTION_PENDING"


def test_capacity_gap_is_pending_not_inferred_from_mia():
    rows = capacity()
    rows[0]["vigente_desde"] = START + timedelta(minutes=1)
    with pytest.raises(IntegrityError, match="gap"):
        ar_bases([], rows, window())


def test_shared_wheel_counts_once_even_if_join_repeats_the_measurement():
    row = {
        "id_observacion": "physical",
        "origen_fenix": "AV32",
        "id_maquina_contador": 32,
        "periodo_inicio": START,
        "periodo_fin": START + timedelta(minutes=10),
        "rueda": 90,
        "teorico_total": 100,
    }
    result = zc_bases([row, row], window(), {"physical"})
    assert result[0]["rueda"] == 90
    assert result[0]["teoricos"] == 100
    assert result[0]["muestras"] == 1


def test_same_model_history_wins_even_if_it_has_fewer_than_three_samples():
    target = {"id_produccion": "p", "codigo_modelo_botella": "1000/100"}
    candidates = [
        {
            "id_produccion": "q",
            "codigo_modelo_botella": "1000/100",
            "estado_produccion": "CLOSED",
            "estado_dato": "VALID",
        },
        {
            "id_produccion": "r",
            "codigo_modelo_botella": "2000/200",
            "estado_produccion": "CLOSED",
            "estado_dato": "VALID",
        },
    ]
    result = population(target, candidates, {})
    assert result[0] == "MODEL"
    assert [p["id_produccion"] for p in result[2]] == ["q"]


def test_live_point_is_partial_and_summary_identity_does_not_change_with_cutoff():
    values = live_windows("p", datetime(2026, 10, 14, 12, 35))
    assert len(values) == 8
    assert values[-1].start == datetime(2026, 10, 14, 11)
    assert values[-1].cutoff == datetime(2026, 10, 14, 11, 35)
    a = Window("p", "PRODUCTION", START, END, END)
    b = Window("p", "PRODUCTION", START, END + timedelta(hours=1), END + timedelta(hours=1))
    assert a.identity == b.identity


def test_all_components_publish_together_and_missing_profile_publishes_nothing():
    config = json.loads(
        (Path(__file__).resolve().parents[3] / "config/customer_score.json").read_text()
    )
    m = mia_bases([], [inspection(1, 100)], window())
    a = ar_bases([], capacity(), window())
    z = [{"rueda": 90, "teoricos": 100, "muestras": 1, "id_base_zc": "wheel"}]
    ref = {"values": [], "sets": [{"reference_id": "reference"}]}
    args = dict(
        mia=m,
        ar=a,
        zc=z,
        reference=ref,
        config=config,
        config_id="c",
        coverage_ready=True,
        calculation_time=END,
    )
    result = calculate(window(), profile_confirmed=True, **args)
    assert result.pending == ()
    assert result.tables["quality_scores"][0]["total_penalty"] == 5.0
    for name, rows in result.tables.items():
        make_table(name, rows)
    waiting = calculate(window(), profile_confirmed=False, **args)
    assert waiting.tables == {}
    assert "MODEL_SEGMENT_DAR_PROFILE_PENDING" in waiting.pending


def test_physical_override_requires_independent_evidence_and_rejects_overlap():
    old = {
        "id_produccion": "old",
        "origen_fenix": "AV32",
        "ID_MAQUINA": 11,
        "fecha_inicio_zc": datetime(2026, 10, 1, 6),
        "fecha_fin_zf": None,
    }
    new = {**old, "id_produccion": "new", "fecha_inicio_zc": datetime(2026, 10, 14, 6, 23)}
    hot = [
        {
            "origen_fenix": "AV32",
            "id": 1,
            "id_maquina_fenix": 11,
            "fecha": datetime(2026, 10, 14, 6, 50),
            "seccion": 12,
            "cavidad": "F",
            "molde": 35,
        }
    ]
    link = {
        "origen_fenix": "AV32",
        "id_maquina_fenix": 11,
        "molde": 35,
        "cavidad": "12F",
        "id_produccion": "old",
        "vigente_desde": datetime(2026, 10, 1),
        "vigente_hasta": None,
    }
    values, pending = derive([old, new], hot, [link])
    assert not pending
    assert next(r for r in values if r["id_produccion"] == "new")["start"] == datetime(
        2026, 10, 14, 6, 50
    )
    approximate, _ = derive([old, new], hot, [])
    assert (
        next(r for r in approximate if r["id_produccion"] == "new")["physical_result"]
        == "INCONCLUSIVE"
    )
    _, pending = derive([old, new], hot, [link, {**link, "id_produccion": "new"}])
    assert pending


def test_capacity_late_close_updates_revision_but_not_identity():
    from app.gold.context import capacity_revisions

    old = {"id_capacidad": "same", "vigente_hasta": None, "revision": 1}
    closed = {"id_capacidad": "same", "vigente_hasta": END, "revision": 1}
    result = capacity_revisions([closed], [old])[0]
    assert result["id_capacidad"] == old["id_capacidad"]
    assert result["revision"] == 2
    assert capacity_revisions([{**result, "revision": 1}], [result])[0]["revision"] == 2


def test_ar_missing_category_is_explained_and_top_code_is_preserved():
    from app.gold.common import PendingBasis

    row = {**event(START, END), "codigo_defecto": "53"}
    assert ar_bases([row], capacity(), window())["top_defects"]["Grave"] == "53"
    row["categoria"] = None
    with pytest.raises(PendingBasis) as error:
        ar_bases([row], capacity(), window())
    assert error.value.code == "AR_CATEGORY_UNRESOLVED"


def test_live_mia_without_denominator_publishes_other_evaluated_components():
    from app.gold.contracts import validate
    from app.gold.scoring import LIVE_PARTIAL_MIA_FORMULA

    config = json.loads(
        (Path(__file__).resolve().parents[3] / "config/customer_score.json").read_text()
    )
    result = calculate(
        window(),
        mia=mia_bases([], [], window()),
        ar=ar_bases([], capacity(), window()),
        zc=[{"rueda": 90, "teoricos": 100, "muestras": 1, "id_base_zc": "wheel"}],
        reference={"values": [], "sets": []},
        config=config,
        config_id="c",
        profile_confirmed=True,
        coverage_ready=True,
        calculation_time=END,
        live_mia=True,
    )

    score = result.tables["quality_scores"][0]
    components = result.tables["quality_score_components"]
    assert result.pending == ("LISTA_RECH_BASE_MISSING",)
    assert score["formula_version"] == LIVE_PARTIAL_MIA_FORMULA
    assert score["reference_id"] is None
    assert score["evaluated_weight"] == sum(row["peso"] for row in components)
    assert len(components) == 7 and all(row["componente"] != "MIA" for row in components)
    validate(result.tables)
