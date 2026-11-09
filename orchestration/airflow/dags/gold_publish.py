"""Publishes Gold every ten minutes; replaces the `watch` polling loop.

The run's moment is only the ceiling: each production is calculated as far as Silver
accredits it. When Silver has moved but no production can advance, the run waits up to
eight minutes for the missing families before publishing anyway, so the blocked state
(how far, what is missing) is recorded and alertable.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable
from gold_runner import gold_report, run_gold
from gold_tasks import GOLD_STORE_POOL, PLANT_TIMEZONE, coverage_command, publish_command

COVERAGE_POKE = timedelta(minutes=1)
COVERAGE_WAIT = timedelta(minutes=8)

with DAG(
    dag_id="gold_publish",
    description="One Gold publication per ten-minute cutoff, as far as Silver accredits",
    schedule=CronTriggerTimetable("*/10 * * * *", timezone=PLANT_TIMEZONE),
    start_date=pendulum.datetime(2026, 11, 1, tz=PLANT_TIMEZONE),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=30),
    is_paused_upon_creation=False,
    default_args={
        "owner": "data-engineering",
        "retries": 3,
        "retry_delay": timedelta(minutes=1),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=20),
    },
    tags=["gold", "calidad"],
) as dag:

    @task.sensor(
        poke_interval=COVERAGE_POKE.total_seconds(),
        timeout=COVERAGE_WAIT.total_seconds(),
        mode="reschedule",
        soft_fail=True,
        retries=0,
    )
    def coverage_ready(dag_run=None) -> bool:
        """Done unless every production is blocked; a timeout skips the wait, not the run."""
        report = gold_report(coverage_command(dag_run.run_after))
        print(f"coverage {report['status']}; blocked: {report['blocked_productions']}")
        return report["status"] != "BLOCKED"

    @task(pool=GOLD_STORE_POOL, trigger_rule="none_failed")
    def publish(dag_run=None) -> None:
        """Clearing a run republishes its own cutoff: the moment is the run's, not now."""
        run_gold(publish_command(dag_run.run_after))

    coverage_ready() >> publish()
