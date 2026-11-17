from datetime import datetime, timedelta

import pytest

from app.gold.coverage import (
    Accreditation,
    accredited,
    coverage_state,
    coverage_status,
    effective_cutoff,
    floor_step,
)

T = datetime(2026, 11, 9, 12, 0)
FAMILIES = {"LISTA_RECH", "CONT_ZC"}


def row(source, until, origin="AV22", estado="READY"):
    return {"source": source, "origin": origin, "estado": estado, "covered_until": until}


def test_cutoff_falls_on_ten_minute_steps():
    assert floor_step(datetime(2026, 11, 9, 11, 59, 59)) == datetime(2026, 11, 9, 11, 50)
    assert floor_step(datetime(2026, 11, 9, 11, 50)) == datetime(2026, 11, 9, 11, 50)


def test_the_slowest_required_family_sets_the_accredited_moment():
    result = accredited(
        [row("LISTA_RECH", "2026-11-09T11:57:00"), row("CONT_ZC", datetime(2026, 11, 9, 11, 24))],
        "AV22",
        FAMILIES,
    )
    assert result == Accreditation(datetime(2026, 11, 9, 11, 20), (), True)


def test_other_origins_do_not_accredit_this_one():
    result = accredited(
        [row("LISTA_RECH", "2026-11-09T11:57:00"), row("CONT_ZC", "2026-11-09T11:57:00", "AV23")],
        "AV22",
        FAMILIES,
    )
    assert result.missing == ("CONT_ZC",) and result.reason == "COVERAGE:CONT_ZC"


def test_a_family_under_review_is_missing_even_with_a_range():
    rows = [
        row("LISTA_RECH", "2026-11-09T11:57:00"),
        row("CONT_ZC", "2026-11-09T11:57:00", estado="REVIEW"),
    ]
    assert accredited(rows, "AV22", FAMILIES).missing == ("CONT_ZC",)


def test_families_without_a_range_leave_the_ceiling_in_force():
    rows = [row("LISTA_RECH", None), row("CONT_ZC", None)]
    result = accredited(rows, "AV22", FAMILIES)
    assert result == Accreditation(None, (), False)
    assert effective_cutoff(T, result) == T


def test_effective_cutoff_is_capped_by_the_ceiling_and_never_regresses():
    data = Accreditation(datetime(2026, 11, 9, 11, 20), (), True)
    assert effective_cutoff(T, data) == datetime(2026, 11, 9, 11, 20)
    assert effective_cutoff(datetime(2026, 11, 9, 11, 10), data) == datetime(2026, 11, 9, 11, 10)
    assert effective_cutoff(T, data, previous=datetime(2026, 11, 9, 11, 40)) == datetime(
        2026, 11, 9, 11, 40
    )
    blocked = Accreditation(None, ("CONT_ZC",), False)
    assert effective_cutoff(T, blocked) is None
    assert effective_cutoff(T, blocked, previous=datetime(2026, 11, 9, 11, 40)) == datetime(
        2026, 11, 9, 11, 40
    )


@pytest.mark.parametrize(
    ("cutoff", "accreditation", "expected"),
    [
        (T, Accreditation(T, (), True), ("READY", None)),
        (
            T - timedelta(minutes=40),
            Accreditation(T - timedelta(minutes=40), (), True),
            ("WAITING_DEPENDENCY", "COVERAGE:INTERVAL"),
        ),
        (
            None,
            Accreditation(None, ("CONT_ZC",), False),
            ("WAITING_DEPENDENCY", "COVERAGE:CONT_ZC"),
        ),
        (
            T - timedelta(hours=25),
            Accreditation(None, ("CONT_ZC", "LISTA_RECH"), False),
            ("WAITING_DEPENDENCY", "COVERAGE:CONT_ZC,LISTA_RECH"),
        ),
    ],
)
def test_state_reflects_reach_and_age_of_the_gap(cutoff, accreditation, expected):
    assert coverage_state(T, T, cutoff, accreditation) == expected


def test_a_closed_production_is_ready_once_its_end_is_covered():
    end = T - timedelta(hours=3)
    assert coverage_state(T, end, end, Accreditation(end, (), True)) == ("READY", None)


def test_status_tells_consumers_how_far_and_what_is_missing():
    rows = [
        {"scope_id": "quality_scores:x", "id_produccion": "b", "origin": "AV23"},
        {
            "scope_id": "coverage:b",
            "id_produccion": "b",
            "origin": "AV23",
            "estado": "WAITING_DEPENDENCY",
            "period_end": T - timedelta(minutes=30),
            "reason_code": "COVERAGE:CONT_ZC,LISTA_AR",
        },
        {
            "scope_id": "coverage:a",
            "id_produccion": "a",
            "origin": "AV23",
            "estado": "WAITING_DEPENDENCY",
            "period_end": None,
            "reason_code": "COVERAGE:INTERVAL",
        },
    ]
    assert coverage_status(rows) == [
        {
            "id_produccion": "a",
            "origin": "AV23",
            "estado": "WAITING_DEPENDENCY",
            "calculated_until": None,
            "missing": [],
        },
        {
            "id_produccion": "b",
            "origin": "AV23",
            "estado": "WAITING_DEPENDENCY",
            "calculated_until": (T - timedelta(minutes=30)).isoformat(),
            "missing": ["CONT_ZC", "LISTA_AR"],
        },
    ]
