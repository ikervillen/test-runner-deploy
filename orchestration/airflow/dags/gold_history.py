"""Fills hours behind the live slot, oldest first, and stops before the next tick.

Each wake writes consecutive hours until twenty seconds remain before the
ten-minute live tick, or until the checkpoint has no older hole. One hour is
committed before the next starts. The live tick holds the disk; this task
does not run beside it.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable
from gold_runner import run_gold
from gold_tasks import PLANT_TIMEZONE, publish_command

CHUNK_LIMIT = timedelta(minutes=8)

with DAG(
    dag_id="gold_history",
    description="Gold backlog: oldest missing hour forward, stopping before the live tick",
    schedule=CronTriggerTimetable("3-59/10 * * * *", timezone=PLANT_TIMEZONE),
    start_date=pendulum.datetime(2026, 11, 1, tz=PLANT_TIMEZONE),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=9),
    is_paused_upon_creation=False,
    default_args={
        "owner": "data-engineering",
        "retries": 2,
        "retry_delay": timedelta(minutes=1),
        "execution_timeout": CHUNK_LIMIT,
    },
    tags=["gold", "calidad"],
) as dag:

    @task(retries=2, execution_timeout=CHUNK_LIMIT)
    def publish_history(dag_run=None) -> None:
        """Hours behind the live slot. The CLI stops before the next ten-minute tick."""
        run_gold(publish_command(dag_run.run_after, "history"))

    publish_history()
