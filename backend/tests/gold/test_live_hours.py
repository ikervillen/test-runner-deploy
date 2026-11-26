from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.dataset as ds

from app.gold.live_hours import retry_closures
from app.gold.live_view import _project
from app.gold.metrics import Window

START = datetime(2026, 11, 24, 14)
END = START + timedelta(hours=1)
LATER = END + timedelta(minutes=10)


def prod(identity, finish=None):
    return dict(
        id_produccion=identity,
        origen_fenix="AV31",
        linea="AV31",
        id_maquina_fenix=31,
        fecha_inicio_zc=START - timedelta(hours=1),
        fecha_fin_zf=finish,
    )


def scope(identity, cutoff, reason=None):
    return dict(
        scope_id=Window(identity, "HOUR", START, END, cutoff).identity,
        id_produccion=identity,
        period_start=START,
        period_end=cutoff,
        reason_code=reason,
        estado="WAITING_DEPENDENCY" if reason else "READY",
        source="quality_scores",
    )


class FixtureSnapshot:
    publication_id = "a" * 64

    def __init__(self, tables):
        self.manifest = {"created_at": "2026-11-24T14:05:00+00:00"}
        self.tables = tables

    def rows(self, name, *, expression, **options):
        return (
            ds.dataset(pa.Table.from_pylist(self.tables[name]))
            .to_table(filter=expression)
            .to_pylist()
        )


def test_only_accredited_missing_closures_are_retried():
    early = START + timedelta(minutes=25)
    snapshot = FixtureSnapshot(
        {
            "productions": [prod("ready"), prod("waiting"), prod("outgoing", early)],
            "publication_scope_status": [
                scope("ready", END),
                scope("waiting", END, "SOURCE_COVERAGE_PENDING"),
                scope("outgoing", early, "SOURCE_COVERAGE_PENDING"),
            ],
        }
    )
    sources = ["LISTA_RECH", "LISTA_DEFECTOS", "LISTA_AR", "CONT_ZC"]
    coverage = [
        dict(
            source=name,
            origin="AV31",
            estado="READY",
            covered_from=START.isoformat(),
            covered_until=LATER.isoformat(),
        )
        for name in sources
    ]
    assert retry_closures(snapshot, coverage[:-1], LATER, 100) == []
    recovered = {item[0]: item[3] for item in retry_closures(snapshot, coverage, LATER, 100)}
    assert recovered == {"outgoing": early.isoformat(), "waiting": END.isoformat()}


def test_older_published_partial_with_coverage_reason_is_retried_once():
    snapshot = FixtureSnapshot(
        {
            "productions": [prod("partial"), prod("other")],
            "publication_scope_status": [
                dict(scope("partial", END, "MIA_SOURCE_COVERAGE_PENDING"), estado="READY"),
                scope("other", END),
            ],
        }
    )
    later = END + timedelta(hours=9)
    bounds = (START, END)
    coverage = [
        dict(source=f, origin="AV31", estado="READY")
        for f in ("LISTA_RECH", "LISTA_DEFECTOS", "LISTA_AR", "CONT_ZC")
    ]
    assert retry_closures(snapshot, coverage, later, 100, older=bounds) == [
        ["partial", START.isoformat(), END.isoformat(), END.isoformat()]
    ]
    snapshot.tables["publication_scope_status"][0]["reason_code"] = "LISTA_RECH_BASE_MISSING"
    assert not retry_closures(snapshot, coverage, later, 100, older=bounds)


def test_older_ar_waits_for_a_changed_dependency_and_retains_the_original_cut():
    original = START + timedelta(minutes=27, seconds=42)
    snapshot = FixtureSnapshot(
        {
            "productions": [prod("outgoing", original)],
            "publication_scope_status": [scope("outgoing", original, "AR_CAVITY_UNRESOLVED")],
        }
    )
    later = END + timedelta(hours=9)
    coverage = [dict(source="LISTA_AR", origin="AV31", estado="READY")]
    options = {"older": (START, END)}
    assert not retry_closures(
        snapshot, coverage, later, 100, ar_changed=lambda *_: False, **options
    )
    assert retry_closures(
        snapshot, coverage, later, 100, ar_changed=lambda *_: True, **options
    ) == [["outgoing", START.isoformat(), END.isoformat(), original.isoformat()]]


def test_open_hour_has_no_score_and_zero_survives_for_closed_result():
    current = Window("p", "HOUR", END, END + timedelta(hours=1), LATER)
    closed = Window("p", "HOUR", START, END, END)
    scores = [
        dict(
            id_resultado=w.identity,
            **w.key,
            total_penalty=value,
            normalized_score=value,
            estado_interpretacion="GOOD",
        )
        for w, value in ((closed, 0.0), (current, 123.0))
    ]
    q = SimpleNamespace(
        snapshot=FixtureSnapshot(
            {
                "quality_scores": scores,
                "quality_score_components": [
                    {"id_resultado": closed.identity, "componente": "MIA", "contribucion": 0.0},
                    {
                        "id_resultado": current.identity,
                        "componente": "Grave",
                        "contribucion": 123.0,
                    },
                ],
                "productions": [prod("p"), prod("ended", START - timedelta(minutes=5))],
                "publication_scope_status": [scope("p", END)],
            }
        ),
        max_rows=100,
        record_scan=lambda n: None,
    )
    view = _project(q, END, datetime(2026, 11, 24, 14, 10, tzinfo=UTC))
    assert len(view.hours) == 8 and view.hours[-1].state == "open"
    assert view.hours[-1].results == []
    closed = view.hours[-2].results[0]
    assert closed.total_penalty == 0.0 and closed.state == "complete"
    assert closed.contributions == {"MIA": 0.0}
    assert all(r.total_penalty != 123.0 for hour in view.hours for r in hour.results)
