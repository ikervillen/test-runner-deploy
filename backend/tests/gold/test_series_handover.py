from datetime import datetime

import pyarrow as pa
import pytest

from app.gold.series_handover import apply_series_handover


@pytest.mark.parametrize(
    "manual,closed,expected", [(False, True, True), (False, False, False), (True, True, False)]
)
def test_series_handover_is_explicit_and_stays_in_one_tandem_half(manual, closed, expected):
    from ingesta_delta_lake.associations import interval_decision
    from ingesta_delta_lake.physical_assignments import ACTOR, outgoing

    start, cut, end = ("2026-11-19T14:" + part for part in ("00:00", "05:00", "10:00"))
    composition = {}
    for key, machine, model, began in (("a", 10, 1, start), ("b", 10, 2, cut), ("c", 9, 3, start)):
        root = {"ID_MAQUINA": machine, "ID_MODELO": model}
        composition[key] = {
            "root": root,
            "coverage": "VALID",
            "zf": [{**root, "origen_fenix": "AV32", "FECHA_ON": began, "FECHA_OFF": None}],
        }
    original = {
        "id": "old",
        "kind": "ZF_ASSOCIATION",
        "origin": "AV32",
        "branch": 1,
        "production": "a",
        "start": start,
        "end": None,
        "actor": "person" if manual else ACTOR,
    }
    unrelated = {**original, "id": "tandem", "branch": 3, "production": "c"}
    case = {"scope": ["AV32", "ZF", 1], "reason": "OVERLAP_REVIEW", "candidates": [original]}
    document = {
        "registry": {"old": original, "tandem": unrelated},
        "association_cases": [{"payload": case}],
        "bundle": {"catalog_composition": composition},
    }
    table = pa.Table.from_pylist(
        [{"origen_fenix": "AV32", "LINEA": 1, "FECHA": datetime.fromisoformat(end)}]
    )
    topology = pa.Table.from_pylist(
        [
            {
                "origen_fenix": "AV32",
                "id_linea_zf_fenix": 41,
                "id_maquina_fenix": 10,
                "codigo_ramal": 1,
                "vigente_desde": start,
                "vigente_hasta": None,
            }
        ]
    )
    evidence = [
        ("AV32", 10, 1, start, None, 41, 10, 1, None),
        ("AV32", 11, 1, start, cut if closed else None, 41, 10, 2, 10),
        ("AV32", 20, 2, cut, None, 41, 10, 1, None),
        ("AV32", 21, 2, cut, None, 41, 10, 2, 20),
    ]
    apply_series_handover(
        document,
        {"LISTA_DEFECTOS": table, "LISTA_RECH": table, "SIP_LINEA_ZF": topology},
        evidence,
        (datetime.fromisoformat(start), datetime.fromisoformat(end)),
        600,
    )
    registry = document["registry"]
    assert registry["tandem"] == unrelated
    assert bool(document["series_handover_scopes"]) is expected
    assert bool(document["association_cases"]) is not expected
    if expected:
        rows = [d for d in registry.values() if d["branch"] == 1]
        assert interval_decision(rows, cut, end)[:2] == ("ASSIGNED", ["b"])
        assert outgoing(rows, start, end, {k: v["root"] for k, v in composition.items()})[:2] == (
            "TRANSITION_TO_OUTGOING",
            ["a"],
        )
    else:
        assert registry["old"] == original
