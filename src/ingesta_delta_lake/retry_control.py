"""Persistent retry budget for unattended workers; manual runs are explicit retries."""

import json
import os
import random
import time

from .common import ConfigurationError, IntegrityError, canonical, now


def ready(control):
    value = json.loads(control.setting("retry_state") or "null")
    if not value:
        return None
    if value["paused"] or value["next_at"] > time.time():
        return {
            "status": "PAUSED_RETRY_EXHAUSTED" if value["paused"] else "RETRY_WAIT",
            "complete": False,
            "retry_state": value,
            "next_input_scan_at": None if value["paused"] else value["next_at"],
        }
    return None


def failed(control, error):
    previous = json.loads(control.setting("retry_state") or "null")
    number = previous["failures"] + 1 if previous else 1
    budget = int(os.environ.get("INGESTA_CONTROL_RETRIES", "5"))
    if not 0 <= budget <= 20:
        budget = 0
    root = error
    while root.__cause__ is not None:
        root = root.__cause__
    paused = number > budget or isinstance(root, IntegrityError | ConfigurationError)
    wait = min(300, 10 * 2 ** min(number - 1, 10)) * random.uniform(0.8, 1.0)
    state = {
        "failures": number,
        "paused": paused,
        "next_at": time.time() + wait,
        "error_type": type(root).__name__,
        "updated_at": now(),
    }
    with control.db:
        control.db.execute(
            "INSERT INTO settings VALUES('retry_state',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (canonical(state),),
        )


def succeeded(control):
    with control.db:
        control.db.execute("DELETE FROM settings WHERE key='retry_state'")
