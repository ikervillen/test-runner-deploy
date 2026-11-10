"""Fills one closed hour that has already left the eight-hour window.

Each run writes that single hour, the newest missing one of the newest
production, and stops. If the hour fails it is not stored, so the next run
repeats it and nothing else. An empty backlog exits. This DAG does not share
the maintenance slot with the live lane: both may score at once, and only the
commit waits.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable
from gold_runner import run_gold
from gold_tasks import PLANT_TIMEZONE, publish_command

CHUNK_LIMIT = timedelta(minutes=45)

with DAG(
    dag_id="gold_history",
    description="Gold backlog: one closed hour outside the eight-hour window",
    schedule=CronTriggerTimetable("0,30 * * * *", timezone=PLANT_TIMEZONE),
    start_date=pendulum.datetime(2026, 11, 1, tz=PLANT_TIMEZONE),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=50),
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
        """One hour. The next run continues with whatever is still outside the window."""
        run_gold(publish_command(dag_run.run_after, "history"))

    publish_history()
