from contextlib import closing

from ingesta_delta_lake import retry_control
from ingesta_delta_lake.common import IntegrityError
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
