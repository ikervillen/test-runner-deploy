"""HTTP and score-query metrics, correct with one or many uvicorn workers.

With ``PROMETHEUS_MULTIPROC_DIR`` set (see ``backend/entrypoint.sh``), every
worker writes its samples to memory-mapped files in that directory and a scrape
served by any worker aggregates all of them. Without it, samples stay in
process memory, which is exact for a single worker. ``prometheus_client`` reads
the variable when the first metric is created, so it must be set before the
server starts and the directory must be emptied on every start.
"""

from __future__ import annotations

import os
from contextlib import suppress

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    generate_latest,
    multiprocess,
)

MULTIPROCESS_ENV = "PROMETHEUS_MULTIPROC_DIR"
LATENCY_BUCKETS_MS = (10.0, 50.0, 100.0, 500.0, 1000.0, 5000.0, 30000.0, 180000.0)

disable_created_metrics()


def multiprocess_enabled() -> bool:
    return bool(os.environ.get(MULTIPROCESS_ENV))


class OperationalMetrics:
    """Prometheus metrics of the web process. Thread-safe; one per application."""

    def __init__(self) -> None:
        self._registry = CollectorRegistry(auto_describe=True)
        self._requests = Counter(
            "calidad_http_requests_total",
            "Total de solicitudes HTTP.",
            ("method", "path", "status"),
            registry=self._registry,
        )
        self._latency = Histogram(
            "calidad_http_request_duration_milliseconds",
            "Latencias HTTP.",
            ("method", "path"),
            buckets=LATENCY_BUCKETS_MS,
            registry=self._registry,
        )
        self._score_queries = Counter(
            "calidad_score_queries_total",
            "Consultas de score por resultado.",
            ("outcome",),
            registry=self._registry,
        )
        self._score_cache = Counter(
            "calidad_score_cache_lookups_total",
            "Consultas de score servidas desde cache (hit) o calculadas (miss).",
            ("result",),
            registry=self._registry,
        )
        self._score_active = Gauge(
            "calidad_score_queries_active",
            "Consultas pesadas actualmente en ejecucion.",
            registry=self._registry,
            multiprocess_mode="livesum",
        )

    def record_http_request(
        self, *, method: str, path: str, status_code: int, duration_ms: float
    ) -> None:
        self._requests.labels(method, path, str(status_code)).inc()
        self._latency.labels(method, path).observe(duration_ms)

    def score_query_started(self) -> None:
        self._score_active.inc()

    def score_query_finished(self, outcome: str, *, cache_hit: bool | None = None) -> None:
        self._score_active.dec()
        self._score_queries.labels(outcome).inc()
        if cache_hit is not None:
            self._score_cache.labels("hit" if cache_hit else "miss").inc()

    def score_query_rejected(self, outcome: str) -> None:
        self._score_queries.labels(outcome).inc()

    def render_prometheus(self) -> str:
        if not multiprocess_enabled():
            return generate_latest(self._registry).decode("utf-8")
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry).decode("utf-8")

    def close(self) -> None:
        """Drop this worker's live gauges so a stopped worker stops counting as busy."""
        if not multiprocess_enabled():
            return
        self._score_active.set(0)
        # Windows cannot delete a file this process still maps; the zero is already exact.
        with suppress(PermissionError):
            multiprocess.mark_process_dead(os.getpid())
