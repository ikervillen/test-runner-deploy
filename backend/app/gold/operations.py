"""Durable publisher health and Prometheus text, separate from business availability."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from time import perf_counter, time
from typing import Any

import portalocker

from .common import atomic_json, read_json, utc_now

BUCKETS = (1, 5, 15, 30, 60, 120, 300, 600)


def observed(output: Path, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(output / "observer.lock", timeout=0):
        return _observed(output, operation)


def _observed(output: Path, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    path = output / "operation-status.json"
    previous = read_json(path) if path.exists() else {}
    state = {**previous, "last_attempt_at": utc_now(), "status": "RUNNING"}
    atomic_json(path, state)
    start = perf_counter()
    try:
        result = operation()
    except Exception as exc:
        state.update(
            status="FAILED", error_type=type(exc).__name__, failures=state.get("failures", 0) + 1
        )
        raise
    else:
        state.update(
            status=result["status"],
            last_success_at=utc_now(),
            last_success_timestamp_seconds=time(),
            publication_id=result["publication_id"],
            rows=result.get("rows", state.get("rows", {})),
            pending_scopes=result.get("pending_scopes", state.get("pending_scopes", 0)),
            incremental=result.get("incremental", {}),
            source=result.get("source", state.get("source", {})),
            coverage=result.get("coverage", state.get("coverage", {})),
        )
        return result
    finally:
        duration = perf_counter() - start
        state.update(
            last_duration_seconds=duration,
            duration_sum=state.get("duration_sum", 0) + duration,
            attempts=state.get("attempts", 0) + 1,
            completed_at=utc_now(),
        )
        buckets = state.get("duration_buckets", {})
        state["duration_buckets"] = {
            str(b): buckets.get(str(b), 0) + int(duration <= b) for b in BUCKETS
        }
        atomic_json(path, state)


def render(root: Path) -> str:
    path = root / "operation-status.json"
    if not path.exists():
        return "# TYPE calidad_gold_status_available gauge\ncalidad_gold_status_available 0\n"
    state = read_json(path)
    from .publications import envelope

    try:
        _, publication = envelope(root)
    except (OSError, ValueError):
        publication = {}
    scalars = {
        "calidad_gold_status_available": 1,
        "calidad_gold_last_success_timestamp_seconds": state.get(
            "last_success_timestamp_seconds", 0
        ),
        "calidad_gold_last_attempt_timestamp_seconds": datetime.fromisoformat(
            state["last_attempt_at"]
        ).timestamp(),
        "calidad_gold_last_attempt_success": int(state["status"] in {"PUBLISHED", "UNCHANGED"}),
        "calidad_gold_pending_scopes": state.get("pending_scopes", 0),
        "calidad_gold_failures_total": state.get("failures", 0),
        "calidad_gold_publication_timestamp_seconds": datetime.fromisoformat(
            publication["created_at"]
        ).timestamp()
        if publication
        else 0,
        "calidad_gold_source_rows_read_last": state.get("incremental", {}).get(
            "source_rows_read", 0
        ),
        "calidad_gold_mia_windows_reused_last": state.get("incremental", {}).get(
            "mia_windows_reused", 0
        ),
    }
    for name, status_file in (
        ("backup", "backup-status.json"),
        ("restore_drill", "restore-status.json"),
        ("maintenance", "maintenance-status.json"),
    ):
        scalars[f"calidad_gold_{name}_success_timestamp_seconds"] = _success_timestamp(
            root / status_file
        )
    source = state.get("source") or {}
    scalars["calidad_gold_source_publication_timestamp_seconds"] = (
        datetime.fromisoformat(source["published_at"]).timestamp() if source else 0
    )
    coverage = state.get("coverage") or {}
    scalars["calidad_gold_coverage_blocked_productions"] = coverage.get("blocked_productions", 0)
    scalars["calidad_gold_coverage_max_lag_seconds"] = coverage.get("max_lag_seconds", 0)
    lines = []
    for name, value in scalars.items():
        lines.extend(
            [
                f"# TYPE {name} {'counter' if name.endswith('_total') else 'gauge'}",
                f"{name} {value}",
            ]
        )
    lines.append("# TYPE calidad_gold_duration_seconds histogram")
    for bound, count in state.get("duration_buckets", {}).items():
        lines.append(f'calidad_gold_duration_seconds_bucket{{le="{bound}"}} {count}')
    lines.extend(
        [
            f'calidad_gold_duration_seconds_bucket{{le="+Inf"}} {state["attempts"]}',
            f"calidad_gold_duration_seconds_sum {state['duration_sum']}",
            f"calidad_gold_duration_seconds_count {state['attempts']}",
        ]
    )
    for name, rows in sorted(state.get("rows", {}).items()):
        lines.append(f'calidad_gold_table_rows{{table="{name}"}} {rows}')
    lags = source.get("discovery_lag_seconds", {})
    if lags:
        lines.append("# TYPE calidad_gold_source_discovery_lag_seconds gauge")
        for family, seconds in sorted(lags.items()):
            lines.append(
                f'calidad_gold_source_discovery_lag_seconds{{family="{family}"}} {seconds}'
            )
    stages = state.get("incremental", {}).get("stage_seconds", {})
    if stages:
        lines.append("# TYPE calidad_gold_stage_duration_seconds_last gauge")
        for name, seconds in sorted(stages.items()):
            lines.append(f'calidad_gold_stage_duration_seconds_last{{stage="{name}"}} {seconds}')
    return "\n".join(lines) + "\n"


def _success_timestamp(path: Path) -> float:
    if not path.exists():
        return 0
    return datetime.fromisoformat(read_json(path)["last_success_at"]).timestamp()
