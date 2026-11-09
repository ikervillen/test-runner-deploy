from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from app.infrastructure.cache import LruCache


def test_cache_shares_a_single_inflight_computation() -> None:
    cache: LruCache[str, int] = LruCache(max_size=2)
    started = Event()
    release = Event()
    calls = 0
    calls_lock = Lock()

    def factory() -> int:
        nonlocal calls
        with calls_lock:
            calls += 1
        started.set()
        assert release.wait(timeout=1)
        return 42

    with ThreadPoolExecutor(max_workers=2) as executor:
        leader = executor.submit(cache.get_or_compute, "same-query", factory)
        assert started.wait(timeout=1)
        follower = executor.submit(cache.get_or_compute, "same-query", factory)
        release.set()
        results = [leader.result(timeout=1), follower.result(timeout=1)]

    assert calls == 1
    assert sorted(results) == [(42, False), (42, True)]
