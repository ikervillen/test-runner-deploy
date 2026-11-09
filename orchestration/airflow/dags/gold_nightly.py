"""Nightly verified backup, then Delta maintenance; replaces ops/backup-gold.ps1.

Maintenance deletes files, so it only runs after that night's backup is verified.
Backup retention is not automated: deleting copies needs an explicit RPO decision.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow.sdk import DAG, task
from airflow.timetables.trigger import CronTriggerTimetable
from gold_runner import run_gold
from gold_tasks import GOLD_STORE_POOL, PLANT_TIMEZONE, backup_command, maintain_command

with DAG(
    dag_id="gold_nightly",
    description="Verified backup of the Gold store, then compaction and VACUUM",
    schedule=CronTriggerTimetable("30 2 * * *", timezone=PLANT_TIMEZONE),
    start_date=pendulum.datetime(2026, 11, 1, tz=PLANT_TIMEZONE),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={
        "owner": "data-engineering",
        "retries": 2,
        "retry_delay": timedelta(minutes=10),
        "execution_timeout": timedelta(hours=2),
    },
    tags=["gold", "calidad", "backup"],
) as dag:

    @task(pool=GOLD_STORE_POOL)
    def backup(dag_run=None, ti=None) -> None:
        run_gold(backup_command(dag_run.run_after, ti.try_number))

    @task(pool=GOLD_STORE_POOL)
    def maintain() -> None:
        run_gold(maintain_command())

    backup() >> maintain()
