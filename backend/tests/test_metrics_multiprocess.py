"""Multiprocess mode is fixed when prometheus_client is first imported, so each
scenario runs in fresh interpreters that share only PROMETHEUS_MULTIPROC_DIR."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

WORKER = """
from app.infrastructure.metrics import OperationalMetrics
metrics = OperationalMetrics()
metrics.record_http_request(method="GET", path="/api/catalog", status_code=200, duration_ms=20)
metrics.score_query_started()
if {finish}:
    metrics.score_query_finished("success", cache_hit=False)
if {close}:
    metrics.close()
"""

SCRAPE = """
from app.infrastructure.metrics import OperationalMetrics
print(OperationalMetrics().render_prometheus())
"""


def _python(code: str, directory: Path) -> str:
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(directory), "PYTHONPATH": str(BACKEND)}
    completed = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    return completed.stdout


def test_scrape_from_any_worker_sums_every_worker(tmp_path: Path) -> None:
    _python(WORKER.format(finish=True, close=True), tmp_path)
    _python(WORKER.format(finish=True, close=True), tmp_path)

    rendered = _python(SCRAPE, tmp_path)

    assert 'calidad_http_requests_total{method="GET",path="/api/catalog",status="200"} 2.0' in (
        rendered
    )
    assert (
        "calidad_http_request_duration_milliseconds_count"
        '{method="GET",path="/api/catalog"} 2.0' in rendered
    )
    assert 'calidad_score_cache_lookups_total{result="miss"} 2.0' in rendered


def test_gracefully_stopped_worker_drops_its_active_queries(tmp_path: Path) -> None:
    _python(WORKER.format(finish=False, close=True), tmp_path)
    assert "calidad_score_queries_active 0.0" in _python(SCRAPE, tmp_path)


def test_crashed_worker_keeps_its_last_active_value_until_restart(tmp_path: Path) -> None:
    _python(WORKER.format(finish=False, close=False), tmp_path)
    assert "calidad_score_queries_active 1.0" in _python(SCRAPE, tmp_path)
