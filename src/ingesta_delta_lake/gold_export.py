"""Optional producer-owned export after a confirmed worker cycle; disabled by default."""

import json
import os

from .common import IntegrityError, now
from .runtime import atomic_json


def refresh(config, discoveries, *, exporter=None):
    """Republish review coverage while a data backlog is still pending.

    Resolved controls must show READY without waiting for another family's queue.
    This does not rewrite Silver tables or advance their data horizon.
    """
    if os.environ.get("INGESTA_GOLD_EXPORT", "0") != "1":
        return {"status": "DISABLED"}
    try:
        return _refresh(config, discoveries, exporter)
    except Exception as exc:
        # Even unavailable state storage must not end the ingestion worker.
        return _failure(exc)


def publish(config, discoveries, *, exporter=None):
    if os.environ.get("INGESTA_GOLD_EXPORT", "0") != "1":
        return {"status": "DISABLED"}
    try:
        return _publish(config, discoveries, exporter)
    except Exception as exc:
        # Even unavailable state storage must not end the ingestion worker.
        return _failure(exc)


def _failure(exc):
    return {"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)}


def _publish(config, discoveries, exporter):
    root = config.silver_root.parent.resolve()
    if any(not p.resolve().is_relative_to(root) for p in (config.bronze_root, config.control_root)):
        raise IntegrityError("Gold export requires one qualified producer-owned storage root")
    path = root / "gold-export-status.json"
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        previous = {}
    except (json.JSONDecodeError, UnicodeError):
        previous = {}
    if not isinstance(previous, dict) or not isinstance(previous.get("discoveries", {}), dict):
        previous = {}
    discoveries = {**previous.get("discoveries", {}), **discoveries}
    status = {"last_attempt_at": now(), "discoveries": discoveries}
    if "last_success_at" in previous:
        status["last_success_at"] = previous["last_success_at"]
    try:
        if exporter is None:
            from app.gold.producer import export_confirmed

            exporter = export_confirmed
        result = exporter(
            config, root, max_ledger_rows=None, source_mode="continuous", discoveries=discoveries
        )
        status.update(result, last_success_at=now())
    except Exception as exc:
        # Export failure must not undo accepted ingestion commits or their receipts.
        status.update(_failure(exc))
    atomic_json(path, status)
    return status


def _refresh(config, discoveries, exporter):
    root = config.silver_root.parent.resolve()
    if any(not p.resolve().is_relative_to(root) for p in (config.bronze_root, config.control_root)):
        raise IntegrityError("Gold export requires one qualified producer-owned storage root")
    path = root / "gold-export-status.json"
    previous = _read_status(path)
    discoveries = {**previous.get("discoveries", {}), **discoveries}
    status = {**previous, "last_coverage_attempt_at": now(), "discoveries": discoveries}
    try:
        if exporter is None:
            from app.gold.producer import refresh_review_coverage

            exporter = refresh_review_coverage
        result = exporter(config, root, discoveries=discoveries)
        status.update(result)
        if result.get("status") != "FAILED":
            status["last_coverage_success_at"] = now()
    except Exception as exc:
        # Coverage refresh must not undo accepted ingestion commits.
        status.update(_failure(exc))
    atomic_json(path, status)
    return status


def _read_status(path):
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeError):
        return {}
    if not isinstance(previous, dict) or not isinstance(previous.get("discoveries", {}), dict):
        return {}
    return previous
