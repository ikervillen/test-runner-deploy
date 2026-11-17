"""Bounded independent processes; one writer per table and cooperative shutdown."""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from .common import ConfigurationError
from .config import Config

HEAVY = {"LISTA_RECH", "LISTA_DEFECTOS", "ASIGNACION_PRODUCCION_RAMAL"}
PHASES = (
    ("SIP_MODELO", "SIP_DEFECTO", "SIP_DEFECTO_STATUS", "SIP_DEFECTO_AR", "SIP_MAQUINA_MOLDEO"),
    ("SIP_LINEA_ZF", "MP9_CAMBIO_MODELO"),
    ("CATALOGO_PRODUCCIONES",),
    ("LISTA_RECH", "CONT_ZC", "LISTA_AR", "LISTA_DEFECTOS"),
    ("ASIGNACION_PRODUCCION_RAMAL",),
)


def decode_config(text):
    values = json.loads(text)
    for name in ("input_root", "bronze_root", "silver_root", "control_root"):
        values[name] = Path(values[name])
    if values.get("association_decisions") is not None:
        values["association_decisions"] = Path(values["association_decisions"])
    if values.get("intake_path") is not None:
        values["intake_path"] = Path(values["intake_path"])
    if "intake_ids" in values:
        values["intake_ids"] = tuple(values["intake_ids"])
    if values.get("runtime_root") is not None:
        values["runtime_root"] = Path(values["runtime_root"])
    for name in ("archive_root", "archive_manifest"):
        if values.get(name) is not None:
            values[name] = Path(values[name])
    if values.get("processing_window") is not None:
        values["processing_window"] = tuple(values["processing_window"])
    values["sources"] = tuple((o, Path(p)) for o, p in values["sources"])
    for name in ("line_by_origin", "line_code_by_origin"):
        values[name] = tuple(tuple(pair) for pair in values.get(name, ()))
    config = Config(**values)
    config.validate()
    return config


def execute(config, result_path, stop_path, lane="all"):
    import pyarrow as pa

    from .runtime import atomic_json
    from .service import run

    root = (config.runtime_root or config.control_root.parent / "runtime").resolve()
    if any(p.resolve().parent != root for p in (result_path, stop_path)):
        raise ConfigurationError(
            "Unit result/stop paths must be in the controlled runtime directory"
        )
    pa.set_cpu_count(config.arrow_threads)
    pa.set_io_thread_count(config.arrow_threads)
    started = time.monotonic()
    try:
        result = run(
            config,
            runtime_owner=True,
            stop_requested=stop_path.exists,
            lane=lane,
            reviews_only=config.reviews_only,
        )
        if config.reviews_only and result.get("status") == "REVIEW_WAITING_RECOVERY":
            result = run(config, runtime_owner=True, stop_requested=stop_path.exists, lane=lane)
    except Exception as exc:
        result = {"status": "FAILED", "error": str(exc), "error_type": type(exc).__name__}
    atomic_json(
        result_path,
        {
            "family": config.family,
            "result": result,
            "seconds": round(time.monotonic() - started, 3),
        },
    )
    return 3 if result["status"] == "FAILED" else 0


def run_phase(
    base,
    families,
    stop,
    stop_path,
    on_result,
    on_active=lambda _: None,
    *,
    limit=2,
    lane="all",
    configs=None,
):
    if (
        type(limit) is not int
        or not 1 <= limit <= base.max_parallel_workers
        or (len(families) != len(set(families)))
    ):
        raise ConfigurationError("Invalid process budget or duplicate table in phase")
    root = base.runtime_root or base.control_root.parent / "runtime"
    if stop_path.resolve().parent != root.resolve():
        raise ConfigurationError("Stop path must be in the controlled runtime directory")
    root.mkdir(parents=True, exist_ok=True)
    pending, active = list(families), {}
    environment = {
        **os.environ,
        "RAYON_NUM_THREADS": str(base.arrow_threads),
        "TOKIO_WORKER_THREADS": str(base.arrow_threads),
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
    }
    try:
        while pending or active:
            if stop.is_set():
                stop_path.touch(exist_ok=True)
                pending.clear()
            while pending and len(active) < limit and not stop.is_set():
                heavy_running = any(f in HEAVY for f, _, _ in active.values())
                index = next(
                    (i for i, f in enumerate(pending) if f not in HEAVY or not heavy_running), None
                )
                if index is None:
                    break
                family = pending.pop(index)
                cfg = configs[family] if configs is not None else replace(base, family=family)
                result_path = root / ("unit-" + uuid.uuid4().hex + ".json")
                command = [
                    sys.executable,
                    "-m",
                    "ingesta_delta_lake.workers",
                    "--config-json",
                    json.dumps(asdict(cfg), default=str),
                    "--result",
                    str(result_path),
                    "--stop",
                    str(stop_path),
                    "--lane",
                    lane,
                ]
                # Inherit stdout/stderr: a full pipe must never block a writer.
                process = subprocess.Popen(command, env=environment)
                active[process] = family, result_path, time.monotonic()
                on_active([{"family": f, "pid": p.pid} for p, (f, _, _) in active.items()])
            for process, (family, path, started) in list(active.items()):
                code = process.poll()
                if code is None:
                    continue
                try:
                    event = json.loads(path.read_text(encoding="utf-8"))
                    if (
                        event["family"] != family
                        or not isinstance(event["result"], dict)
                        or not isinstance(event["result"]["status"], str)
                        or not isinstance(event["seconds"], (int, float))
                        or event["seconds"] < 0
                    ):
                        raise ValueError("Invalid unit result")
                except (OSError, ValueError, KeyError, TypeError):
                    event = {
                        "family": family,
                        "seconds": round(time.monotonic() - started, 3),
                        "result": {
                            "status": "FAILED",
                            "exit_code": code,
                            "error": "Child exited without a valid durable result",
                        },
                    }
                if code and event["result"]["status"] != "FAILED":
                    event["result"] = {
                        "status": "FAILED",
                        "exit_code": code,
                        "error": "Child exit disagrees with its result",
                    }
                on_result(family, event["result"], event["seconds"])
                path.unlink(missing_ok=True)
                del active[process]
                on_active([{"family": f, "pid": p.pid} for p, (f, _, _) in active.items()])
            if active:
                time.sleep(0.05)
    finally:
        if active:
            stop_path.touch(exist_ok=True)
            for process in active:
                process.wait()  # Finish the unit; never force-kill an uncertain commit.


def main(argv=None):
    parser = argparse.ArgumentParser(description="Controlled single-source subprocess")
    parser.add_argument("--config-json", required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--stop", type=Path, required=True)
    parser.add_argument("--lane", choices=("live", "history", "all"), default="all")
    args = parser.parse_args(argv)
    return execute(decode_config(args.config_json), args.result, args.stop, args.lane)


if __name__ == "__main__":
    sys.exit(main())
