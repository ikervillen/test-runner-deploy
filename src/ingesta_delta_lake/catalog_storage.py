"""Lectura de snapshots aceptados y escritura condicionada del catálogo derivado."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.dataset as ds
from deltalake import DeltaTable, write_deltalake
from deltalake.transaction import CommitProperties

from . import __version__, catalog, mp9
from .commit_protocol import application_transaction, delta_configuration, merge_counts_match
from .common import DependencyPending, IntegrityError, canonical, digest
from .delta import DeltaStore
from .mp9_storage import Mp9Store, load_models
from .pipeline_publications import SilverSnapshot
from .pipeline_rows import portable
from .topology import FAMILIES, MACHINES, machine_for_period
from .topology_storage import TopologyStore
from .topology_storage import snapshot as topology_snapshot


def read_source(config):
    source = replace(config, family="MP9_CAMBIO_MODELO")
    if not source.control_path.exists():
        if source.table_path.exists():
            raise IntegrityError("Falta el control de MP9 existente")
        return {}, {"contract": mp9.CONTRACT, "version": None, "hash": mp9.content_hash({})}, []
    with closing(sqlite3.connect(f"{source.control_path.as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        expected = canonical(
            {
                "plant": source.plant,
                "silver_root": str(source.silver_root),
                "bronze_root": str(source.bronze_root),
                "contract": mp9.CONTRACT,
            }
        )
        if settings.get("binding") != expected:
            raise IntegrityError("El control MP9 pertenece a otro ámbito")
        if db.execute(
            "SELECT COUNT(*) FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN')"
        ).fetchone()[0]:
            raise DependencyPending("Recuperar MP9 antes de derivar su catálogo")
        version, records = Mp9Store(source, settings["control_id"]).read()
        if settings.get("last_version", "None") != str(version):
            raise IntegrityError("El snapshot MP9 no está confirmado")
        cases = [
            {
                "issue_id": r["issue_id"],
                "reason": r["reason"],
                "status": r["status"],
                "payload": json.loads(r["payload_json"]),
            }
            for r in db.execute("SELECT * FROM issues WHERE status != 'RESOLVED' ORDER BY issue_id")
        ]
    return (
        records,
        {
            "contract": mp9.CONTRACT,
            "version": version,
            "hash": mp9.content_hash(records),
            "control_id": settings["control_id"],
            "table_path": str(source.table_path),
            "quality_hash": digest(cases),
        },
        cases,
    )


def historical_mp9(config, publication_id=None):
    if config.history_exchange is None:
        if publication_id is not None:
            raise IntegrityError("Historical MP9 reference is no longer configured")
        return None
    storage = SimpleNamespace(exchange=config.history_exchange, silver=config.history_silver)
    try:
        snapshot = SilverSnapshot.open(storage, publication_id=publication_id)
    except FileNotFoundError:
        if publication_id is not None:
            raise
        return None
    if snapshot.manifest["lane"] != "history":
        raise IntegrityError("MP9 reference must be published by history")
    if "MP9_CAMBIO_MODELO" in snapshot.manifest["tables"]:
        return snapshot
    return None


def merge_historical_closures(records, snapshot, *, batch_size):
    """Use confirmed closures only for identities already owned by live."""
    merged, issues = dict(records), []
    if snapshot is None or not records:
        return merged, issues
    family = "MP9_CAMBIO_MODELO"
    blocked = set()
    for case in snapshot.control(family)["cases"]:
        payload = case["payload"]
        key = payload.get("origen_fenix"), payload.get("ID")
        if key in records:
            blocked.add(key)
            issues.append(case)
    ids = sorted({key[1] for key in records})
    selected = ds.field("ID").isin(ids) & ds.field("FECHA_OFF").is_valid()
    for batch in snapshot.batches(family, batch_size=batch_size, expression=selected):
        for raw in batch.to_pylist():
            key = mp9.key(raw)
            if key not in records or key in blocked or raw["FECHA_OFF"] is None:
                continue
            candidate = portable(raw)
            row, conflicts = mp9.consolidate([records[key], candidate])
            if conflicts:
                payload = {
                    "origen_fenix": key[0],
                    "ID": key[1],
                    "candidates": [records[key], candidate],
                    "fields": conflicts,
                }
                issues.append(
                    {
                        "issue_id": digest(["historical_mp9_closure", payload]),
                        "reason": "HISTORICAL_MP9_CONFLICT",
                        "status": "PENDING",
                        "payload": payload,
                    }
                )
            else:
                merged[key] = row
    return merged, issues


def verify_manifest(config, manifest):
    """Releer versiones fijadas, también si la fuente ya avanzó; sin consumir CSV vivos."""
    result = {}
    for name, descriptor in manifest.items():
        source = replace(config, family=name)
        if descriptor["version"] is None:
            result[name] = {}
            continue
        with closing(sqlite3.connect(f"{source.control_path.as_uri()}?mode=ro", uri=True)) as db:
            control_id = db.execute("SELECT value FROM settings WHERE key='control_id'").fetchone()
        if control_id is None or control_id[0] != descriptor["control_id"]:
            raise IntegrityError("El control del snapshot de derivación ha cambiado")
        store = (
            TopologyStore(source, control_id[0])
            if name in FAMILIES
            else (
                Mp9Store(source, control_id[0])
                if name == "MP9_CAMBIO_MODELO"
                else DeltaStore(source, control_id[0])
            )
        )
        store.table()  # Verificar propiedad y todos los commits antes de leer una versión anterior.
        table = DeltaTable(str(source.table_path), version=descriptor["version"])
        version, rows = store.read(table)
        if (
            version != descriptor["version"]
            or source.contract.content_hash(rows) != descriptor["hash"]
        ):
            raise IntegrityError("El snapshot fuente fijado no coincide con la evidencia")
        if (reference := descriptor.get("historical_closures")) is not None:
            snapshot = historical_mp9(config, reference["publication_id"])
            if snapshot is None:
                raise IntegrityError("Historical MP9 reference is missing its confirmed table")
            rows, _ = merge_historical_closures(rows, snapshot, batch_size=config.read_batch_rows)
            if mp9.content_hash(rows) != reference["hash"]:
                raise IntegrityError("Historical MP9 closures differ from the pinned reference")
        result[name] = rows
    return result


def load_sources(config, *, previous_history=None):
    models, model_snapshot = load_models(config)
    records, mp9_snapshot, cases = read_source(config)
    history = historical_mp9(config)
    if previous_history is not None and config.history_exchange is not None:
        pinned = historical_mp9(config, previous_history["publication_id"])
        if pinned is None:
            raise IntegrityError("Previously confirmed historical MP9 reference is missing")
        family = "MP9_CAMBIO_MODELO"
        if history is None or history.manifest["controls"].get(family) == pinned.manifest[
            "controls"
        ].get(family):
            history = pinned
    if history is not None and records:
        records, conflicts = merge_historical_closures(
            records, history, batch_size=config.read_batch_rows
        )
        mp9_snapshot["historical_closures"] = {
            "publication_id": history.manifest["publication_id"],
            "hash": mp9.content_hash(records),
        }
        cases.extend(conflicts)
    manifest = {"SIP_MODELO": model_snapshot, "MP9_CAMBIO_MODELO": mp9_snapshot}
    if config.topology_enabled:
        machines, descriptor, machine_cases = topology_snapshot(config, MACHINES)
        manifest[MACHINES] = descriptor
        codes = dict(config.line_code_by_origin)
        for row in records.values():
            if row["STATUS"] != 1:
                continue
            machine = machine_for_period(
                machines, machine_cases, row["ID_MAQUINA"], row["FECHA_ON"], row["FECHA_OFF"]
            )
            if machine is None or machine["codigo_linea_zc"] != codes[row["origen_fenix"]]:
                payload = {"origen_fenix": row["origen_fenix"], "ID": row["ID"], "candidate": row}
                cases.append(
                    {
                        "issue_id": digest(["physical_machine", payload, descriptor]),
                        "reason": "MACHINE_PENDING",
                        "status": "PENDING",
                        "payload": payload,
                    }
                )
    return models, records, manifest, cases


class CatalogStore(DeltaStore):
    def read(self, table=None):
        table = self.read_view(table if table is not None else self.table())
        if table is None:
            return None, {}
        arrow = table.to_pyarrow_table()
        if arrow.schema != catalog.SCHEMA:
            raise IntegrityError("Esquema del catálogo inesperado")
        rows, natural_keys = {}, set()
        for row in arrow.to_pylist():
            for f in ("fecha_inicio_zc", "fecha_fin_zf"):
                if row[f] is not None:
                    row[f] = row[f].isoformat(timespec="seconds")
            if any(
                row[f] is None for f in catalog.FIELDS if f not in ("fecha_fin_zf", "ID_ST1_REL")
            ):
                raise IntegrityError("Campo obligatorio del catálogo nulo")
            identity = row["id_produccion"]
            natural = (row["origen_fenix"], row["id_zc_raiz"])
            if (
                row["planta"] != self.plant
                or identity != catalog.production_id(self.plant, *natural)
                or identity in rows
                or natural in natural_keys
                or not row["linea"]
                or row["ID_ST1_REL"] not in (None, row["id_zc_raiz"])
                or (
                    row["fecha_fin_zf"] is not None
                    and (row["ID_ST1_REL"] is None or row["fecha_fin_zf"] < row["fecha_inicio_zc"])
                )
            ):
                raise IntegrityError("Identidad o límites del catálogo incompatibles")
            rows[identity] = row
            natural_keys.add(natural)
        return table.version(), rows

    def apply_changes(self, inserts, updates, operation, before_version):
        table = self.table()
        version, existing = self.read(table)
        if version != before_version or not (inserts or updates):
            raise IntegrityError("Snapshot de catálogo cambiado o escritura vacía")
        seen = set()
        for row in inserts:
            if row["id_produccion"] in existing or row["id_produccion"] in seen:
                raise IntegrityError("La producción ya existe")
            seen.add(row["id_produccion"])
        for row in updates:
            old = existing.get(row["id_produccion"])
            if (
                old is None
                or old != row["before"]
                or row["id_produccion"] in seen
                or any(
                    row[f] != old[f]
                    for f in catalog.FIELDS
                    if f not in ("fecha_fin_zf", "ID_ST1_REL")
                )
                or (old["fecha_fin_zf"] is not None and row["fecha_fin_zf"] != old["fecha_fin_zf"])
                or (old["ID_ST1_REL"] is not None and row["ID_ST1_REL"] != old["ID_ST1_REL"])
            ):
                raise IntegrityError("La actualización del catálogo requiere revisión")
            seen.add(row["id_produccion"])
        values = []
        for row in [*inserts, *updates]:
            value = {f: row[f] for f in catalog.FIELDS}
            for f in ("fecha_inicio_zc", "fecha_fin_zf"):
                if value[f] is not None:
                    value[f] = datetime.fromisoformat(value[f])
            values.append(value)
        arrow = pa.Table.from_pylist(values, schema=catalog.SCHEMA)
        props = CommitProperties(
            custom_metadata={
                "ingesta_operation_id": operation,
                "ingesta_control_id": self.control_id,
                "ingesta_plant": self.plant,
                "ingesta_contract": catalog.CONTRACT,
                "ingesta_release": __version__,
                "ingesta_timestamp_policy": "local_naive_test_v1",
            },
            app_transactions=application_transaction(self.control_id, before_version),
            max_commit_retries=0,
        )
        if table is None:
            write_deltalake(
                str(self.path),
                arrow,
                mode="error",
                configuration=delta_configuration(self.config),
                commit_properties=props,
            )
        else:
            metrics = (
                table.merge(
                    arrow,
                    "t.id_produccion = s.id_produccion",
                    source_alias="s",
                    target_alias="t",
                    commit_properties=props,
                )
                .when_matched_update(
                    {
                        "fecha_fin_zf": "coalesce(t.fecha_fin_zf, s.fecha_fin_zf)",
                        "ID_ST1_REL": "coalesce(t.ID_ST1_REL, s.ID_ST1_REL)",
                    },
                    predicate="(t.fecha_fin_zf IS NULL AND s.fecha_fin_zf IS NOT NULL) OR "
                    "(t.ID_ST1_REL IS NULL AND s.ID_ST1_REL IS NOT NULL)",
                )
                .when_not_matched_insert_all()
                .execute()
            )
            if not merge_counts_match(metrics, inserts, updates, self.config):
                raise IntegrityError("El MERGE de catálogo no coincide con la intención")
