from __future__ import annotations

from app.infrastructure.benchmark.customer_score import (
    QuerySample,
    percentile,
    run_benchmark,
    summarize,
)


def test_percentile_uses_linear_interpolation() -> None:
    assert percentile([10, 20, 30, 40], 0.95) == 38.5


def test_summary_reports_server_and_transport_timings() -> None:
    result = summarize(
        [
            QuerySample(100, 90, 3, 50, False),
            QuerySample(200, 180, 4, 60, True),
        ]
    )

    assert result["runs"] == 2
    assert result["elapsed_ms"] == {"min": 100, "p50": 150, "p95": 195, "max": 200}
    assert result["server_query_time_ms"]["p95"] == 175.5
    assert result["files_read"] == 4
    assert result["rows_scanned"] == 60
    assert result["cache_hits"] == 1


def test_benchmark_collects_query_metadata() -> None:
    calls = 0

    def fake_request(_: str, __: dict[str, object], ___: float) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"meta": {"query_time_ms": 12.5, "files_read": 2, "rows_scanned": 10}}

    result = run_benchmark(
        url="http://test/query",
        payload={"selection": {}},
        repetitions=2,
        timeout_seconds=1,
        request_fn=fake_request,
    )

    assert calls == 2
    assert result["runs"] == 2
    assert result["cache_hits"] == 0
