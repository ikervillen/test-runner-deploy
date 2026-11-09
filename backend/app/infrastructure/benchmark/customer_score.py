from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class QuerySample:
    elapsed_ms: float
    query_time_ms: float
    files_read: int
    rows_scanned: int
    cache_hit: bool


def percentile(values: Sequence[float], value: float) -> float:
    if not values:
        raise ValueError("No se pueden calcular percentiles sin muestras.")
    ordered = sorted(values)
    position = (len(ordered) - 1) * value
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(samples: Sequence[QuerySample]) -> dict[str, object]:
    if not samples:
        raise ValueError("No se han recibido muestras de consulta.")
    elapsed = [sample.elapsed_ms for sample in samples]
    query = [sample.query_time_ms for sample in samples]
    return {
        "runs": len(samples),
        "elapsed_ms": {
            "min": round(min(elapsed), 2),
            "p50": round(percentile(elapsed, 0.5), 2),
            "p95": round(percentile(elapsed, 0.95), 2),
            "max": round(max(elapsed), 2),
        },
        "server_query_time_ms": {
            "min": round(min(query), 2),
            "p50": round(percentile(query, 0.5), 2),
            "p95": round(percentile(query, 0.95), 2),
            "max": round(max(query), 2),
        },
        "files_read": max(sample.files_read for sample in samples),
        "rows_scanned": max(sample.rows_scanned for sample in samples),
        "cache_hits": sum(sample.cache_hit for sample in samples),
        "samples": [asdict(sample) for sample in samples],
    }


def _query_sample(
    *,
    url: str,
    payload: dict[str, object],
    timeout_seconds: float,
    request_fn: Callable[[str, dict[str, object], float], dict[str, object]],
) -> QuerySample:
    started = time.perf_counter()
    response = request_fn(url, payload, timeout_seconds)
    elapsed_ms = (time.perf_counter() - started) * 1000
    meta = response.get("meta")
    if not isinstance(meta, dict):
        raise RuntimeError("La respuesta no contiene metadatos de rendimiento.")
    return QuerySample(
        elapsed_ms=elapsed_ms,
        query_time_ms=float(meta.get("query_time_ms", 0)),
        files_read=int(meta.get("files_read", 0)),
        rows_scanned=int(meta.get("rows_scanned", 0)),
        cache_hit=bool(meta.get("cache_hit", False)),
    )


def request_json(url: str, payload: dict[str, object], timeout_seconds: float) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            decoded: dict[str, object] = json.loads(response.read().decode("utf-8"))
            return decoded
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"La consulta devolvio HTTP {error.code}: {detail}") from error


def run_benchmark(
    *,
    url: str,
    payload: dict[str, object],
    repetitions: int,
    timeout_seconds: float,
    concurrency: int = 1,
    request_fn: Callable[[str, dict[str, object], float], dict[str, object]] = request_json,
) -> dict[str, object]:
    if repetitions < 1:
        raise ValueError("Las repeticiones deben ser al menos una.")
    if concurrency < 1:
        raise ValueError("La concurrencia debe ser al menos una.")
    samples: list[QuerySample] = []
    for _ in range(repetitions):
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(
                    _query_sample,
                    url=url,
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                    request_fn=request_fn,
                )
                for _ in range(concurrency)
            ]
            samples.extend(future.result() for future in futures)
    summary = summarize(samples)
    summary["concurrency"] = concurrency
    summary["batches"] = repetitions
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Mide una consulta de calidad contra la API.")
    parser.add_argument("--url", default="http://127.0.0.1:8001/api/customer-score/query")
    parser.add_argument(
        "--selection", type=Path, required=True, help="JSON de CustomerScoreRequest."
    )
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Consultas simultaneas por repeticion.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--output", type=Path, help="Ruta opcional del informe JSON.")
    args = parser.parse_args()
    if args.repetitions < 1 or args.concurrency < 1:
        parser.error("--repetitions y --concurrency deben ser al menos 1.")
    try:
        payload = json.loads(args.selection.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        parser.error(f"No se puede leer --selection: {error}")

    result = run_benchmark(
        url=args.url,
        payload=payload,
        repetitions=args.repetitions,
        timeout_seconds=args.timeout_seconds,
        concurrency=args.concurrency,
    )
    report = json.dumps(result, indent=2, ensure_ascii=False)
    print(report)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
