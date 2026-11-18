"""Producer-side bridge for an isolated, confirmed ingestion snapshot.

Run with the ingestion Python (3.12) after its writers finish, not from a Gold
consumer. This adapter checks ingestion controls and exports bounded attribution
data; normal Gold reads only its immutable manifest and Delta tables.
"""

from __future__ import annotations

import csv
import io
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pyarrow as pa
from deltalake import CommitProperties, DeltaTable, write_deltalake

from .common import IntegrityError, digest
from .publications import RETENTION, Snapshot, promote, reference

FAMILIES = (
    "SIP_MODELO",
    "SIP_DEFECTO",
    "SIP_DEFECTO_STATUS",
    "SIP_DEFECTO_AR",
    "SIP_MAQUINA_MOLDEO",
    "SIP_LINEA_ZF",
    "MP9_CAMBIO_MODELO",
    "CATALOGO_PRODUCCIONES",
    "LISTA_AR",
    "LISTA_DEFECTOS",
    "LISTA_RECH",
    "CONT_ZC",
)


def _time(value):
    if value.strip() in {"", "NULL"}:
        return None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise IntegrityError("Unsupported auxiliary Fenix timestamp")


def auxiliary_captures(root, sources):
    for origin, directory in sources:
        for family in (
            "MP9_PARAM_MODELO",
            "SIP_NUMERO_GOTAS_SIMULTANEAS",
            "SIP_MAQUINA_MOLDEO",
            "MP9_LISTA_RECH_ZC",
        ):
            for path in sorted(directory.glob(f"*_{family}.csv")):
                before = path.read_bytes()
                target = (
                    root / "bronze-extensions" / origin / family / (digest(before.hex()) + ".csv")
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.write_bytes(before)
                if target.read_bytes() != before or path.read_bytes() != before:
                    raise IntegrityError("Auxiliary source changed during stable capture")
                yield origin, family, before


def auxiliary(
    root: Path,
    sources: tuple[tuple[str, Path], ...],
    *,
    captures=None,
    initial=None,
    encoding="cp1252",
) -> dict[str, pa.Table]:
    """Minimal approved extra fields, captured once, with contradictions rejected.

    This local replay producer does not register these sources in the running
    server worker. That integration belongs to the later server validation step.
    """
    # Keys differ per family (tuples or scalars) and identify one auxiliary record.
    initial = initial or {}
    parameters = {(r["origen_fenix"], r["id"]): r for r in initial.get("production_parameters", [])}
    drops = {r["id_gotas"]: r for r in initial.get("gobs_dictionary", [])}
    machines = {
        (r["id_maquina_fenix"], r["vigente_desde"]): r for r in initial.get("machine_gobs", [])
    }
    hot = {(r["origen_fenix"], r["id"]): r for r in initial.get("hot_rejection_evidence", [])}
    maps = {
        "MP9_PARAM_MODELO": parameters,
        "SIP_NUMERO_GOTAS_SIMULTANEAS": drops,
        "SIP_MAQUINA_MOLDEO": machines,
        "MP9_LISTA_RECH_ZC": hot,
    }
    for origin, family, before in (
        captures if captures is not None else auxiliary_captures(root, sources)
    ):
        accepted = maps[family]
        for raw in csv.reader(io.StringIO(before.decode(encoding)), delimiter=";"):
            if not raw or not any(raw):
                continue
            if raw[-1] == "":
                raw = raw[:-1]
            if family == "MP9_PARAM_MODELO":
                if len(raw) != 9:
                    raise IntegrityError("Unexpected production parameter schema")
                row = {
                    "origen_fenix": origin,
                    "id": int(raw[0]),
                    "id_cambio_modelo": int(raw[1]),
                    "fecha_on": _time(raw[2]),
                    "fecha_off": _time(raw[3]),
                    "num_secciones": int(raw[4]),
                }
                key = (origin, row["id"])
            elif family == "SIP_NUMERO_GOTAS_SIMULTANEAS":
                if len(raw) != 3:
                    raise IntegrityError("Unexpected gobs dictionary schema")
                row = {"id_gotas": int(raw[0]), "valor": int(raw[2])}
                key = row["id_gotas"]
            elif family == "SIP_MAQUINA_MOLDEO":
                if len(raw) != 10:
                    raise IntegrityError("Unexpected machine configuration schema")
                row = {
                    "id_maquina_fenix": int(raw[0]),
                    "id_gotas": int(raw[5]),
                    "vigente_desde": _time(raw[7]),
                    "vigente_hasta": _time(raw[8]),
                }
                key = (row["id_maquina_fenix"], row["vigente_desde"])
            else:
                if len(raw) != 11:
                    raise IntegrityError("Unexpected hot rejection schema")

                def optional_int(token):
                    return None if token in {"NULL", ""} else int(token)

                row = {
                    "origen_fenix": origin,
                    "id": int(raw[0]),
                    "fecha": _time(raw[1]),
                    "molde": optional_int(raw[2]),
                    "cavidad": None if raw[7] in {"NULL", ""} else raw[7],
                    "seccion": optional_int(raw[9]),
                    "id_maquina_fenix": int(raw[10]),
                    "rechazos": optional_int(raw[5]),
                }
                key = (origin, row["id"])
            previous = accepted.get(key)
            if previous and previous != row:
                fields = {k for k in row if previous[k] != row[k]}
                if fields in ({"fecha_off"}, {"vigente_hasta"}) and all(
                    previous[k] is None for k in fields
                ):
                    accepted[key] = row
                elif fields in ({"fecha_off"}, {"vigente_hasta"}) and all(
                    row[k] is None for k in fields
                ):
                    continue  # Old re-export cannot reopen a closed episode.
                else:
                    raise IntegrityError(f"Contradictory auxiliary {family} key {key}")
            else:
                accepted[key] = row

    def table(values, fields):
        return pa.Table.from_pylist(list(values.values()), schema=pa.schema(fields))

    return {
        "production_parameters": table(
            parameters,
            [
                ("origen_fenix", pa.string()),
                ("id", pa.int64()),
                ("id_cambio_modelo", pa.int64()),
                ("fecha_on", pa.timestamp("us")),
                ("fecha_off", pa.timestamp("us")),
                ("num_secciones", pa.int64()),
            ],
        ),
        "gobs_dictionary": table(drops, [("id_gotas", pa.int64()), ("valor", pa.int64())]),
        "machine_gobs": table(
            machines,
            [
                ("id_maquina_fenix", pa.int64()),
                ("id_gotas", pa.int64()),
                ("vigente_desde", pa.timestamp("us")),
                ("vigente_hasta", pa.timestamp("us")),
            ],
        ),
        "hot_rejection_evidence": table(
            hot,
            [
                ("origen_fenix", pa.string()),
                ("id", pa.int64()),
                ("fecha", pa.timestamp("us")),
                ("molde", pa.int64()),
                ("cavidad", pa.string()),
                ("seccion", pa.int64()),
                ("id_maquina_fenix", pa.int64()),
                ("rechazos", pa.int64()),
            ],
        ),
    }


def export_confirmed(
    config,
    root: Path,
    *,
    max_ledger_rows: int | None = 250_000,
    source_mode="offline_snapshot",
    discoveries=None,
) -> dict:
    import portalocker

    root = root.resolve(strict=True)
    if source_mode not in {"offline_snapshot", "continuous"}:
        raise IntegrityError("Unsupported producer mode")
    if source_mode == "continuous" and discoveries is None:
        raise IntegrityError("Continuous producer requires actual discovery observations")
    with portalocker.Lock(root / "gold-export.lock", timeout=0):
        return _export_confirmed(
            config,
            root,
            max_ledger_rows=max_ledger_rows,
            source_mode=source_mode,
            discoveries=discoveries or {},
        )


HORIZON_FAMILIES = frozenset({"LISTA_DEFECTOS", "LISTA_RECH", "CONT_ZC"})


def _later(left, right):
    values = []
    for item in (left, right):
        if item is None or item == "":
            continue
        values.append(item if isinstance(item, datetime) else datetime.fromisoformat(str(item)))
    return max(values) if values else None


def _apply_horizon(row: dict, family: str, origin: str, observed: dict | None) -> None:
    """History cursor. An accepted backlog row moves only its own family."""
    if family not in HORIZON_FAMILIES:
        return
    later = _later(row.get("covered_until"), (observed or {}).get(origin))
    if later is not None:
        row["covered_until"] = later


def _apply_live(row: dict, family: str, origin: str, live: dict | None) -> None:
    """Live cursor. The newest ten-minute slot, independent of the backlog fecha."""
    if family not in HORIZON_FAMILIES:
        return
    stamp = (live or {}).get(origin)
    if stamp is not None:
        row["live_until"] = stamp


def refresh_review_coverage(config, root: Path, *, discoveries=None) -> dict:
    """Republish READY/REVIEW from the live controls without rewriting Silver tables.

    A closed case becomes visible on the next cycle. Each operational family
    advances to the observations it has already accepted, even when another
    family still has a backlog. LISTA_AR does not pull the cutoff.
    """
    import portalocker
    from ingesta_delta_lake.association_storage import source_snapshot

    from .publications import envelope

    root = root.resolve(strict=True)
    # Discovery stamps stay with the full export. Accepted observation times move
    # with the family that wrote them, without waiting for another family's queue.
    _ = discoveries
    if not (root / "current.json").exists():
        return {"status": "NO_PUBLICATION"}
    with portalocker.Lock(root / "gold-export.lock", timeout=0):
        pointer, manifest = envelope(root)
        previous = {(row.get("source"), row.get("origin")): row for row in manifest["coverage"]}
        coverage = []
        for family in FAMILIES:
            _, cases, meta, _ = source_snapshot(config, family, read=False)
            for origin, _path in config.sources:
                relevant = [
                    case
                    for case in cases
                    if not case["payload"].get("origen_fenix")
                    or case["payload"].get("origen_fenix") == origin
                ]
                prior = previous.get((family, origin), {})
                row = {
                    "source": family,
                    "origin": origin,
                    "estado": "READY" if meta["version"] is not None else "WAITING_INPUT",
                    "reason_code": "MANUAL_REVIEW" if relevant else None,
                    "quality_hash": meta["quality_hash"],
                    "last_successful_discovery": prior.get("last_successful_discovery"),
                }
                for field in ("covered_from", "covered_until"):
                    if prior.get(field) is not None:
                        row[field] = prior[field]
                _apply_horizon(row, family, origin, meta.get("observed_until"))
                _apply_live(row, family, origin, meta.get("live_until"))
                coverage.append(row)
        if coverage == manifest["coverage"]:
            return {"status": "UNCHANGED", "publication_id": pointer["publication_id"]}
        publication_id = digest([pointer["publication_id"], coverage])
        result = promote(
            root,
            manifest["tables"],
            environment=manifest["environment"],
            plant=manifest["plant"],
            input_id=manifest.get("input_publication_id"),
            operation_id=publication_id,
            generation=pointer["generation"] + 1,
            previous=pointer["publication_id"],
            coverage=coverage,
            dependencies=manifest.get("dependencies") or [],
            source_mode=manifest.get("source_mode", "confirmed"),
            contract=manifest.get("contract"),
        )
        return {"status": "COVERAGE_REFRESHED", "publication_id": result["publication_id"]}


def _export_confirmed(config, root, *, max_ledger_rows, source_mode, discoveries):
    """Called only at the isolated producer's confirmed boundary; no live deployment."""
    from ingesta_delta_lake.association_storage import (
        AssociationControl,
        AssociationStore,
        source_snapshot,
    )

    from .artifact_stream import items

    root = root.resolve()
    refs, coverage, versions, dependencies = [], [], {}, []
    observation_rows = 0
    source_vector = {}
    for family in FAMILIES:
        observed_ranges = {}
        _, cases, meta, extra = source_snapshot(config, family, read=False)
        source_vector[family] = meta
        cfg = replace(config, family=family)
        versions[family] = meta["version"]
        if meta["version"] is not None:
            table = DeltaTable(str(cfg.table_path), version=meta["version"])
            if family in {"LISTA_DEFECTOS", "LISTA_RECH", "LISTA_AR", "CONT_ZC"}:
                observation_rows += table.to_pyarrow_dataset().count_rows()
            if family in {"LISTA_DEFECTOS", "LISTA_RECH", "CONT_ZC"}:
                for batch in (
                    table.to_pyarrow_dataset()
                    .scanner(columns=["origen_fenix", "FECHA"], batch_size=8192, use_threads=False)
                    .to_batches()
                ):
                    for source_origin, instant in zip(
                        batch.column(0).to_pylist(), batch.column(1).to_pylist(), strict=True
                    ):
                        left, right = observed_ranges.get(source_origin, (instant, instant))
                        observed_ranges[source_origin] = (min(left, instant), max(right, instant))
            operation = accepted_operation(cfg.table_path, meta["version"])
            refs.append(
                reference(
                    root,
                    family,
                    cfg.table_path.relative_to(root).as_posix(),
                    meta["version"],
                    operation,
                    operation_field="ingesta_operation_id",
                )
            )
        if extra.get("manifest"):
            dependencies.append(
                {"input_versions": {k: v["version"] for k, v in extra["manifest"].items()}}
            )
        for origin, _ in config.sources:
            # Unlocated cases must not be hidden by claiming another source/line is clear.
            relevant = [
                c
                for c in cases
                if not c["payload"].get("origen_fenix")
                or c["payload"].get("origen_fenix") == origin
            ]
            coverage.append(
                {
                    "source": family,
                    "origin": origin,
                    "estado": "READY" if meta["version"] is not None else "WAITING_INPUT",
                    "reason_code": "MANUAL_REVIEW" if relevant else None,
                    "quality_hash": meta["quality_hash"],
                    "last_successful_discovery": discoveries.get(family),
                }
            )
            if origin in observed_ranges:
                from datetime import timedelta

                coverage[-1]["covered_from"] = observed_ranges[origin][0] - timedelta(minutes=10)
                coverage[-1]["covered_until"] = observed_ranges[origin][1]
            _apply_horizon(coverage[-1], family, origin, meta.get("observed_until"))
            _apply_live(coverage[-1], family, origin, meta.get("live_until"))
    if max_ledger_rows is not None and observation_rows > max_ledger_rows:
        raise IntegrityError("Local producer budget exceeded before loading attribution artifacts")
    cfg = replace(config, family="ASIGNACION_PRODUCCION_RAMAL")
    with closing(AssociationControl(cfg)) as control:
        if control.pending():
            raise IntegrityError("Recover producer associations before exporting")
        latest = control.latest("full")
        if latest is None:
            raise IntegrityError("No confirmed full attribution publication")
        import json

        publication = json.loads(latest["payload_json"])
        if any(versions.get(k) != d["version"] for k, d in publication["manifest"].items()):
            raise IntegrityError(
                "Attribution publication is not compatible with confirmed source vector"
            )
        ledger = items(cfg, publication["artifacts"]["ledger"], "item", max_items=max_ledger_rows)
        descriptor = publication["artifacts"]["bundle"]
        registry = dict(items(cfg, descriptor, "registry", mapping=True, max_items=100_000))
        ar_rows = list(items(cfg, descriptor, "sources.LISTA_AR.item", max_items=250_000))
        types = list(items(cfg, descriptor, "sources.SIP_DEFECTO_AR.item", max_items=10_000))
        assoc = AssociationStore(cfg, control.control_id).table()
        if assoc is not None:
            refs.append(
                reference(
                    root,
                    "ASIGNACION_PRODUCCION_RAMAL",
                    cfg.table_path.relative_to(root).as_posix(),
                    assoc.version(),
                    assoc.history(1)[0]["ingesta_operation_id"],
                    operation_field="ingesta_operation_id",
                )
            )
    auxiliary_tables = auxiliary(root, config.sources)
    auxiliary_tables.update(assignment_tables(config.plant, ledger, registry, ar_rows, types))
    for name, table in auxiliary_tables.items():
        relative = f"gold-input-extensions/{name}"
        operation = (
            digest([name, publication["artifacts"], versions])
            if name == "observation_assignments"
            else digest([name, table.to_pylist(), versions])
        )
        target = root / relative
        existing = DeltaTable(target) if (target / "_delta_log").exists() else None
        if existing is None or existing.history(1)[0].get("gold_operation_id") != operation:
            write_deltalake(
                target,
                table,
                mode="overwrite",
                configuration=RETENTION,
                commit_properties=CommitProperties(
                    custom_metadata={"gold_operation_id": operation}
                ),
            )
        refs.append(reference(root, name, relative, DeltaTable(target).version(), operation))
    publication_id = digest(
        [versions, [(r["name"], r["delta_version"]) for r in refs], coverage, source_mode]
    )
    from .publications import envelope

    pointer = envelope(root)[0] if (root / "current.json").exists() else None
    # A concurrent source revision or review must not promote a mixed source vector.
    for family, accepted in source_vector.items():
        if source_snapshot(config, family, read=False)[2] != accepted:
            raise IntegrityError(
                "Producer source vector changed during export; retry confirmed cycle"
            )
    for ref in refs:
        Snapshot(root, {"tables": [ref]}).table(ref["name"])
    ledger_rows = (
        DeltaTable(root / "gold-input-extensions/observation_assignments")
        .to_pyarrow_dataset()
        .count_rows()
    )
    if pointer and pointer["publication_id"] == publication_id:
        return {
            "status": "UNCHANGED",
            "publication_id": publication_id,
            "tables": len(refs),
            "ledger_rows": ledger_rows,
        }
    result = promote(
        root,
        refs,
        environment=config.environment,
        plant=config.plant,
        input_id=None,
        operation_id=publication_id,
        generation=pointer["generation"] + 1 if pointer else 1,
        previous=pointer["publication_id"] if pointer else None,
        coverage=coverage,
        dependencies=dependencies,
        source_mode=source_mode,
    )
    Snapshot.open(root)
    return {
        "status": "EXPORTED",
        "publication_id": result["publication_id"],
        "tables": len(refs),
        "ledger_rows": ledger_rows,
    }


def _iso(value):
    return datetime.fromisoformat(value) if value is not None else None


def accepted_operation(path, version):
    import json

    log = path / "_delta_log" / f"{version:020d}.json"
    with log.open(encoding="utf-8") as stream:
        for line in stream:
            value = json.loads(line)
            if "commitInfo" in value:
                return value["commitInfo"]["ingesta_operation_id"]
    raise IntegrityError("Accepted operation metadata is unavailable")


def assignment_tables(plant, ledger, registry, ar_rows, types):
    from ingesta_delta_lake import associations as native

    ar_rows = {native.source_id("LISTA_AR", r): r for r in ar_rows}

    def decisions():
        for r in ledger:
            episodes = [
                registry[i]
                for i in r["episode_ids"]
                if i in registry and registry[i].get("production") in r["productions"]
            ]
            zf = [e for e in episodes if e["kind"] == "ZF_ASSOCIATION"]
            assignment = f"fenix-association-v1:{plant}:{r['origin']}:{zf[0]['id']}" if zf else None
            zone = (
                native.zone_for_ar({"sources": {"SIP_DEFECTO_AR": types}}, ar_rows[r["source_id"]])
                if r["family"] == "LISTA_AR"
                else None
            )
            yield {
                "source_family": r["family"],
                "source_id": r["source_id"],
                "origin": r["origin"],
                "status": r["status"],
                "productions": r["productions"],
                "rule": r["rule"],
                "assignment_id": assignment,
                "zone": zone,
            }

    auxiliary_tables = {}
    decision_schema = pa.schema(
        [
            ("source_family", pa.string()),
            ("source_id", pa.string()),
            ("origin", pa.string()),
            ("status", pa.string()),
            ("productions", pa.list_(pa.string())),
            ("rule", pa.string()),
            ("assignment_id", pa.string()),
            ("zone", pa.string()),
        ]
    )

    def batches():
        buffer = []
        for row in decisions():
            buffer.append(row)
            if len(buffer) == 8192:
                yield pa.RecordBatch.from_pylist(buffer, schema=decision_schema)
                buffer.clear()
        if buffer:
            yield pa.RecordBatch.from_pylist(buffer, schema=decision_schema)

    auxiliary_tables["observation_assignments"] = pa.RecordBatchReader.from_batches(
        decision_schema, batches()
    )
    presences = [
        {
            "id_asignacion": "fenix-zc-presence:" + r["id"],
            "id_produccion": r["production"],
            "zona": "ZC",
            "ramal": None,
            "vigente_desde": _iso(r["start"]),
            "vigente_hasta": _iso(r["end"]),
            "version_regla": r["actor"],
            "revision_decision": r["revision"],
            "evidencia_origen": "registry:" + r["id"],
        }
        for r in registry.values()
        if r["kind"] == "ZC_PRESENCE"
    ]
    auxiliary_tables["zc_presences"] = pa.Table.from_pylist(
        presences,
        schema=pa.schema(
            [
                ("id_asignacion", pa.string()),
                ("id_produccion", pa.string()),
                ("zona", pa.string()),
                ("ramal", pa.int64()),
                ("vigente_desde", pa.timestamp("us")),
                ("vigente_hasta", pa.timestamp("us")),
                ("version_regla", pa.string()),
                ("revision_decision", pa.int64()),
                ("evidencia_origen", pa.string()),
            ]
        ),
    )
    return auxiliary_tables
