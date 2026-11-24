"""One Gold owner per physical lane, without shared live/history scheduling gates."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import sqlite3
import threading
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from time import monotonic

import portalocker
import pyarrow as pa

from .catalogs import load_independent_bundle
from .common import IntegrityError, atomic_json, canonical, digest, read_json
from .evidence import Evidence
from .independent_inputs import cutoff, export
from .pipeline import run
from .publications import Snapshot, commit_scope


class Worker:
    def __init__(self, pipelines, lane, source, *, score, catalogs):
        self.pipelines, self.lane, self.source = pipelines, lane, source
        self.storage = pipelines.lane(lane)
        self.policy = pipelines.policy
        self.score, self.catalogs = score, catalogs
        self.stop = threading.Event()
        self.root = self.storage.gold
        self.spill_root = self.root / "checkpoint" / "facts-work"
        self.reference = None
        self.acknowledged = set()

    def prepare_spill(self):
        self.spill_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = self.spill_root.resolve(strict=True)
        if root != self.spill_root.absolute() or not root.is_relative_to(self.root.resolve()):
            raise IntegrityError("Gold scratch directory escapes its storage")
        for path in root.iterdir():
            if (
                path.name.startswith("gold-facts-")
                and path.is_dir()
                and not path.is_symlink()
                and path.resolve() == path
            ):
                shutil.rmtree(path)

    @staticmethod
    def spill_budget():
        try:
            limit = int(os.environ.get("GOLD_SPILL_DB_BYTES", str(2 * 1024**3)))
        except ValueError as exc:
            raise IntegrityError("GOLD_SPILL_DB_BYTES must be an integer") from exc
        if limit <= 0:
            raise IntegrityError("GOLD_SPILL_DB_BYTES must be positive")
        return limit

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
        from .common import live_epoch

        if self.lane != "live" or live_epoch():
            return None
        root = self.pipelines.history.gold / "published"
        cache = self.root / "checkpoint" / "history-reference.json"
        try:
            identity = read_json(root / "current.json")["publication_id"]
            if self.reference is None or self.reference.publication_id != identity:
                self.reference = Snapshot.open(
                    root, publication_id=identity, require_gold_contract=True
                )
                atomic_json(cache, {"publication_id": identity})
        except (OSError, ValueError, KeyError):
            if self.reference is None and cache.exists():
                with suppress(OSError, ValueError):
                    self.reference = Snapshot.open(
                        root,
                        require_gold_contract=True,
                        publication_id=read_json(cache)["publication_id"],
                    )
        return self.reference

    def publish(self, evidence, reference, *, score, catalogs, cutoff_at, recovery=None):
        return run(
            self.root / "inputs",
            self.root / "published",
            confirmed_input=evidence,
            reference_snapshot=reference,
            cutoff=cutoff_at,
            bundle=catalogs,
            config=score,
            environment=self.source.environment,
            plant=self.source.plant,
            max_rows_per_source=self.policy.read_max_rows,
            max_total_rows=self.policy.read_max_rows,
            recovery=recovery,
            **self.publication_options(recovery),
            spill_root=self.spill_root,
            spill_db_bytes=self.spill_budget(),
        )

    def publication_options(self, recovery):
        if recovery is not None:
            state = recovery[1]
            return {
                "lane": state.get("lane", "all"),
                "current_slot_seconds": state.get("current_slot_seconds"),
                "mia_reference_mode": state.get("mia_reference_mode"),
            }
        if self.lane == "live":
            return {
                "lane": "live",
                "current_slot_seconds": self.policy.window_seconds,
                "mia_reference_mode": "live",
            }
        return {"mia_reference_mode": "history"}

    def recover_publication(self):
        output = self.root / "published"
        pending = []
        for path in sorted((output / "operations").glob("*.json")):
            if path in self.acknowledged:
                continue
            operation = read_json(path)
            if operation["state"] != "ACKNOWLEDGED":
                pending.append((path, operation))
            else:
                self.acknowledged.add(path)
        if not pending:
            return None
        if len(pending) != 1:
            raise IntegrityError("Gold live has multiple pending publications")
        path, operation = pending[0]
        identity = operation["operation_id"]
        if (output / "current.json").exists():
            actual = Snapshot.open(output, require_gold_contract=True)
            if actual.publication_id == identity:
                for ref in actual.manifest["tables"]:
                    actual.table(ref["name"])
                operation["state"] = "ACKNOWLEDGED"
                atomic_json(path, operation)
                return identity

        evidence = Evidence.open(self.root / "inputs", operation["input_publication_id"])
        cutoff_at = cutoff(evidence)
        matches = []
        for run_path in (output / "runs").glob("*.json"):
            state = read_json(run_path)
            if state.get("input_publication_id") != evidence.publication_id:
                continue
            config_path = output / "configs" / f"{state['config_id']}.json"
            if not config_path.is_file():
                continue
            settings = read_json(config_path)
            pinned = state.get("operation_id")
            if pinned is None:
                # Legacy independent runs published the complete snapshot with an empty scope.
                publication_config = digest(
                    [
                        settings["catalogs"],
                        settings["score"],
                        state["code_id"],
                        state["recalculation"],
                    ]
                )
                pinned = digest(
                    [
                        "gold-v1",
                        evidence.publication_id,
                        publication_config,
                        cutoff_at.isoformat(),
                        commit_scope(None, None),
                    ]
                )
            if pinned == identity:
                matches.append((state, settings))
        if len(matches) != 1:
            raise IntegrityError(f"Cannot identify the original Gold run for {identity}")
        state, settings = matches[0]
        vector = evidence.document.get("source_vector") or [None]
        reference_id = state.get("reference_publication_id", vector[-1])
        reference = (
            Snapshot.open(
                self.pipelines.history.gold / "published",
                publication_id=reference_id,
                require_gold_contract=True,
            )
            if reference_id
            else None
        )
        result = self.publish(
            evidence,
            reference,
            score=settings["score"],
            catalogs=settings["catalogs"],
            cutoff_at=datetime.fromisoformat(state.get("cutoff", cutoff_at.isoformat())),
            recovery=(identity, state),
        )
        if result.get("publication_id") != identity:
            raise IntegrityError(f"Gold did not complete recovery of {identity}")
        return identity

    def cycle(self):
        started = monotonic()
        recovered = self.recover_publication() if self.lane == "live" else None
        began_export = monotonic()
        evidence = export(self.pipelines, self.lane, self.source)
        began_publish = monotonic()
        result = self.publish(
            evidence,
            self.historical_reference(),
            score=self.score,
            catalogs=self.catalogs,
            cutoff_at=cutoff(evidence),
        )
        if recovered is not None:
            result["recovered_publication_id"] = recovered
            result["recovery_seconds"] = round(began_export - started, 3)
        sources = evidence.document.get("sources") or []
        result.update(
            silver_publication_id=sources[0] if sources else None,
            spill_directory=str(self.spill_root),
            silver_read=evidence.document.get("silver_read"),
            export_seconds=round(began_publish - began_export, 3),
            calculation_and_publish_seconds=round(monotonic() - began_publish, 3),
        )
        return result

    def health(self):
        marker = read_json(self.root / "checkpoint" / "identity.json")
        if marker["lane"] != self.lane:
            raise IntegrityError("Gold health belongs to another lane")
        try:
            with portalocker.Lock(self.root / "checkpoint" / "worker.lock", timeout=0):
                raise IntegrityError("Gold owner is not running")
        except portalocker.exceptions.LockException:
            path = self.root / "checkpoint" / "health.json"
            result = read_json(path) if path.exists() else {"status": "STARTING", "lane": self.lane}
            if result["status"] == "FAILED":
                raise IntegrityError("Gold owner reports a failed cycle") from None
            return result

    def run(self, *, once=False):
        from .common import utc_now

        self.prepare()
        with portalocker.Lock(self.root / "checkpoint" / "worker.lock", timeout=0):
            self.prepare_spill()
            while not self.stop.is_set():
                try:
                    result = self.cycle()
                except Exception as exc:
                    result = {
                        "status": "FAILED",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                    if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                        result.update(
                            spill_directory=str(self.spill_root),
                            spill_disk_free_bytes=shutil.disk_usage(self.spill_root).free,
                        )
                result.update(lane=self.lane, time=utc_now())
                atomic_json(self.root / "checkpoint" / "health.json", result)
                print(canonical(result).decode("utf-8"), flush=True)
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
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args(argv)
    worker = Worker(
        IndependentPipelines.load(args.pipelines),
        args.lane,
        load_config(args.source, require_inputs=False),
        score=read_json(args.score),
        catalogs=load_independent_bundle(args.catalogs),
    )
    signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
    if args.health:
        print(canonical(worker.health()).decode("utf-8"))
    else:
        # SQLite sorts can spill files independently of facts.sqlite and its WAL.
        os.environ["SQLITE_TMPDIR"] = str(worker.spill_root)
        worker.run(once=args.once)


if __name__ == "__main__":
    main()
