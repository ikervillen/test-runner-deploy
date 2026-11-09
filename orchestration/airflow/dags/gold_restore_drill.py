"""Weekly proof that the latest backup restores and reads under the Gold contract.

Outside the gold_store pool: it never writes Gold tables, so publishing continues.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable
from gold_runner import run_gold
from gold_tasks import PLANT_TIMEZONE, restore_drill_command

with DAG(
    dag_id="gold_restore_drill",
    description="Restore the latest Gold backup into scratch space and read it",
    schedule=CronTriggerTimetable("30 4 * * 0", timezone=PLANT_TIMEZONE),
    start_date=pendulum.datetime(2026, 11, 1, tz=PLANT_TIMEZONE),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=False,
    default_args={
        "owner": "data-engineering",
        "retries": 1,
        "retry_delay": timedelta(minutes=30),
        "execution_timeout": timedelta(hours=3),
    },
    tags=["gold", "calidad", "backup"],
) as dag:

    @task
    def restore_drill() -> None:
        run_gold(restore_drill_command())

    restore_drill()
