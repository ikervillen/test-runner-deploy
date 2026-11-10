from datetime import timedelta

import pytest
from airflow.dag_processing.dagbag import DagBag
from airflow.timetables.trigger import CronTriggerTimetable
from conftest import DAGS
from gold_tasks import GOLD_STORE_POOL


@pytest.fixture(scope="module")
def dagbag() -> DagBag:
    """Only pools that compose.airflow.yaml's airflow-init creates are accepted."""
    return DagBag(dag_folder=str(DAGS), known_pools={"default_pool", GOLD_STORE_POOL})


def test_dags_import_cleanly(dagbag):
    assert dagbag.import_errors == {}
    assert set(dagbag.dag_ids) == {
        "gold_publish",
        "gold_history",
        "gold_nightly",
        "gold_restore_drill",
    }


@pytest.mark.parametrize(
    ("dag_id", "cron"),
    [
        ("gold_publish", "*/10 * * * *"),
        ("gold_history", "0,30 * * * *"),
        ("gold_nightly", "30 2 * * *"),
        ("gold_restore_drill", "30 4 * * 0"),
    ],
)
def test_dags_run_in_plant_time_one_at_a_time(dagbag, dag_id, cron):
    dag = dagbag.dags[dag_id]
    assert isinstance(dag.timetable, CronTriggerTimetable)
    assert dag.timetable._expression == cron
    assert dag.timetable._timezone.name == "Europe/Madrid"
    assert (dag.catchup, dag.max_active_runs) == (False, 1)


def test_scoring_stays_off_the_maintenance_slot(dagbag):
    pools = {
        (dag_id, task.task_id): task.pool
        for dag_id, dag in dagbag.dags.items()
        for task in dag.tasks
    }
    assert pools == {
        ("gold_publish", "coverage_ready"): "default_pool",
        ("gold_publish", "publish"): "default_pool",
        ("gold_history", "publish_history"): "default_pool",
        ("gold_nightly", "backup"): GOLD_STORE_POOL,
        ("gold_nightly", "maintain"): GOLD_STORE_POOL,
        ("gold_restore_drill", "restore_drill"): "default_pool",
    }


def test_maintenance_only_follows_a_verified_backup(dagbag):
    maintain = dagbag.dags["gold_nightly"].get_task("maintain")
    assert maintain.upstream_task_ids == {"backup"}
    assert maintain.trigger_rule == "all_success"


def test_live_stays_short_and_history_is_one_chunk(dagbag):
    live = dagbag.dags["gold_publish"]
    history = dagbag.dags["gold_history"]
    publish = live.get_task("publish")
    backlog = history.get_task("publish_history")
    sensor = live.get_task("coverage_ready")
    assert live.dagrun_timeout == timedelta(minutes=30)
    assert publish.execution_timeout == timedelta(minutes=20)
    assert publish.retries == 0
    assert publish.pool == backlog.pool == "default_pool"
    assert backlog.execution_timeout == timedelta(minutes=45)
    assert backlog.retries == 2
    assert sensor.execution_timeout == timedelta(minutes=20)
    assert sensor.retries == 0


def test_blocked_cutoffs_wait_at_most_eight_minutes_and_then_publish_anyway(dagbag):
    dag = dagbag.dags["gold_publish"]
    sensor = dag.get_task("coverage_ready")
    publish = dag.get_task("publish")
    assert (sensor.poke_interval, sensor.timeout) == (60, 480)
    assert (sensor.mode, sensor.soft_fail, sensor.retries) == ("reschedule", True, 0)
    assert publish.upstream_task_ids == {"coverage_ready"}
    assert publish.trigger_rule == "none_failed"
