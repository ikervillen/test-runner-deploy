"""Worker de piloto, comprobación sin escrituras y coordinación de activación Docker."""

import argparse
import json
import os
import signal
import sqlite3
import sys
import threading
import time
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path

import portalocker

from .common import ConfigurationError, IntegrityError, canonical, now
from .config import Config
from .contracts import CONTRACTS

ORDER = (
    "SIP_MODELO",
    "SIP_DEFECTO",
    "SIP_DEFECTO_STATUS",
    "SIP_DEFECTO_AR",
    "SIP_MAQUINA_MOLDEO",
    "SIP_LINEA_ZF",
    "MP9_CAMBIO_MODELO",
    "CATALOGO_PRODUCCIONES",
    "LISTA_AR",
    "CONT_ZC",
    "LISTA_RECH",
    "LISTA_DEFECTOS",
    "ASIGNACION_PRODUCCION_RAMAL",
)
HISTORY = ("CONT_ZC", "LISTA_RECH", "LISTA_DEFECTOS")
PROTOCOL = 1
POLICY = "pilot-local-naive-v1"


def describe():
    from .associations import RULE

    return {
        "deployment_protocol": PROTOCOL,
        "control_payload_format": 3,
        "association_artifact_format": 2,
        "data_policy": POLICY,
        "contracts": {f: {"name": c.name, "schema": str(c.schema)} for f, c in CONTRACTS.items()},
        "assignment_rule": RULE,
    }


def atomic_json(path, value):
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def settings():
    channel = os.environ["INGESTA_CHANNEL"]
    instance = os.environ["INGESTA_INSTANCE"]
    if channel not in {"main", "develop"} or not instance.isalnum() or len(instance) != 32:
        raise ConfigurationError("Canal/identidad de almacenamiento inválidos")
    value = json.loads(os.environ["INGESTA_SETTINGS"])
    allowed = {"plant", "sources", "encoding", "line_by_origin", "line_code_by_origin"}
    if set(value) != allowed or not isinstance(value["sources"], dict):
        raise ConfigurationError("Configuración de piloto inesperada")
    base = Config(
        plant=value["plant"],
        input_root=Path("/input"),
        bronze_root=Path("/lake/bronze"),
        silver_root=Path("/lake/silver"),
        control_root=Path("/lake/control"),
        sources=tuple((o, (Path("/input") / p).resolve()) for o, p in value["sources"].items()),
        encoding=value["encoding"],
        environment="development",
        control_format=3,
        topology_enabled=True,
        # Amortize the expensive Windows input scan across three bounded batches.
        # Batches remain sequential and each retains its own durable receipt.
        mia_batches_per_turn=int(os.environ.get("INGESTA_MIA_BATCHES_PER_TURN", "3")),
        input_scan_interval_seconds=600,
        timestamp_policy="local_naive_test_v1",
        line_by_origin=tuple(value["line_by_origin"].items()),
        line_code_by_origin=tuple(value["line_code_by_origin"].items()),
    )
    base.validate()
    if any(not Path(p).is_relative_to(base.input_root) for _, p in base.sources):
        raise ConfigurationError("Origen fuera del montaje de entrada")
    return channel, instance, base


def marker():
    channel, instance, _ = settings()
    expected = {"channel": channel, "instance": instance, "protocol": PROTOCOL}
    if json.loads(Path("/lake/environment.json").read_text()) != expected:
        raise IntegrityError("Volumen de otro entorno o sin inicializar")
    return expected


def bootstrap():
    channel, instance, _ = settings()
    root = Path("/lake")
    path = root / "environment.json"
    if path.exists():
        marker()
        return {"status": "ALREADY_PREPARED"}
    if any(root.iterdir()):
        raise IntegrityError("No adoptar un volumen no vacío sin identidad")
    # Solo infraestructura explícita: no crea tablas ni controles SQLite.
    for name in ("bronze", "silver", "control", "runtime", "logs"):
        p = root / name
        p.mkdir()
        os.chown(p, 10001, 10001)
    atomic_json(path, {"channel": channel, "instance": instance, "protocol": PROTOCOL})
    return {"status": "PREPARED"}


@contextmanager
def control_read(path, allow_pending=False):
    """Preflight detenido: no crear SHM ni copiar controles grandes a RAM.

    immutable solo se usa SIN WAL. Con WAL nunca se ignoran sus commits: el worker
    propietario lo lee en modo RO sobre su montaje RW para poder reconstruir SHM.
    """
    wal = path.with_name(path.name + "-wal")
    has_wal = wal.exists() and wal.stat().st_size > 0
    if has_wal and not allow_pending:
        raise IntegrityError("Recuperar WAL con la release propietaria antes de actualizar")
    uri = path.as_uri() + "?mode=ro" + ("" if has_wal else "&immutable=1")
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        yield db


def check(allow_pending=False):
    """Solo lectura: binding, propiedad, esquema y confirmación técnica, sin leer CSV."""
    from .association_storage import STORES, AssociationStore

    identity = marker()
    _, _, base = settings()
    pending = []
    versions = {}
    for family in ORDER:
        cfg = replace(base, family=family)
        if not cfg.control_path.exists():
            if cfg.table_path.exists() and any(cfg.table_path.iterdir()):
                raise IntegrityError(f"Tabla sin control: {family}")
            versions[family] = None
            continue
        with control_read(cfg.control_path, allow_pending) as db:
            values = dict(db.execute("SELECT key,value FROM settings"))
            expected = canonical(
                {
                    "plant": cfg.plant,
                    "silver_root": str(cfg.silver_root),
                    "bronze_root": str(cfg.bronze_root),
                    "contract": cfg.contract.name,
                }
            )
            if values.get("binding") != expected:
                raise IntegrityError(f"Contrato/rutas incompatibles: {family}")
            batches = db.execute(
                "SELECT operation_id,state FROM batches "
                "WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
            ).fetchall()
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_operations'"
            ).fetchone():
                batches += db.execute(
                    "SELECT decision_id,state FROM review_operations WHERE state='PREPARED'"
                ).fetchall()
            pending.extend({"family": family, "operation": o, "state": s} for o, s in batches)
            cls = AssociationStore if family == "ASIGNACION_PRODUCCION_RAMAL" else STORES[family]
            table = cls(cfg, values["control_id"]).table()
            version = table.version() if table else None
            if table and table.to_pyarrow_dataset().schema != cfg.contract.schema:
                raise IntegrityError(f"Esquema incompatible: {family}")
            if not batches and values.get("last_version", "None") != str(version):
                raise IntegrityError(f"Commit sin confirmación: {family}")
            versions[family] = version
    if pending and not allow_pending:
        raise IntegrityError("Recuperar lotes pendientes con su release antes de cambiar imagen")
    return {
        "status": "COMPATIBLE",
        "identity": identity,
        "versions": versions,
        "pending": pending,
        "policy": POLICY,
    }


def token_paths():
    token = os.environ["INGESTA_ACTIVATION"]
    if len(token) != 32 or not token.isalnum():
        raise ConfigurationError("Token de activación inválido")
    root = Path("/lake/runtime")
    return token, root / (token + ".json"), root / (token + ".activate")


def health():
    marker()
    token, status, _ = token_paths()
    value = json.loads(status.read_text())
    if value["token"] != token or value["phase"] not in {"READY", "RUNNING", "IDLE"}:
        raise IntegrityError("Worker no preparado")
    # Heartbeat supervisado por el proceso, independiente de cuánto dure el lote.
    if time.time() - status.stat().st_mtime > 90:
        raise IntegrityError("Heartbeat atrasado")
    return value


def activate():
    health()
    token, _, path = token_paths()
    atomic_json(path, {"token": token})
    return {"status": "ACTIVATED", "token": token}


def service_reviews(base, stop, on_result):
    """Short decision turns between ingestion phases; same family locks, no scanner."""
    configured = os.environ.get("INGESTA_REVIEW_ROOT")
    if not configured:
        return
    root = Path(configured)
    for family in ORDER:
        if stop.is_set():
            break
        config = replace(base, family=family)
        if not config.control_path.exists():
            continue
        pending = any(
            not (root / "results" / family / path.name).exists()
            for path in (root / "commands" / family).glob("*.json")
            if not path.name.endswith(".progress.json")
        )
        if not pending:
            continue
        from .service import run

        started = time.monotonic()
        try:
            result = run(config, runtime_owner=True, reviews_only=True)
        except Exception as exc:
            result = {"status": "FAILED", "error": str(exc), "attempted": False}
        on_result(family, result, round(time.monotonic() - started, 3))


def worker(interval=600):
    if not 10 <= interval <= 86400:
        raise ConfigurationError("Intervalo fuera de rango")
    _, _, base = settings()
    workers = int(os.environ.get("INGESTA_WORKERS", "2"))
    if workers not in (1, 2):
        raise ConfigurationError("INGESTA_WORKERS debe ser 1 o 2")
    token, status_path, activation = token_paths()
    stop_path = status_path.with_suffix(".stop")
    stop = threading.Event()
    next_derived = 0.0
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    status = {"token": token, "phase": "READY", "time": now()}
    guard = threading.Lock()
    heartbeat_stop = threading.Event()
    source_discoveries = {}

    def heartbeat():
        while not heartbeat_stop.is_set():
            with guard:
                atomic_json(status_path, {**status, "time": now()})
            heartbeat_stop.wait(10)

    with portalocker.Lock(str(base.silver_root / ".runtime.lock"), mode="a", timeout=0):
        check(allow_pending=True)
        stop_path.unlink(missing_ok=True)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            while not activation.exists() and not stop.wait(0.2):
                pass
            if activation.exists() and json.loads(activation.read_text()) != {"token": token}:
                raise IntegrityError("Activación de otra instancia")
            def mirror(lane):
                try:
                    from .sources import mirror_exports

                    mirrored = mirror_exports(base, lane=lane)
                except Exception as exc:
                    mirrored = {
                        "copied": 0,
                        "unchanged": 0,
                        "skipped": 0,
                        "failed": 1,
                        "lane": lane,
                        "error": str(exc),
                    }
                print(
                    canonical({"event": "BRONZE_MIRROR", "time": now(), **mirrored}),
                    flush=True,
                )

            while not stop.is_set():
                cycle_started = time.monotonic()
                mirror("live")
                backlog = False
                failed = False
                next_scans = []
                association_confirmed = False

                def record(family, result, seconds, *, scan_deadlines=next_scans):
                    nonlocal backlog, failed, next_derived, association_confirmed
                    backlog = backlog or bool(result.get("remaining_versions", 0))
                    failed = failed or result["status"] == "FAILED"
                    if result.get("input_scan_performed") and result["status"] != "FAILED":
                        source_discoveries[family] = now()
                    if family == "ASIGNACION_PRODUCCION_RAMAL" and result.get("attempted", True):
                        association_confirmed = result["status"] != "FAILED"
                        next_derived = time.monotonic() + (
                            30 if result["status"] == "FAILED" else interval
                        )
                    if result.get("next_input_scan_at") is not None:
                        scan_deadlines.append(result["next_input_scan_at"])
                    event = {
                        "activation_token": token,
                        "channel": os.environ["INGESTA_CHANNEL"],
                        "instance": os.environ["INGESTA_INSTANCE"],
                        "time": now(),
                        "family": family,
                        "result": result,
                        "seconds": seconds,
                    }
                    with (Path("/lake/logs") / (now()[:10] + ".jsonl")).open(
                        "a", encoding="utf-8"
                    ) as stream:
                        stream.write(canonical(event) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    print(canonical(event), flush=True)

                if workers == 2:
                    from .common import history_yields
                    from .workers import PHASES, run_phase

                    def active(units):
                        with guard:
                            status.update(phase="RUNNING", family=None, workers=units)

                    for phase in PHASES:
                        if stop.is_set():
                            break
                        if (
                            phase == ("ASIGNACION_PRODUCCION_RAMAL",)
                            and time.monotonic() < next_derived
                        ):
                            from .supervision_export import deferred

                            record(
                                phase[0],
                                deferred(replace(base, family=phase[0]), reason="CADENCE"),
                                0,
                            )
                            continue
                        run_phase(base, phase, stop, stop_path, record, active, lane="live")
                        service_reviews(base, stop, record)
                    if not stop.is_set() and not history_yields():
                        mirror("history")
                    if not stop.is_set() and not history_yields():
                        run_phase(
                            base,
                            HISTORY,
                            stop,
                            stop_path,
                            record,
                            active,
                            limit=1,
                            lane="history",
                        )
                else:
                    from .common import history_yields
                    from .service import run

                    for family in ORDER:
                        if stop.is_set():
                            break
                        with guard:
                            status.update(phase="RUNNING", family=family)
                        started = time.monotonic()
                        try:
                            if (
                                family == "ASIGNACION_PRODUCCION_RAMAL"
                                and time.monotonic() < next_derived
                            ):
                                from .supervision_export import deferred

                                result = deferred(replace(base, family=family), reason="CADENCE")
                            else:
                                result = run(
                                    replace(base, family=family),
                                    runtime_owner=True,
                                    stop_requested=stop.is_set,
                                    lane="live",
                                )
                        except Exception as exc:
                            result = {"status": "FAILED", "error": str(exc)}
                        record(family, result, round(time.monotonic() - started, 3))
                        service_reviews(base, stop, record)
                    if not stop.is_set() and not history_yields():
                        mirror("history")
                    if not stop.is_set() and not history_yields():
                        for family in HISTORY:
                            if stop.is_set() or history_yields():
                                break
                            with guard:
                                status.update(phase="RUNNING", family=family)
                            started = time.monotonic()
                            try:
                                result = run(
                                    replace(base, family=family),
                                    runtime_owner=True,
                                    stop_requested=lambda: stop.is_set() or history_yields(),
                                    lane="history",
                                )
                            except Exception as exc:
                                result = {"status": "FAILED", "error": str(exc)}
                            record(family, result, round(time.monotonic() - started, 3))
                if not stop.is_set() and not failed:
                    # A full export waits until every family has drained its queue.
                    # Review coverage does not: a closed case is READY on the next
                    # cycle, while unfinished inputs stay pending and resume later.
                    if not backlog and association_confirmed:
                        from .gold_export import publish

                        exported = publish(base, source_discoveries)
                        export_family = "GOLD_EXPORT"
                    else:
                        from .gold_export import refresh

                        exported = refresh(base, source_discoveries)
                        export_family = "COVERAGE_REFRESH"
                    if exported["status"] != "DISABLED":
                        print(
                            canonical({"family": export_family, "time": now(), "result": exported}),
                            flush=True,
                        )
                with guard:
                    status.update(phase="IDLE", family=None)
                from .common import history_yields, next_live_tick, plant_now

                until_tick = (next_live_tick(plant_now()) - plant_now()).total_seconds()
                if history_yields():
                    delay = max(0, until_tick)
                else:
                    delay = (
                        (30 if failed else 1)
                        if backlog or failed
                        else max(1, interval - (time.monotonic() - cycle_started))
                    )
                    if next_scans and not backlog and not failed:
                        delay = max(1, min(delay, min(next_scans) - time.time()))
                stop.wait(delay)
        finally:
            heartbeat_stop.set()
            thread.join()
            atomic_json(status_path, {"token": token, "phase": "STOPPED", "time": now()})
    return {"status": "STOPPED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Runtime Docker de ingesta piloto")
    parser.add_argument(
        "command",
        choices=[
            "describe",
            "bootstrap",
            "identity",
            "check",
            "worker",
            "health",
            "activate",
            "self-test",
            "compact-control",
            "trial-control",
            "inventory-control",
            "rebuild-index",
            "compact-associations",
        ],
    )
    parser.add_argument("--allow-pending", action="store_true")
    parser.add_argument("--interval", type=int, default=600)
    parser.add_argument("--family", choices=ORDER)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--remove-backup", action="store_true")
    parser.add_argument("--reformulate", action="store_true")
    parser.add_argument("--trial-root", type=Path)
    parser.add_argument("--limit", type=int, default=32)
    args = parser.parse_args(argv)
    try:
        if args.command == "describe":
            result = describe()
        elif args.command == "self-test":
            from .runtime_selftest import self_test

            result = self_test()
        elif args.command == "check":
            result = check(args.allow_pending)
        elif args.command == "compact-control":
            from .maintenance import replace_control

            marker()
            if not args.family:
                raise ConfigurationError("compact-control requires --family")
            result = replace_control(
                replace(settings()[2], family=args.family),
                apply=args.apply,
                remove_backup=args.remove_backup,
                reformulate=args.reformulate,
            )
        elif args.command == "compact-associations":
            from .association_migration import execute, inventory

            marker()
            config = replace(settings()[2], family="ASIGNACION_PRODUCCION_RAMAL")
            result = (
                execute(config, limit=args.limit)
                if args.apply
                else inventory(config.control_root / "association-artifacts")
            )
        elif args.command in {"trial-control", "inventory-control", "rebuild-index"}:
            marker()
            if not args.family:
                raise ConfigurationError("Control inspection requires --family")
            config = replace(settings()[2], family=args.family)
            if args.command == "rebuild-index":
                from .inspection_index import execute

                result = execute(config)
            elif args.command == "inventory-control":
                from .control_inventory import inventory

                result = inventory(config.control_path)
            else:
                from .control_trial import trial

                if not args.trial_root:
                    raise ConfigurationError("trial-control requires --trial-root")
                result = trial(config, args.trial_root)
        elif args.command == "worker":
            result = worker(args.interval)
        else:
            result = {
                "bootstrap": bootstrap,
                "identity": marker,
                "health": health,
                "activate": activate,
            }[args.command]()
        print(canonical(result))
        return 0
    except Exception as exc:
        print(canonical({"status": "FAILED", "error": str(exc)}), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
