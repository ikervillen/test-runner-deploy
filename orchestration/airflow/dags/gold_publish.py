"""Publishes Gold every ten minutes; replaces the `watch` polling loop."""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable
from gold_runner import run_gold
from gold_tasks import GOLD_STORE_POOL, PLANT_TIMEZONE, publish_command

with DAG(
    dag_id="gold_publish",
    description="One Gold publication per ten-minute industrial cutoff",
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

    @task(pool=GOLD_STORE_POOL)
    def publish(dag_run=None) -> None:
        """Clearing a run republishes its own cutoff: the moment is the run's, not now."""
        run_gold(publish_command(dag_run.run_after))

    publish()
