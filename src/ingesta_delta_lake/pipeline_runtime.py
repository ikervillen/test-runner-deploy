"""Autonomous lane workers. Neither worker opens the other lane's mutable stores."""

import argparse
import json
import signal
import threading
import time
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import portalocker

from .common import IntegrityError, canonical, digest, now
from .config import load_config
from .contracts import CONTRACTS
from .pipeline_intake import Intake, discover_lanes
from .pipeline_policy import LANES, STORES, IndependentPipelines
from .pipeline_publications import publish_family
from .runtime import atomic_json
from .workers import PHASES, run_phase


class PipelineWorker:
    def __init__(self, pipelines, lane, source, *, stop=None, exporter=None):
        self.pipelines, self.lane = pipelines, lane
        self.storage, self.policy = pipelines.lane(lane), pipelines.policy
        self.parallel = getattr(self.policy, f"{lane}_workers")
        self.source = replace(
            source,
            bronze_root=self.storage.bronze,
            silver_root=self.storage.silver,
            control_root=self.storage.control,
            runtime_root=self.storage.checkpoint / "units",
            intake_path=self.storage.checkpoint / "intake.sqlite",
            intake_ids=(),
            max_parallel_workers=self.parallel,
            arrow_threads=self.policy.arrow_threads,
        )
        self.source.validate()
        self.stop = stop if stop is not None else threading.Event()
        self.exporter = exporter
        self.identity = digest(
            [
                pipelines.identity(lane),
                source.plant,
                [(origin, str(folder)) for origin, folder in source.sources],
                {family: c.name for family, c in CONTRACTS.items()},
            ]
        )
        self.status_path = self.storage.checkpoint / "worker.json"
        self.status = {"lane": lane, "identity": self.identity, "phase": "STARTING"}
        self.guard = threading.Lock()

    def prepare(self):
        marker = self.storage.checkpoint / "identity.json"
        if marker.exists():
            if json.loads(marker.read_text()) != {"lane": self.lane, "identity": self.identity}:
                raise IntegrityError("Lane storage identity differs from the worker")
        else:
            for name in STORES:
                path = getattr(self.storage, name)
                if path.exists() and any(path.iterdir()):
                    raise IntegrityError("Cannot implicitly adopt a nonempty lane store")
            for name in STORES:
                getattr(self.storage, name).mkdir(parents=True, exist_ok=True)
            atomic_json(marker, {"lane": self.lane, "identity": self.identity})
        self.source.runtime_root.mkdir(parents=True, exist_ok=True)

    def update(self, **values):
        with self.guard:
            self.status.update(values, time=now())
            atomic_json(self.status_path, self.status)

    def event(self, value):
        value = {"lane": self.lane, "time": now(), **value}
        path = self.storage.checkpoint / "events"
        path.mkdir(exist_ok=True)
        with (path / (value["time"][:10] + ".jsonl")).open("a", encoding="utf-8") as stream:
            stream.write(canonical(value) + "\n")
            stream.flush()
        print(canonical(value), flush=True)

    def cycle(self, intake, *, phases=PHASES, executor=run_phase):
        self.update(phase="DISCOVERING")
        lanes, window, failures = discover_lanes(self.source.sources, self.policy)
        for failure in failures:
            intake.failure(failure["path"], failure["error"])
            self.event({"event": "DISCOVERY_FAILED", **failure})
        bounds = (window.start.isoformat(), window.end.isoformat()) if window else None
        self.update(phase="CAPTURING", source_window=bounds)
        self.event({"event": "BRONZE_CAPTURE_STARTED", "candidates": len(lanes[self.lane])})
        captured = intake.capture_turn(self.source, self.policy, lanes[self.lane])
        self.event({"event": "BRONZE_CAPTURE", **captured})
        changed, results = False, {}
        for phase in phases:
            if self.stop.is_set():
                break
            configs = {
                family: replace(
                    self.source,
                    family=family,
                    intake_ids=intake.pending(
                        family,
                        getattr(self.policy, f"{self.lane}_batch_files"),
                        window=window if self.lane == "live" else None,
                    ),
                    processing_window=bounds if self.lane == "live" else None,
                )
                for family in phase
            }

            def record(family, result, seconds, *, selected=configs):
                nonlocal changed
                ids = selected[family].intake_ids
                results[family] = result
                if result.get("status") != "FAILED" and not result.get("remaining_versions", 0):
                    try:
                        if ids or result.get("status") == "APPLIED":
                            publish_family(
                                selected[family],
                                self.storage,
                                lane=self.lane,
                                identity=self.identity,
                                family=family,
                                captured_ids=ids,
                            )
                        intake.acknowledge(ids, result, published_at=now())
                    except Exception as exc:
                        result["publication_error"] = str(exc)
                        self.event(
                            {
                                "event": "SILVER_PUBLICATION_FAILED",
                                "family": family,
                                "error": str(exc),
                            }
                        )
                changed = changed or bool(ids) and result.get("status") != "FAILED"
                changed = changed or result.get("status") == "APPLIED"
                self.event({"family": family, "result": result, "seconds": seconds})

            executor(
                self.source,
                phase,
                self.stop,
                self.source.runtime_root / "worker.stop",
                record,
                lambda units: self.update(phase="TRANSFORMING", workers=units),
                limit=self.parallel,
                lane="live" if self.lane == "live" else "all",
                configs=configs,
            )
        if changed and self.exporter is not None and not self.stop.is_set():
            self.update(phase="PUBLISHING")
            try:
                exported = self.exporter(self.source, self.storage)
            except Exception as exc:
                exported = {"status": "FAILED", "error": str(exc)}
            self.event({"event": "SILVER_PUBLICATION", "result": exported})
        self.update(
            phase="IDLE",
            workers=[],
            last_capture=captured,
            results={
                f: {k: r[k] for k in ("status", "delta_version", "remaining_versions") if k in r}
                for f, r in results.items()
            },
        )
        return results

    def run(self, *, once=False):
        self.prepare()
        with portalocker.Lock(str(self.storage.checkpoint / "worker.lock"), mode="a", timeout=0):
            (self.source.runtime_root / "worker.stop").unlink(missing_ok=True)
            stop_heartbeat = threading.Event()

            def heartbeat():
                while not stop_heartbeat.wait(self.policy.poll_seconds):
                    self.update()

            thread = threading.Thread(target=heartbeat, daemon=True)
            thread.start()
            try:
                with closing(
                    Intake(self.source.intake_path, lane=self.lane, identity=self.identity)
                ) as intake:
                    while not self.stop.is_set():
                        started = time.monotonic()
                        self.cycle(intake)
                        if once:
                            break
                        self.stop.wait(
                            max(0, self.policy.poll_seconds - (time.monotonic() - started))
                        )
            finally:
                stop_heartbeat.set()
                thread.join()
                self.update(phase="STOPPED", workers=[])

    def health(self):
        value = json.loads(self.status_path.read_text())
        if value.get("identity") != self.identity or value.get("phase") in {"STOPPED", "STARTING"}:
            raise IntegrityError("Independent pipeline is not running")
        stamp = datetime.fromisoformat(value["time"])
        if (datetime.now(UTC) - stamp).total_seconds() >= self.policy.latency_seconds:
            raise IntegrityError("Independent pipeline heartbeat is stale")
        return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("worker", "health", "prepare"))
    parser.add_argument("--pipelines", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--lane", choices=LANES, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    worker = PipelineWorker(
        IndependentPipelines.load(args.pipelines), args.lane, load_config(args.source)
    )
    if args.command == "worker":
        signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
        signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
        worker.run(once=args.once)
    elif args.command == "prepare":
        worker.prepare()
    else:
        print(canonical(worker.health()))


if __name__ == "__main__":
    main()
