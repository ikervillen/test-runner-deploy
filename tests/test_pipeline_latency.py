"""Measure stable-file-to-confirmed-Silver latency while historical work is active."""

import os
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta

import portalocker
from test_lista_rech import line
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake.pipeline_publications import SilverSnapshot
from ingesta_delta_lake.pipeline_runtime import PipelineWorker


def test_live_mia_meets_configured_latency_with_historical_writer_locked(
    config, pipeline_config, record_property
):
    config = replace(config, family="LISTA_RECH", timestamp_policy="local_naive_test_v1")
    plan = replace(pipeline_config, policy=replace(pipeline_config.policy, poll_seconds=1))
    incoming = config.sources[0][1]
    for index in range(200):
        stamp = datetime(2019, 1, 1) + timedelta(minutes=10 * index)
        (incoming / (stamp.strftime("%d%m%Y_%H%M%S") + "_LISTA_RECH.csv")).write_text(
            line(date=stamp.strftime("%d/%m/%Y %H:%M:%S"))
        )
    live, history = (PipelineWorker(plan, lane, config) for lane in ("live", "history"))
    live.prepare()
    history.prepare()
    errors = []

    def execute(worker):
        try:
            worker.run(phases=(("LISTA_RECH",),))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=execute, args=(w,)) for w in (live, history)]
    lock = history.source.silver_root / ".writer-AIALA-LISTA_RECH.lock"
    with portalocker.Lock(lock, timeout=0):
        for thread in threads:
            thread.start()
        try:
            target = incoming / "18112020_120000_LISTA_RECH.csv"
            with target.open("w", encoding=config.encoding) as output:
                output.write(line(date="18/11/2020 12:00:00", inspected=107))
                output.flush()
                os.fsync(output.fileno())
            available = time.monotonic()
            deadline = available + plan.policy.latency_seconds
            while time.monotonic() < deadline:
                try:
                    snap = SilverSnapshot.open(plan.live)
                    rows = [
                        r for b in snap.batches("LISTA_RECH", batch_size=10) for r in b.to_pylist()
                    ]
                    if any(
                        r["FECHA"] == datetime(2020, 11, 18, 12) and r["INSPECC"] == 107
                        for r in rows
                    ):
                        break
                except (FileNotFoundError, KeyError):
                    pass
                if errors:
                    raise errors[0]
                time.sleep(0.1)
            else:
                raise AssertionError("Live exceeded its configured stable-file-to-Silver SLA")
            elapsed = time.monotonic() - available
            record_property("stable_file_to_silver_seconds", elapsed)
            assert elapsed < plan.policy.latency_seconds
            assert threads[1].is_alive()
            assert history.status["phase"] != "STOPPED"
        finally:
            live.stop.set()
            history.stop.set()
            for thread in threads:
                thread.join(plan.policy.latency_seconds)
    assert not any(t.is_alive() for t in threads)
    assert not errors
