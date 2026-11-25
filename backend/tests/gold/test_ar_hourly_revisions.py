from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa

from app.gold.attribution import source_id
from app.gold.catalogs import load_independent_bundle
from app.gold.common import atomic_json
from app.gold.evidence import save
from app.gold.live_hours import ar_revisions, retry_closures
from app.gold.metrics import Window

START = datetime(2026, 11, 24, 11)
HOUR = timedelta(hours=1)
FAMILIES = ("LISTA_RECH", "LISTA_DEFECTOS", "LISTA_AR", "CONT_ZC")


def _event():
    return dict(
        origen_fenix="AV21",
        COD_AR=17,
        FECHA_ON=START + timedelta(minutes=50),
        FECHA_OFF=START + timedelta(minutes=75),
        MAQ_IS=4,
        CAVIDAD="1A",
        STATUS=1,
        TIPO=1,
        MODELO="model",
        COLOR="green",
        PROCESO="production",
        MOLDE=10,
        **{"ID_Agrupación": 11},
    )


def _decision(row, production):
    return dict(
        source_family="LISTA_AR",
        source_id=source_id("LISTA_AR", row),
        status="ASSIGNED",
        productions=[production],
        zone="ZF",
    )


def test_ar_revisions_from_content_addressed_inputs(tmp_path):
    event = _event()
    old = _decision(event, "p")
    empty = save(
        tmp_path / "inputs",
        {"row_budget": 1000, "ar_pin": "earlier", "ar_categories": {"17": None}},
        {
            "LISTA_AR": pa.Table.from_pylist([event]).slice(0, 0),
            "observation_assignments": pa.Table.from_pylist([old]).slice(0, 0),
        },
        window_seconds=600,
    )
    blocked = save(
        tmp_path / "inputs",
        {"row_budget": 1000, "ar_pin": "later", "ar_categories": {"17": None}},
        {
            "LISTA_AR": pa.Table.from_pylist([event]),
            "observation_assignments": pa.Table.from_pylist([old]),
        },
        window_seconds=600,
    )
    windows = [
        Window("p", "HOUR", start, start + HOUR, start + HOUR)
        for start in (START, START + HOUR, START + 2 * HOUR)
    ]

    class Published:
        def rows(self, table, **kwargs):
            if table == "defect_catalog":
                return [{"codigo": "17", "categoria": None}]
            if table == "productions":
                return [
                    dict(
                        id_produccion="p",
                        origen_fenix="AV21",
                        fecha_inicio_zc=START,
                        fecha_inicio_zf=START,
                    )
                ]
            return [
                dict(
                    scope_id=w.identity,
                    id_produccion="p",
                    period_start=w.start,
                    period_end=w.cutoff,
                    estado="WAITING_DEPENDENCY" if i == 1 else "READY",
                    reason_code="AR_CATEGORY_UNRESOLVED" if i == 1 else None,
                    evidence_reference=empty.publication_id if i == 0 else blocked.publication_id,
                )
                for i, w in enumerate(windows)
            ]

    cutoff = START + 3 * HOUR + timedelta(minutes=10)
    coverage = [
        dict(
            source=f,
            origin="AV21",
            estado="READY",
            covered_from=START.isoformat(),
            covered_until=cutoff.isoformat(),
        )
        for f in FAMILIES
    ]
    doc = {"ar_pin": "revised", "ar_categories": {"17": "Menor"}}
    visible = Published()
    changes = ar_revisions(tmp_path, visible, doc, lambda _: ([event], [old]), cutoff, 1000)
    closed = []
    retries = retry_closures(visible, coverage, cutoff, 1000, closed=closed, ar_changed=changes)
    assert [v[1] for v in retries] == [w.start.isoformat() for w in windows[:2]]
    assert closed == [windows[2].identity]
    assert len(doc["ar_window_cache"]) == 3

    moved = _decision(event, "another-production")
    changed = ar_revisions(tmp_path, visible, doc, lambda _: ([event], [moved]), cutoff, 1000)
    assert not changed(dict(evidence_reference=empty.publication_id), windows[0])
    assert changed(dict(evidence_reference=blocked.publication_id), windows[1])


def test_official_mdl_catalog_overrides_legacy_conflicts_and_retains_other_configuration(tmp_path):
    expected = {
        "17": "Menor",
        "112": "Mayor",
        "133": "Leve",
        "221": "Mayor",
        "228": "Menor",
        "240": "Leve",
        "242": "Leve",
    }
    source = tmp_path / "catalogs.json"
    legacy = {"9999": "Mayor", **dict.fromkeys(expected)}
    atomic_json(source, {"defect_categories": legacy, "profiles": [{"id": "keep"}]})
    effective = load_independent_bundle(Path(source))
    assert len(effective["defect_categories"]) >= 229
    assert {code: effective["defect_categories"][code] for code in expected} == expected
    assert {code: effective["defect_categories"][code] for code in ("13", "39", "130", "311")} == {
        "13": "Grave",
        "39": "Mayor",
        "130": "Grave",
        "311": "Grave",
    }
    assert effective["defect_categories"]["9999"] == "Mayor"
    assert effective["profiles"] == [{"id": "keep"}]
