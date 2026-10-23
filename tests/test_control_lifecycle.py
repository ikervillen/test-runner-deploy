import json
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.control_lifecycle import maintain
from ingesta_delta_lake.maintenance import replace_control
from ingesta_delta_lake.service import run


def test_confirmed_receipt_can_be_archived_and_replayed(config, source):
    initial = run(config)
    replace_control(config, apply=True, reformulate=True)
    with closing(Control(config)) as control:
        with control.db:
            control.db.execute(
                "UPDATE compact_receipts SET verified_at='2000-01-01T00:00:00+00:00'"
            )
        maintain(control, "none", {"status": "APPLIED"})
        raw = json.loads(control.batch(initial["batch_id"])["prepared_json"])
        assert "$receipt_archive" in raw
        assert control.prepared(control.batch(initial["batch_id"]))["$control_receipt"] == 3
    assert run(config)["status"] == "NO_CHANGE"


def test_polls_do_not_accumulate_receipts_or_attempts(config, source):
    run(config)
    replace_control(config, apply=True, reformulate=True)
    with closing(Control(config)) as control:
        count = control.db.execute("SELECT count(*) FROM attempts").fetchone()[0]
    for _ in range(5):
        assert run(config)["status"] == "NO_CHANGE"
    with closing(Control(config)) as control:
        assert control.db.execute("SELECT count(*) FROM attempts").fetchone()[0] == count
        assert control.db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
        assert control.known_captures()
        assert control.setting("last_poll")


def test_attempt_archive_keeps_running_and_pending_attempts(config, source):
    run(config)
    replace_control(config, apply=True, reformulate=True)
    with closing(Control(config)) as control:
        with control.db:
            control.db.execute(
                "UPDATE attempts SET finished_at=?",
                ((datetime.now(UTC) - timedelta(days=60)).isoformat(),),
            )
            running = control.start_attempt()
        maintain(control, "none", {"status": "APPLIED"})
        assert control.db.execute(
            "SELECT 1 FROM attempts WHERE attempt_id=?", (running,)
        ).fetchone()
        assert control.db.execute("SELECT count(*) FROM attempt_archives").fetchone()[0] == 1


def test_retention_validation_does_not_allow_zero_days(config, source, monkeypatch):
    run(config)
    replace_control(config, apply=True, reformulate=True)
    monkeypatch.setenv("INGESTA_CONTROL_RECEIPT_DAYS", "0")
    with closing(Control(config)) as control, pytest.raises(IntegrityError):
        maintain(control, "none", {"status": "APPLIED"})
