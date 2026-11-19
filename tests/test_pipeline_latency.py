"""Measure stable-file-to-confirmed-Silver latency while historical work is active."""

import os
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta

import portalocker
from test_cont_zc import line as zc_line
from test_lista_defectos import line as defects_line
from test_lista_rech import line as rejections_line
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake.pipeline_publications import SilverSnapshot
from ingesta_delta_lake.pipeline_runtime import PipelineWorker


def test_live_mia_meets_configured_latency_with_historical_writer_locked(
    config, pipeline_config, record_property
):
    origins = ("AV21", "AV22", "AV23", "AV31", "AV32")
    sources = tuple((origin, config.input_root / origin) for origin in origins)
    for _, folder in sources:
        folder.mkdir(exist_ok=True)
    config = replace(
        config, family="LISTA_RECH", sources=sources, timestamp_policy="local_naive_test_v1"
    )
    plan = replace(pipeline_config, policy=replace(pipeline_config.policy, poll_seconds=1))
    incoming = config.sources[0][1]
    for index in range(200):
        stamp = datetime(2019, 1, 1) + timedelta(minutes=10 * index)
        (incoming / (stamp.strftime("%d%m%Y_%H%M%S") + "_LISTA_RECH.csv")).write_text(
            rejections_line(date=stamp.strftime("%d/%m/%Y %H:%M:%S"))
        )
    cutoff = datetime(2020, 11, 18, 12)
    date = cutoff.strftime("%d/%m/%Y %H:%M:%S")
    hourly = {
        "LISTA_RECH": rejections_line(date=date, inspected=107),
        "LISTA_DEFECTOS": defects_line(date=date, inspected=107),
        "CONT_ZC": zc_line(FECHA=date),
    }
    live, history = (PipelineWorker(plan, lane, config) for lane in ("live", "history"))
    live.prepare()
    history.prepare()
    errors = []

    def execute(worker):
        try:
            worker.run(phases=(tuple(hourly),))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=execute, args=(w,)) for w in (live, history)]
    lock = history.source.silver_root / ".writer-AIALA-LISTA_RECH.lock"
    with portalocker.Lock(lock, timeout=0):
        for thread in threads:
            thread.start()
        try:
            available = time.monotonic()
            for _, folder in sources:
                for number in range(27):
                    (folder / f"18112020_120000_BRONZE_{number}.csv").write_text("fixture\n")
                for family, payload in hourly.items():
                    target = folder / f"18112020_120000_{family}.csv"
                    temporary = target.with_suffix(".part")
                    with temporary.open("w", encoding=config.encoding) as output:
                        output.write(payload)
                        output.flush()
                        os.fsync(output.fileno())
                    temporary.replace(target)
            deadline = available + plan.policy.latency_seconds
            while time.monotonic() < deadline:
                try:
                    snap = SilverSnapshot.open(plan.live)
                    published = {
                        family: {
                            row["origen_fenix"]
                            for b in snap.batches(family, batch_size=10)
                            for row in b.to_pylist()
                            if row["FECHA"] == cutoff
                        }
                        for family in hourly
                        if family in snap.manifest["tables"]
                    }
                    if published == dict.fromkeys(hourly, set(origins)):
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
