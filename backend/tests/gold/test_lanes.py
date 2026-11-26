from datetime import datetime, timedelta

from app.gold.lanes import canonical_hour, current_slot, plan
from app.gold.publications import chunk_delete_predicate

FAMILIES = ("LISTA_DEFECTOS", "LISTA_RECH", "LISTA_AR", "CONT_ZC")
CEILING = datetime(2026, 11, 10, 12, 20)


def _coverage(until: datetime) -> list[dict]:
    return [
        {
            "source": family,
            "origin": "AV22",
            "estado": "READY",
            "covered_until": until,
            "live_until": None if family == "LISTA_AR" else until,
        }
        for family in FAMILIES
    ]


def _production(identity: str, start: datetime, end: datetime | None = None) -> dict:
    return {
        "id_produccion": identity,
        "origen_fenix": "AV22",
        "fecha_inicio_zc": start,
        "fecha_inicio_operativo_zc": start,
        "fecha_fin_zf": end,
    }


def test_zf_owner_keeps_the_closing_hour_and_other_production_keeps_its_facts():
    six = datetime(2026, 11, 26, 6)
    handover = six + timedelta(hours=1, minutes=27, seconds=42)
    old = {
        **_production("1649", six - timedelta(days=1), handover),
        "fecha_inicio_zf": six - timedelta(days=1),
        "id_maquina_fenix": 8,
    }
    new = {
        **_production("1651", six + timedelta(minutes=10)),
        "fecha_inicio_zf": handover,
        "id_maquina_fenix": 8,
    }
    seven, eight, nine = (six + timedelta(hours=i) for i in (1, 2, 3))
    for end, scored, expected_cutoff in (
        (seven, "1649", seven),
        (eight, "1649", handover),
        (nine, "1651", nine),
    ):
        chunks = current_slot([new, old], end, 600)
        assert [(c.production_id, c.cutoff) for c in chunks if not c.facts_only] == [
            (scored, expected_cutoff)
        ]
        assert set(canonical_hour([new, old], end - timedelta(hours=1), end)[0]) == {scored}
        if end == eight:
            assert any(c.production_id == "1651" and c.facts_only for c in chunks)
    middle = dict(new, fecha_fin_zf=handover + timedelta(minutes=4))
    third = dict(new, id_produccion="1652", fecha_inicio_zf=middle["fecha_fin_zf"])
    owners, ambiguous = canonical_hour([old, middle, third], seven, eight)
    assert not owners and ambiguous
    assert all(c.facts_only for c in current_slot([old, middle, third], eight, 600))


def test_live_writes_only_the_open_hour_and_history_the_hour_before_the_slot():
    recent = _production("recent", datetime(2026, 11, 10, 6, 0))
    older = _production("older", datetime(2026, 7, 3, 6, 0))
    productions = [older, recent]
    coverage = _coverage(CEILING)

    live = plan(productions, {}, coverage, CEILING, "live")
    assert [(c.production_id, c.start, c.cutoff, c.facts_only) for c in live] == [
        ("older", datetime(2026, 11, 10, 12, 0), CEILING, True),
        ("recent", datetime(2026, 11, 10, 12, 0), CEILING, True),
    ]
    history = plan(productions, {}, coverage, CEILING, "history")
    assert [(c.production_id, c.start, c.end) for c in history] == [
        ("older", datetime(2026, 7, 3, 6, 0), datetime(2026, 7, 3, 7, 0)),
    ]
    assert plan(productions, {}, coverage, CEILING, "all") is None


def test_live_is_idle_when_every_hour_of_the_window_is_stored():
    production = _production("open", datetime(2026, 11, 10, 6, 0))
    stored = {"open": {}}
    cursor = datetime(2026, 11, 10, 6, 0)
    while cursor <= datetime(2026, 11, 10, 12, 0):
        end = cursor + timedelta(hours=1)
        stored["open"][cursor] = CEILING if cursor.hour == 12 else end
        cursor = end
    assert (
        plan(
            [production],
            stored,
            _coverage(CEILING),
            CEILING,
            "live",
            copied_until={"open": CEILING},
        )
        == []
    )
    assert plan([production], stored, _coverage(CEILING), CEILING, "history") == []


def test_live_does_not_walk_back_through_hours_already_behind_the_deployment():
    production = _production("open", datetime(2026, 11, 10, 6, 0))
    stored = {"open": {datetime(2026, 11, 10, 12, 0): CEILING}}
    assert plan([production], stored, _coverage(CEILING), CEILING, "live", since=CEILING) == []
    closed = plan(
        [production],
        {},
        _coverage(datetime(2026, 11, 10, 13, 0)),
        datetime(2026, 11, 10, 13, 0),
        "live",
        since=datetime(2026, 11, 10, 12, 0),
    )
    assert [(chunk.start, chunk.cutoff, chunk.facts_only) for chunk in closed] == [
        (datetime(2026, 11, 10, 12, 0), datetime(2026, 11, 10, 13, 0), False)
    ]


def test_an_interval_that_crosses_the_hour_belongs_to_the_chunk():
    production = _production("open", CEILING - timedelta(minutes=30))
    chunk = plan([production], {}, _coverage(CEILING), CEILING, "live")[0]
    assert chunk.overlaps(chunk.start - timedelta(minutes=30), chunk.end + timedelta(minutes=5))
    assert not chunk.overlaps(chunk.end, chunk.end + timedelta(minutes=10))


def test_a_closed_hour_reopens_only_when_a_stop_crosses_it():
    production = _production("open", datetime(2026, 11, 10, 6, 0))
    closed = datetime(2026, 11, 10, 9, 0)
    stored = {
        "open": {
            datetime(2026, 11, 10, 12, 0): CEILING,
            closed: closed + timedelta(hours=1),
        }
    }
    untouched = plan([production], stored, _coverage(CEILING), CEILING, "live")
    assert all(chunk.start != closed for chunk in untouched)
    reopened = plan(
        [production],
        stored,
        _coverage(CEILING),
        CEILING,
        "live",
        reopen={("open", closed)},
        copied_until={"open": CEILING},
    )
    assert [(chunk.start, chunk.facts_only) for chunk in reopened] == [(closed, False)]


def test_a_blocked_origin_is_not_a_chunk():
    blocked = _production("blocked", datetime(2026, 11, 1, 6, 0))
    coverage = [
        {"source": family, "origin": "AV22", "estado": "WAITING_INPUT", "covered_until": None}
        for family in FAMILIES
    ]
    assert plan([blocked], {}, coverage, CEILING, "history") == []
    assert plan([blocked], {}, coverage, CEILING, "live") == []


def test_a_hour_chunk_deletes_only_that_hour():
    hour = datetime(2026, 11, 10, 12, 0)
    hours = (("p1", hour),)
    assert chunk_delete_predicate("quality_scores", None) == ""
    assert chunk_delete_predicate("dim_date", {"p1"}, hours) is None
    assert chunk_delete_predicate("mia_rejections", {"p1"}, hours) == (
        '(t."id_produccion" IN (\'p1\') AND t."periodo_inicio" >= timestamp '
        "'2026-11-10 12:00:00' AND t.\"periodo_inicio\" < timestamp '2026-11-10 13:00:00')"
    )
    assert chunk_delete_predicate("quality_scores", {"p1"}, hours) == (
        "(t.\"id_produccion\" IN ('p1') AND t.\"inicio\" = timestamp '2026-11-10 12:00:00') "
        "AND t.\"tipo_ventana\" = 'HOUR'"
    )
    assert (
        chunk_delete_predicate("quality_score_components", {"p1"}, hours, ("result-1",))
        == "t.\"id_resultado\" IN ('result-1')"
    )


def test_the_live_anchor_is_the_slot_that_closes_next():
    from app.gold.pipeline import _live_anchor

    assert _live_anchor(datetime(2026, 11, 16, 16, 7)) == datetime(2026, 11, 16, 16, 0)
    assert _live_anchor(datetime(2026, 11, 16, 16, 10)) == datetime(2026, 11, 16, 16, 0)
