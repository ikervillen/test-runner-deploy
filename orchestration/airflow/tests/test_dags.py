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
    assert set(dagbag.dag_ids) == {"gold_publish", "gold_nightly", "gold_restore_drill"}


@pytest.mark.parametrize(
    ("dag_id", "cron"),
    [
        ("gold_publish", "*/10 * * * *"),
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


def test_every_gold_writer_shares_the_single_slot_pool(dagbag):
    pools = {
        (dag_id, task.task_id): task.pool
        for dag_id, dag in dagbag.dags.items()
        for task in dag.tasks
    }
    assert pools == {
        ("gold_publish", "publish"): GOLD_STORE_POOL,
        ("gold_nightly", "backup"): GOLD_STORE_POOL,
        ("gold_nightly", "maintain"): GOLD_STORE_POOL,
        ("gold_restore_drill", "restore_drill"): "default_pool",
    }


def test_maintenance_only_follows_a_verified_backup(dagbag):
    maintain = dagbag.dags["gold_nightly"].get_task("maintain")
    assert maintain.upstream_task_ids == {"backup"}
    assert maintain.trigger_rule == "all_success"


def test_publication_retries_finish_before_the_next_slot_is_late(dagbag):
    task = dagbag.dags["gold_publish"].get_task("publish")
    assert task.retries == 3
    assert task.retry_exponential_backoff
    assert task.max_retry_delay <= timedelta(minutes=5)
