"""Explicit local commands: no deployment, API connection or production defaults."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from .common import IntegrityError, read_json
from .failures import PERMANENT, exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Confirmed Silver → Gold, isolated local validation"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze-legacy")
    freeze.add_argument("--data-root", type=Path, required=True)
    freeze.add_argument("--config-root", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--production", action="append", required=True)
    verify = commands.add_parser("verify-legacy")
    verify.add_argument("--reference", type=Path, required=True)
    parity = commands.add_parser("verify-mia-parity")
    parity.add_argument("--reference", type=Path, required=True)
    for name in ("build", "watch", "publish", "coverage"):
        build = commands.add_parser(name)
        build.add_argument("--settings", type=Path, required=True)
        if name == "build":
            build.add_argument("--recalculate-closed", action="append", default=[])
            build.add_argument("--reason")
            build.add_argument(
                "--cutoff", required=True, help="Local ceiling for every production, ISO format"
            )
        if name in ("publish", "coverage"):
            build.add_argument(
                "--at",
                required=True,
                help="Scheduled local wall-clock moment, ISO format; the ceiling of the attempt",
            )
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--root", type=Path, required=True)
    commands.add_parser("contracts")
    for name in ("backup", "restore"):
        maintenance = commands.add_parser(name)
        maintenance.add_argument("--source", type=Path, required=True)
        maintenance.add_argument("--destination", type=Path, required=True)
    for name in ("verify-backup", "inventory"):
        maintenance = commands.add_parser(name)
        maintenance.add_argument("--root", type=Path, required=True)
    restore_drill = commands.add_parser("restore-drill")
    restore_drill.add_argument("--root", type=Path, required=True)
    restore_drill.add_argument("--scratch", type=Path, required=True)
    maintain = commands.add_parser("maintain")
    maintain.add_argument("--root", type=Path, required=True)
    maintain.add_argument(
        "--keep-days",
        type=int,
        required=True,
        help="Publications younger than this stay fully readable for rollback",
    )
    check = commands.add_parser("validate-gold")
    check.add_argument("--root", type=Path, required=True)
    check.add_argument("--max-rows", type=int, default=250_000)
    segments = commands.add_parser("import-segments")
    segments.add_argument("--workbook", type=Path, required=True)
    segments.add_argument("--bundle", type=Path, required=True)
    segments.add_argument("--output", type=Path, required=True)
    segments.add_argument("--common-profile")
    replay = commands.add_parser("local-replay")
    replay.add_argument("--ingestion-config", type=Path, required=True)
    replay.add_argument("--publication-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "contracts":
            from .contracts import describe

            result = describe()
        elif args.command in {"backup", "restore", "verify-backup"}:
            from . import backup

            result = (
                backup.create(args.source, args.destination)
                if args.command == "backup"
                else backup.restore(args.source, args.destination)
                if args.command == "restore"
                else backup.verify(args.root)
            )
        elif args.command == "restore-drill":
            from .backup import drill

            result = drill(args.root, args.scratch)
        elif args.command == "maintain":
            from .maintenance import maintain

            result = maintain(args.root, keep_days=args.keep_days)
        elif args.command == "inventory":
            from .publications import Snapshot

            snap = Snapshot.open(args.root, require_gold_contract=True)
            result = {"publication_id": snap.publication_id, "tables": {}}
            for ref in snap.manifest["tables"]:
                table = snap.table(ref["name"])
                files = [Path(p) for p in table.file_uris()]
                result["tables"][ref["name"]] = {
                    "delta_version": ref["delta_version"],
                    "files": len(files),
                    "bytes": sum(p.stat().st_size for p in files),
                    "partition_columns": table.metadata().partition_columns,
                }
        elif args.command == "validate-gold":
            from .contracts import CONTRACTS, contract_id, validate
            from .publications import Snapshot

            if not 1 <= args.max_rows <= 1_000_000:
                raise IntegrityError("Invalid explicit row budget")
            snap = Snapshot.open(args.root, require_gold_contract=True)
            tables = {name: snap.rows(name, max_rows=args.max_rows) for name in CONTRACTS}
            validate(tables, complete=True)
            result = {
                "status": "VALID",
                "publication_id": snap.publication_id,
                "contract_id": contract_id(),
                "rows": {k: len(v) for k, v in tables.items()},
            }
        elif args.command == "import-segments":
            from .segments import save_import

            result = save_import(
                args.workbook,
                read_json(args.bundle),
                args.output,
                common_profile_id=args.common_profile,
            )
        elif args.command == "local-replay":
            from .local_replay import replay

            result = replay(args.ingestion_config, args.publication_root)
        elif args.command == "freeze-legacy":
            from .legacy import freeze

            result = freeze(args.data_root, args.config_root, args.output, args.production)
        elif args.command == "verify-legacy":
            from .legacy import verify

            result = verify(args.reference)
        elif args.command == "verify-mia-parity":
            from .parity import compare

            result = compare(args.reference)
            if result["status"] != "MATCH":
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                return 2
        elif args.command == "inspect":
            from .publications import Snapshot

            snap = Snapshot.open(args.root)
            rows = snap.rows("publication_scope_status")
            result = {
                "publication_id": snap.publication_id,
                "tables": {r["name"]: r["delta_version"] for r in snap.manifest["tables"]},
                "scope_states": dict(Counter(r["estado"] for r in rows)),
                "pending_reasons": dict(
                    Counter(r["reason_code"] for r in rows if r["estado"] != "READY")
                ),
            }
        else:
            from .pipeline import run

            settings = read_json(args.settings)
            required = {
                "schema_id",
                "input_root",
                "output_root",
                "environment",
                "plant",
                "score_config",
                "catalog_bundle",
                "poll_seconds",
                "max_rows_per_source",
                "max_total_rows",
            }
            if (
                set(settings) - {"source_scope_start"} != required
                or settings["schema_id"] != "gold-settings-v1"
            ):
                raise IntegrityError("Settings must match gold-settings-v1 exactly")
            scope = None
            if "source_scope_start" in settings:
                from .live_scope import LiveScope

                try:
                    scope = LiveScope(datetime.fromisoformat(settings["source_scope_start"]))
                except (TypeError, ValueError) as exc:
                    raise IntegrityError("Gold source scope must be a local ISO datetime") from exc
            if not 1 <= settings["poll_seconds"] <= 3600:
                raise IntegrityError("Invalid poll interval")
            for name in ("max_rows_per_source", "max_total_rows"):
                if type(settings[name]) is not int or not 1 <= settings[name] <= 1_000_000:
                    raise IntegrityError("Invalid explicit row budget")

            def configured(name):
                path = Path(settings[name])
                return (
                    path.resolve()
                    if path.is_absolute()
                    else (args.settings.parent / path).resolve()
                )

            def execute(cutoff):
                return run(
                    configured("input_root"),
                    configured("output_root"),
                    bundle=read_json(configured("catalog_bundle")),
                    config=read_json(configured("score_config")),
                    cutoff=cutoff,
                    environment=settings["environment"],
                    plant=settings["plant"],
                    max_rows_per_source=settings["max_rows_per_source"],
                    max_total_rows=settings["max_total_rows"],
                    recalculate_closed_ids=getattr(args, "recalculate_closed", []),
                    recalculation_reason=getattr(args, "reason", None),
                    scope=scope,
                )

            if args.command == "build":
                started = time.perf_counter()
                cutoff = datetime.fromisoformat(args.cutoff)
                if cutoff.tzinfo is not None:
                    raise IntegrityError(
                        "Use a local industrial cutoff; UTC conversion is not implicit"
                    )
                result = execute(cutoff)
                from .resources import peak_memory

                result["resources"] = {
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    **peak_memory(),
                }
            else:
                from .schedule import attempt_cutoff, require_continuous_source

                require_continuous_source(configured("input_root"))
                if args.command == "coverage":
                    from .readiness import report

                    result = report(
                        configured("input_root"),
                        configured("output_root"),
                        attempt_cutoff(datetime.fromisoformat(args.at)),
                        scope=scope,
                    )
                elif args.command == "publish":
                    result = execute(attempt_cutoff(datetime.fromisoformat(args.at)))
                else:
                    while True:
                        try:
                            result = execute(attempt_cutoff(datetime.now()))
                            if result["status"] != "UNCHANGED":
                                print(json.dumps(result, ensure_ascii=False), flush=True)
                        except Exception as exc:
                            print(
                                json.dumps({"status": "FAILED", "error": str(exc)}),
                                file=sys.stderr,
                                flush=True,
                            )
                        time.sleep(settings["poll_seconds"])
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        code = exit_code(exc)
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "retryable": code not in PERMANENT,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return code
