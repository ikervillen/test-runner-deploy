from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from threading import Lock
from typing import Generic, TypeVar

KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


class LruCache(Generic[KeyT, ValueT]):
    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._items: OrderedDict[KeyT, ValueT] = OrderedDict()
        self._inflight: dict[KeyT, Future[ValueT]] = {}
        self._lock = Lock()

    def get(self, key: KeyT) -> ValueT | None:
        if self._max_size == 0:
            return None
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def set(self, key: KeyT, value: ValueT) -> None:
        if self._max_size == 0:
            return
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._max_size:
                self._items.popitem(last=False)

    def get_or_compute(self, key: KeyT, factory: Callable[[], ValueT]) -> tuple[ValueT, bool]:
        """Calcula una vez una clave en vuelo y comparte su resultado con los esperantes."""

        if self._max_size == 0:
            return factory(), False
        with self._lock:
            cached = self._items.get(key)
            if cached is not None:
                self._items.move_to_end(key)
                return cached, True
            pending = self._inflight.get(key)
            if pending is None:
                pending = Future()
                self._inflight[key] = pending
                is_leader = True
            else:
                is_leader = False

        if not is_leader:
            return pending.result(), True

        try:
            value = factory()
        except Exception as error:
            with self._lock:
                self._inflight.pop(key, None)
                pending.set_exception(error)
            raise

        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self._max_size:
                self._items.popitem(last=False)
            self._inflight.pop(key, None)
            pending.set_result(value)
        return value, False

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
