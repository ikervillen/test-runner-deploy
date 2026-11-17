"""Safety net for the live hour, every ten minutes.

The run writes the open clock hour when Silver has published a new cutoff, and
exits before reading facts when that publication is already scored. The backlog
of closed hours is `gold_history`, which stops before this tick.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable
from gold_runner import gold_report, run_gold
from gold_tasks import PLANT_TIMEZONE, coverage_command, publish_command

COVERAGE_POKE = timedelta(minutes=1)
COVERAGE_WAIT = timedelta(minutes=8)
LIVE_LIMIT = timedelta(minutes=20)

with DAG(
    dag_id="gold_publish",
    description="Live Gold: the open hour, and only when Silver has moved",
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

    @task(trigger_rule="none_failed", execution_timeout=LIVE_LIMIT, retries=0)
    def publish(dag_run=None) -> None:
        """Clearing a run republishes its own cutoff: the moment is the run's, not now."""
        run_gold(publish_command(dag_run.run_after, "live"))

    coverage_ready() >> publish()
