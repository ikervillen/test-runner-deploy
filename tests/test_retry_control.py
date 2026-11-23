from contextlib import closing
from dataclasses import replace

from ingesta_delta_lake import retry_control
from ingesta_delta_lake.catalog_control import CatalogControl
from ingesta_delta_lake.common import DependencyPending, IntegrityError
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.maintenance import replace_control
from ingesta_delta_lake.service import run


def test_retry_budget_survives_restart_and_manual_success_resets_it(config, source):
    run(config)
    replace_control(config, apply=True, reformulate=True)
    for _ in range(6):
        with closing(Control(config)) as control:
            retry_control.failed(control, OSError("temporary"))
    with closing(Control(config)) as control:
        assert retry_control.ready(control)["status"] == "PAUSED_RETRY_EXHAUSTED"
        assert retry_control.ready(control)["retry_state"]["failures"] == 6
    assert run(config, runtime_owner=True)["status"] == "PAUSED_RETRY_EXHAUSTED"
    assert run(config)["status"] == "NO_CHANGE"
    with closing(Control(config)) as control:
        assert retry_control.ready(control) is None


def test_integrity_failure_pauses_without_automatic_retry(config, source):
    run(config)
    replace_control(config, apply=True, reformulate=True)
    with closing(Control(config)) as control:
        retry_control.failed(control, IntegrityError("contradiction"))
        assert retry_control.ready(control)["status"] == "PAUSED_RETRY_EXHAUSTED"


def test_recovers_legacy_dependency_pause_but_keeps_integrity_pause(config):
    catalog = replace(config, family="CATALOGO_PRODUCCIONES")
    catalog.control_root.mkdir(parents=True, exist_ok=True)
    with closing(CatalogControl(catalog)) as control:
        dependency = retry_control.LEGACY_DEPENDENCIES[catalog.family]
        attempt = control.start_attempt()
        retry_control.failed(control, IntegrityError(dependency))
        control.finish_attempt(attempt, {"status": "FAILED", "error": dependency})
        assert retry_control.ready(control) is None
        retry_control.failed(control, DependencyPending(dependency))
        assert control.setting("retry_state") is None

        attempt = control.start_attempt()
        retry_control.failed(control, IntegrityError("contradiction"))
        control.finish_attempt(attempt, {"status": "FAILED", "error": "contradiction"})
        assert retry_control.ready(control)["status"] == "PAUSED_RETRY_EXHAUSTED"


def test_retry_wait_is_not_a_new_attempt_and_expiry_allows_retry(config, source, monkeypatch):
    run(config)
    replace_control(config, apply=True, reformulate=True)
    with closing(Control(config)) as control:
        retry_control.failed(control, OSError("temporary"))
        waiting = retry_control.ready(control)
        assert waiting["status"] == "RETRY_WAIT"
        count = control.db.execute("SELECT count(*) FROM attempts").fetchone()[0]
    assert run(config, runtime_owner=True)["status"] == "RETRY_WAIT"
    with closing(Control(config)) as control:
        assert control.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == count
        monkeypatch.setattr(retry_control.time, "time", lambda: waiting["next_input_scan_at"] + 1)
        assert retry_control.ready(control) is None


def test_live_derived_pause_is_revalidated_once_without_resetting_the_failure(config):
    from ingesta_delta_lake.association_storage import AssociationControl

    for family, factory in (
        ("CATALOGO_PRODUCCIONES", CatalogControl),
        ("ASIGNACION_PRODUCCION_RAMAL", AssociationControl),
    ):
        old = replace(config, family=family)
        old.control_root.mkdir(parents=True, exist_ok=True)
        with closing(factory(old)) as control:
            attempt = control.start_attempt()
            error = IntegrityError("legacy dependency race")
            retry_control.failed(control, error)
            control.finish_attempt(attempt, {"status": "FAILED", "error": str(error)})
            assert retry_control.ready(control)["status"] == "PAUSED_RETRY_EXHAUSTED"

        live = replace(old, pipeline_lane="live")
        with closing(factory(live)) as control:
            assert retry_control.ready(control) is None
            assert control.db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
            retry_control.failed(control, IntegrityError("control mismatch"))
            assert retry_control.ready(control)["status"] == "PAUSED_RETRY_EXHAUSTED"
            retry_control.failed(control, DependencyPending("source publishing"))
            assert retry_control.ready(control) is None
