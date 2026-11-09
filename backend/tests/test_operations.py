from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.api.errors import ApplicationError
from app.infrastructure.metrics import OperationalMetrics
from app.infrastructure.operations import ScoreQueryExecutor


def test_metrics_render_request_and_score_query_counters() -> None:
    metrics = OperationalMetrics()
    metrics.record_http_request(
        method="POST",
        path="/api/customer-score/query",
        status_code=200,
        duration_ms=12.5,
    )
    metrics.score_query_started()
    metrics.score_query_finished("success", cache_hit=True)

    rendered = metrics.render_prometheus()

    assert (
        "calidad_http_requests_total"
        '{method="POST",path="/api/customer-score/query",status="200"} 1.0' in rendered
    )
    assert (
        "calidad_http_request_duration_milliseconds_bucket"
        '{le="50.0",method="POST",path="/api/customer-score/query"} 1.0' in rendered
    )
    assert 'calidad_score_queries_total{outcome="success"} 1.0' in rendered
    assert 'calidad_score_cache_lookups_total{result="hit"} 1.0' in rendered
    assert "calidad_score_queries_active 0.0" in rendered
    assert "_created" not in rendered


def test_score_query_executor_rejects_when_capacity_is_full() -> None:
    async def scenario() -> None:
        metrics = OperationalMetrics()
        executor = ScoreQueryExecutor(
            max_concurrency=1,
            queue_timeout_seconds=0.01,
            execution_timeout_seconds=1,
            metrics=metrics,
        )
        started = asyncio.create_task(executor.execute(lambda: time.sleep(0.1)))
        await asyncio.sleep(0.01)
        with pytest.raises(ApplicationError, match="maximo de consultas") as captured:
            await executor.execute(lambda: "not reached")
        assert captured.value.status_code == 429
        await started

    asyncio.run(scenario())


def test_score_query_executor_returns_controlled_timeout() -> None:
    async def scenario() -> None:
        executor = ScoreQueryExecutor(
            max_concurrency=1,
            queue_timeout_seconds=0.1,
            execution_timeout_seconds=0.01,
            metrics=OperationalMetrics(),
        )
        with pytest.raises(ApplicationError, match="tiempo maximo") as captured:
            await executor.execute(lambda: time.sleep(0.05))
        assert captured.value.status_code == 504
        await asyncio.sleep(0.06)

    asyncio.run(scenario())


def test_cancelled_request_holds_capacity_until_worker_finishes_then_recovers() -> None:
    async def scenario() -> None:
        metrics = OperationalMetrics()
        executor = ScoreQueryExecutor(
            max_concurrency=1,
            queue_timeout_seconds=0.01,
            execution_timeout_seconds=1,
            metrics=metrics,
        )
        started, finish = threading.Event(), threading.Event()

        def work():
            started.set()
            finish.wait(timeout=2)

        running = asyncio.create_task(executor.execute(work))
        assert await asyncio.to_thread(started.wait, 1)
        try:
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            with pytest.raises(ApplicationError) as rejected:
                await executor.execute(lambda: "too soon")
            assert rejected.value.status_code == 429
        finally:
            finish.set()
        await asyncio.sleep(0.03)
        assert await executor.execute(lambda: "recovered") == "recovered"
        assert "calidad_score_queries_active 0.0" in metrics.render_prometheus()

    asyncio.run(scenario())
