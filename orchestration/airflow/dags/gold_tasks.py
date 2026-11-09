"""Commands the Gold DAGs run. Pure functions, no Airflow imports, unit-tested.

Industrial rules (ten-minute step, per-production coverage, continuous-source guard)
live in the application (`app.gold.schedule`, `app.gold.coverage`); DAGs only pass
the scheduled moment, which is the ceiling of the attempt.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, tzinfo

import pendulum

GOLD_CLI = "calidad-gold"
SETTINGS = "/config/settings.json"
GOLD_ROOT = "/gold"
BACKUP_ROOT = "/backup"
RESTORE_SCRATCH = f"{BACKUP_ROOT}/.restore-drill"
GOLD_STORE_POOL = "gold_store"
PLANT_TIMEZONE: tzinfo = pendulum.timezone(os.environ.get("GOLD_PLANT_TIMEZONE", "Europe/Madrid"))
PUBLICATION_RETENTION_DAYS = int(os.environ.get("GOLD_PUBLICATION_RETENTION_DAYS", "30"))
# Mirrors app.gold.failures, which lives in the Gold virtualenv, not Airflow's.
PERMANENT_EXIT_CODES = frozenset({2, 65})


def local_moment(moment: datetime, zone: tzinfo = PLANT_TIMEZONE) -> str:
    """Plant wall-clock time of an aware instant, as the naive ISO string the CLI expects."""
    if moment.tzinfo is None:
        raise ValueError("Scheduler moments must be timezone-aware")
    return moment.astimezone(zone).replace(tzinfo=None).isoformat(timespec="seconds")


def publish_command(run_after: datetime) -> list[str]:
    return [GOLD_CLI, "publish", "--settings", SETTINGS, "--at", local_moment(run_after)]


def coverage_command(run_after: datetime) -> list[str]:
    """Read-only: can any production advance its cutoff with what Silver has published?"""
    return [GOLD_CLI, "coverage", "--settings", SETTINGS, "--at", local_moment(run_after)]


def backup_command(run_after: datetime, try_number: int) -> list[str]:
    """Backups refuse existing destinations, so every attempt writes a new directory."""
    stamp = run_after.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = f"{BACKUP_ROOT}/{stamp}-try{try_number}"
    return [GOLD_CLI, "backup", "--source", GOLD_ROOT, "--destination", target]


def maintain_command(keep_days: int = PUBLICATION_RETENTION_DAYS) -> list[str]:
    return [GOLD_CLI, "maintain", "--root", GOLD_ROOT, "--keep-days", str(keep_days)]


def restore_drill_command() -> list[str]:
    return [GOLD_CLI, "restore-drill", "--root", GOLD_ROOT, "--scratch", RESTORE_SCRATCH]
