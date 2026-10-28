from copy import deepcopy

import pytest

from ingesta_delta_lake.ar_attribution import resolve
from ingesta_delta_lake.associations import source_id


def fixture():
    ar = {
        "origen_fenix": "AV31",
        "COD_AR": 115,
        "FECHA_ON": "2026-10-16T22:20:00",
        "FECHA_OFF": "2026-10-16T22:30:00",
        "MAQ_IS": 31,
        "CAVIDAD": "7D",
        "STATUS": 1,
        "TIPO": 1,
        "MODELO": "1358/242",
        "COLOR": "M",
        "PROCESO": "SS",
        "MOLDE": 10,
        "ID_Agrupación": 14349,
    }
    second = {**ar, "FECHA_ON": ar["FECHA_OFF"], "FECHA_OFF": "2026-10-16T22:34:00", "STATUS": 2}
    mia = {
        "origen_fenix": "AV31",
        "MOLDE": 10,
        "CAVIDAD": "7D",
        "FECHA": "2026-10-16T22:30:00",
        "INSPECC": 90,
        "RECH": 1,
    }
    bundle = {"sources": {"LISTA_AR": [ar, second], "LISTA_RECH": [mia], "LISTA_DEFECTOS": []}}
    ledger = [
        {
            "family": "LISTA_RECH",
            "source_id": "independent-mia",
            "status": "ASSIGNED",
            "productions": ["part-a"],
            "episode_ids": ["physical-branch-4"],
        }
    ]
    ledger += [
        {
            "family": "LISTA_AR",
            "source_id": source_id("LISTA_AR", row),
            "status": "AMBIGUOUS_PRODUCTION",
            "productions": [],
            "episode_ids": [],
        }
        for row in (ar, second)
    ]
    return bundle, ledger


def test_contiguous_ar_segments_share_physical_witness_without_merging_rows():
    bundle, ledger = fixture()
    original = deepcopy(bundle)
    resolve(bundle, ledger, lambda _: ["part-a", "part-b"])
    assert bundle == original and len(ledger) == 3
    assert all(e["status"] == "ASSIGNED" for e in ledger[1:])
    assert all(e["productions"] == ["part-a"] for e in ledger[1:])
    assert all(e["evidence_source_ids"] == ["independent-mia"] for e in ledger[1:])


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("22:20:00", "ASSIGNED"),
        ("22:25:00", "ASSIGNED"),
        ("22:29:59", "ASSIGNED"),
        ("22:30:00", "AMBIGUOUS_PRODUCTION"),
    ],
)
def test_instantaneous_ar_uses_containing_physical_window_without_expanding_dates(stamp, expected):
    bundle, ledger = fixture()
    row = bundle["sources"]["LISTA_AR"][0]
    row.update(FECHA_ON="2026-10-16T" + stamp, FECHA_OFF="2026-10-16T" + stamp)
    bundle["sources"]["LISTA_AR"] = [row]
    ledger = ledger[:2]
    ledger[1]["source_id"] = source_id("LISTA_AR", row)
    original = deepcopy(row)
    resolve(bundle, ledger, lambda _: ["part-a", "part-b"])
    assert ledger[1]["status"] == expected
    assert row == original and row["FECHA_ON"] == row["FECHA_OFF"]


def test_reused_group_number_does_not_bridge_a_gap_or_different_mold():
    for field, value in [
        ("FECHA_ON", "2026-10-16T22:31:00"),
        ("MOLDE", 11),
        ("ID_Agrupación", None),
    ]:
        bundle, ledger = fixture()
        bundle["sources"]["LISTA_AR"][1][field] = value
        ledger[2]["source_id"] = source_id("LISTA_AR", bundle["sources"]["LISTA_AR"][1])
        resolve(bundle, ledger, lambda _: ["part-a", "part-b"])
        assert ledger[1]["status"] == "ASSIGNED"
        assert ledger[2]["status"] == "AMBIGUOUS_PRODUCTION"


def test_conflicting_or_empty_flow_evidence_never_selects_a_production():
    for action in ("conflicting", "zero", "review", "incompatible", "boundary"):
        bundle, ledger = fixture()
        if action == "conflicting":
            bundle["sources"]["LISTA_RECH"].append(dict(bundle["sources"]["LISTA_RECH"][0]))
            ledger.insert(1, {**ledger[0], "source_id": "other-mia", "productions": ["part-b"]})
        elif action == "zero":
            bundle["sources"]["LISTA_RECH"][0].update(INSPECC=0, RECH=0)
        elif action == "review":
            ledger[0]["status"] = "EVIDENCE_CONFLICT"
        elif action == "boundary":
            bundle["sources"]["LISTA_RECH"][0]["FECHA"] = "2026-10-16T22:25:00"
        resolve(
            bundle,
            ledger,
            lambda _, action=action: (
                ["part-b"] if action == "incompatible" else ["part-a", "part-b"]
            ),
        )
        assert all(
            e["status"] == "AMBIGUOUS_PRODUCTION" for e in ledger if e["family"] == "LISTA_AR"
        )
