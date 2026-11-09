from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import TypeVar

from starlette import status

from app.api.errors import ApplicationError

from .metrics import OperationalMetrics

T = TypeVar("T")


class ScoreQueryExecutor:
    """Protege el backend de calculos Polars simultaneos sin cancelar hilos activos."""

    def __init__(
        self,
        *,
        max_concurrency: int,
        queue_timeout_seconds: float,
        execution_timeout_seconds: float,
        metrics: OperationalMetrics,
    ) -> None:
        self._semaphore = asyncio.BoundedSemaphore(max_concurrency)
        self._queue_timeout_seconds = queue_timeout_seconds
        self._execution_timeout_seconds = execution_timeout_seconds
        self._metrics = metrics

    async def execute(self, operation: Callable[[], T]) -> T:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self._queue_timeout_seconds)
        except TimeoutError as error:
            self._metrics.score_query_rejected("capacity_rejected")
            raise ApplicationError(
                "SCORE_QUERY_CAPACITY_REACHED",
                "El servidor esta procesando el maximo de consultas de calidad. "
                "Intentalo de nuevo.",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            ) from error

        self._metrics.score_query_started()
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            response = await asyncio.wait_for(
                asyncio.shield(task), timeout=self._execution_timeout_seconds
            )
        except TimeoutError as error:
            task.add_done_callback(self._release_after_timeout)
            self._metrics.score_query_rejected("timeout")
            raise ApplicationError(
                "SCORE_QUERY_TIMEOUT",
                "La consulta de calidad ha superado el tiempo maximo permitido.",
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                details={"timeout_seconds": self._execution_timeout_seconds},
            ) from error
        except asyncio.CancelledError:
            task.add_done_callback(
                lambda completed: self._release_after_timeout(completed, "cancelled_completed")
            )
            self._metrics.score_query_rejected("cancelled")
            raise
        except Exception:
            self._release()
            self._metrics.score_query_finished("error")
            raise
        else:
            self._release()
            cache_hit = getattr(getattr(response, "meta", None), "cache_hit", None)
            self._metrics.score_query_finished("success", cache_hit=cache_hit)
            return response

    def _release_after_timeout(
        self, task: asyncio.Task[object], outcome: str = "timeout_completed"
    ) -> None:
        with suppress(asyncio.CancelledError, Exception):
            task.exception()
        self._release()
        self._metrics.score_query_finished(outcome)

    def _release(self) -> None:
        self._semaphore.release()
