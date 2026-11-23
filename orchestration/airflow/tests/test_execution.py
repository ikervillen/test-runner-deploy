"""Runs the DAGs through Airflow's real task runner against a recording CLI stub."""

import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from airflow.dag_processing.dagbag import DagBag
from conftest import DAGS
from gold_tasks import GOLD_STORE_POOL

AIRFLOW = str(Path(sys.executable).with_name("airflow"))
SLOT = datetime(2026, 11, 6, 14, 40, tzinfo=UTC)
COVERAGE = "coverage --settings /config/settings.json --at 2026-11-06T15:40:00"
PUBLISH = "publish --settings /config/settings.json --at 2026-11-06T15:40:00 --lane live"


@pytest.fixture(scope="module")
def dags():
    subprocess.run([AIRFLOW, "db", "migrate"], check=True, capture_output=True)
    subprocess.run(
        [AIRFLOW, "pools", "set", GOLD_STORE_POOL, "1", "test"], check=True, capture_output=True
    )
    return DagBag(dag_folder=str(DAGS), known_pools={"default_pool", GOLD_STORE_POOL}).dags


@pytest.fixture
def recorded(tmp_path, monkeypatch) -> Path:
    """Stub CLI: records its arguments; `coverage` answers $GOLD_STUB_COVERAGE (READY),
    everything else exits with $GOLD_STUB_EXIT (0)."""
    record = tmp_path / "calls.txt"
    stub = tmp_path / "bin" / "calidad-gold"
    stub.parent.mkdir()
    stub.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{record}"\n'
        'if [ "$1" = coverage ]; then\n'
        '  echo "{\\"status\\": \\"${GOLD_STUB_COVERAGE:-READY}\\",'
        ' \\"blocked_productions\\": []}"\n'
        "  exit 0\n"
        "fi\n"
        'exit "${GOLD_STUB_EXIT:-0}"\n',
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{stub.parent}:{Path(sys.executable).parent}:/usr/bin:/bin")
    return record


def calls(recorded: Path) -> list[str]:
    return recorded.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    ("dag_id", "run_after", "expected"),
    [
        ("gold_publish", SLOT, [COVERAGE, PUBLISH]),
        (
            "gold_history",
            SLOT,
            ["publish --settings /config/settings.json --at 2026-11-06T15:40:00 --lane history"],
        ),
        (
            "gold_nightly",
            datetime(2026, 11, 7, 1, 30, tzinfo=UTC),
            [
                "backup --source /gold --destination /backup/20261107T013000Z-try1",
                "maintain --root /gold --keep-days 30",
            ],
        ),
        (
            "gold_restore_drill",
            datetime(2026, 11, 8, 3, 30, tzinfo=UTC),
            ["restore-drill --root /gold --scratch /backup/.restore-drill"],
        ),
    ],
)
def test_run_invokes_the_cli_with_its_own_scheduled_moment(
    dags, recorded, dag_id, run_after, expected
):
    run = dags[dag_id].test(logical_date=run_after, run_after=run_after)
    assert run.state == "success"
    assert calls(recorded) == expected


@pytest.mark.parametrize(
    ("code", "slot"),
    [
        ("65", SLOT + timedelta(minutes=10)),
        ("75", SLOT + timedelta(minutes=20)),
    ],
)
def test_live_publish_fails_once_without_retrying_in_the_same_slot(
    dags, recorded, monkeypatch, code, slot
):
    # Live gives the next scheduled slot a turn; it deliberately has retries=0.
    monkeypatch.setenv("GOLD_STUB_EXIT", code)
    run = dags["gold_publish"].test(logical_date=slot, run_after=slot)
    assert run.get_task_instance("publish").state == "failed"
    assert sum(c.startswith("publish ") for c in calls(recorded)) == 1


def test_a_fully_blocked_cutoff_still_publishes_after_the_wait(dags, recorded, monkeypatch):
    """The wait is skipped on timeout; publishing records the blocked state for alerts."""
    slot = SLOT + timedelta(minutes=30)
    sensor = dags["gold_publish"].get_task("coverage_ready")
    monkeypatch.setattr(sensor, "mode", "poke")
    monkeypatch.setattr(sensor, "poke_interval", 0)
    monkeypatch.setattr(sensor, "timeout", 0)
    monkeypatch.setenv("GOLD_STUB_COVERAGE", "BLOCKED")
    run = dags["gold_publish"].test(logical_date=slot, run_after=slot)
    assert run.get_task_instance("coverage_ready").state == "skipped"
    assert run.get_task_instance("publish").state == "success"
    assert [c.split()[0] for c in calls(recorded)] == ["coverage", "publish"]
