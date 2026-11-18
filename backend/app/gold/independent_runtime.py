"""One Gold owner per physical lane, without shared live/history scheduling gates."""

from __future__ import annotations

import argparse
import signal
import threading
from pathlib import Path

import portalocker
import pyarrow as pa

from .common import IntegrityError, atomic_json, digest, read_json
from .independent_inputs import cutoff, export
from .pipeline import run
from .publications import Snapshot


class Worker:
    def __init__(self, pipelines, lane, source, *, score, catalogs):
        self.pipelines, self.lane, self.source = pipelines, lane, source
        self.storage = pipelines.lane(lane)
        self.policy = pipelines.policy
        self.score, self.catalogs = score, catalogs
        self.stop = threading.Event()
        self.root = self.storage.gold
        self.reference = None

    def prepare(self):
        identity = digest(
            [
                self.pipelines.identity(self.lane),
                self.source.plant,
                self.source.environment,
                self.source.line_by_origin,
                self.source.topology_enabled,
                self.source.line_code_by_origin,
            ]
        )
        marker = self.root / "checkpoint" / "identity.json"
        if marker.exists():
            if read_json(marker) != {"identity": identity, "lane": self.lane}:
                raise IntegrityError("Gold store belongs to a different lane or contract")
        else:
            if self.root.exists() and any(self.root.iterdir()):
                raise IntegrityError("Gold will not silently adopt an unmarked existing store")
            marker.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(marker, {"identity": identity, "lane": self.lane})
        pa.set_cpu_count(self.policy.arrow_threads)
        pa.set_io_thread_count(self.policy.arrow_threads)
        return identity

    def historical_reference(self):
        if self.lane != "live":
            return None
        root = self.pipelines.history.gold / "published"
        cache = self.root / "checkpoint" / "history-reference.json"
        try:
            self.reference = Snapshot.open(root, require_gold_contract=True)
            atomic_json(cache, {"publication_id": self.reference.publication_id})
        except (OSError, ValueError):
            if self.reference is None and cache.exists():
                self.reference = Snapshot.open(
                    root,
                    require_gold_contract=True,
                    publication_id=read_json(cache)["publication_id"],
                )
        return self.reference

    def cycle(self):
        evidence = export(self.pipelines, self.lane, self.source)
        return run(
            self.root / "inputs",
            self.root / "published",
            confirmed_input=evidence,
            reference_snapshot=self.historical_reference(),
            cutoff=cutoff(evidence),
            bundle=self.catalogs,
            config=self.score,
            environment=self.source.environment,
            plant=self.source.plant,
            max_rows_per_source=self.policy.read_max_rows,
            max_total_rows=self.policy.read_max_rows,
        )

    def run(self, *, once=False):
        from .common import utc_now

        self.prepare()
        with portalocker.Lock(self.root / "checkpoint" / "worker.lock", timeout=0):
            while not self.stop.is_set():
                try:
                    result = self.cycle()
                except Exception as exc:
                    result = {
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                result.update(lane=self.lane, time=utc_now())
                atomic_json(self.root / "checkpoint" / "health.json", result)
                print(result, flush=True)
                if once:
                    if result["status"] == "FAILED":
                        raise IntegrityError(result["error"])
                    return
                self.stop.wait(self.policy.poll_seconds)


def main(argv=None):
    from ingesta_delta_lake.config import load_config
    from ingesta_delta_lake.pipeline_policy import LANES, IndependentPipelines

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipelines", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--lane", choices=LANES, required=True)
    parser.add_argument("--score", type=Path, required=True)
    parser.add_argument("--catalogs", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    worker = Worker(
        IndependentPipelines.load(args.pipelines),
        args.lane,
        load_config(args.source),
        score=read_json(args.score),
        catalogs=read_json(args.catalogs),
    )
    signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
    worker.run(once=args.once)


if __name__ == "__main__":
    main()
