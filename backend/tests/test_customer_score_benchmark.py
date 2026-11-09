from __future__ import annotations

from app.infrastructure.benchmark.customer_score import run_benchmark


def test_benchmark_executes_each_batch_with_requested_concurrency() -> None:
    calls: list[tuple[str, float]] = []

    def request(url: str, payload: dict[str, object], timeout_seconds: float) -> dict[str, object]:
        del payload
        calls.append((url, timeout_seconds))
        return {
            "meta": {
                "query_time_ms": 12.5,
                "files_read": 4,
                "rows_scanned": 20,
                "cache_hit": False,
            }
        }

    result = run_benchmark(
        url="http://example.test/api/customer-score/query",
        payload={"selection": {}},
        repetitions=2,
        concurrency=3,
        timeout_seconds=15,
        request_fn=request,
    )

    assert len(calls) == 6
    assert result["runs"] == 6
    assert result["batches"] == 2
    assert result["concurrency"] == 3
    assert result["files_read"] == 4
    assert result["rows_scanned"] == 20
