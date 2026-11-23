"""Persistent retry budget for unattended workers; manual runs are explicit retries."""

import json
import os
import random
import time

from .common import ConfigurationError, DependencyPending, IntegrityError, canonical, now

LEGACY_DEPENDENCIES = {
    "CATALOGO_PRODUCCIONES": "Recuperar MP9 antes de derivar su catálogo",
    "ASIGNACION_PRODUCCION_RAMAL": "Actualizar el catálogo: sus fuentes han cambiado",
}


# Revalidate pre-existing Live pauses once after the dependency and handover policy changes.
# An integrity failure under this policy remains paused with its original error recorded.
LIVE_DERIVED_POLICY = "live-production-continuity-v1"


def live_derived(config):
    return config.pipeline_lane == "live" and config.family in LEGACY_DEPENDENCIES


def ready(control):
    value = json.loads(control.setting("retry_state") or "null")
    if not value:
        return None
    family = control.config.family
    if (
        value["paused"]
        and value["error_type"] == "IntegrityError"
        and live_derived(control.config)
        and control.setting("retry_revalidation_policy") != LIVE_DERIVED_POLICY
    ):
        return None
    if (
        value["paused"]
        and value["error_type"] == "IntegrityError"
        and family in LEGACY_DEPENDENCIES
    ):
        last = control.db.execute(
            "SELECT result_json FROM attempts WHERE finished_at IS NOT NULL "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        if last and json.loads(last[0] or "{}").get("error") == LEGACY_DEPENDENCIES[family]:
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
    if isinstance(error, DependencyPending):
        succeeded(control)
        return
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
        if live_derived(control.config):
            control.db.execute(
                "INSERT INTO settings VALUES('retry_revalidation_policy',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (LIVE_DERIVED_POLICY,),
            )
        control.db.execute(
            "INSERT INTO settings VALUES('retry_state',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (canonical(state),),
        )


def succeeded(control):
    with control.db:
        control.db.execute("DELETE FROM settings WHERE key='retry_state'")
