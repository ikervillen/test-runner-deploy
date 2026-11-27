"""Producer-owned immutable Silver snapshots; readers never open active SQLite."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import portalocker
import pyarrow as pa
from deltalake import DeltaTable

from .common import IntegrityError, canonical, digest, now
from .runtime import atomic_json


def schema_hash(schema):
    return digest([(f.name, str(f.type), f.nullable) for f in schema])


def _read(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _inside(root, relative):
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise IntegrityError("Invalid publication path")
    path = Path(relative)
    resolved = (root / path).resolve()
    if path.is_absolute() or ".." in path.parts or not resolved.is_relative_to(root.resolve()):
        raise IntegrityError("Publication path escapes its store")
    return resolved


def immutable_json(path, value):
    if path.exists():
        if canonical(_read(path)) != canonical(value):
            raise IntegrityError("Immutable publication identity was reused")
        return
    atomic_json(path, value)


class SilverSnapshot:
    def __init__(self, storage, manifest):
        self.storage, self.manifest = storage, manifest
        payload = {k: v for k, v in manifest.items() if k != "publication_id"}
        if manifest.get("schema_id") != "fenix-silver-snapshot-v1" or digest(
            payload
        ) != manifest.get("publication_id"):
            raise IntegrityError("Invalid Silver publication hash or schema")

    @classmethod
    def open(cls, storage, *, publication_id=None):
        root = storage.exchange / "silver"
        if publication_id is None:
            pointer = _read(root / "current.json")
            publication_id = pointer["publication_id"]
        if (
            not isinstance(publication_id, str)
            or len(publication_id) != 64
            or any(c not in "0123456789abcdef" for c in publication_id)
        ):
            raise IntegrityError("Invalid Silver publication identity")
        manifest = _read(root / "publications" / (publication_id + ".json"))
        if manifest.get("publication_id") != publication_id:
            raise IntegrityError("Silver pointer differs from its immutable snapshot")
        return cls(storage, manifest)

    def control(self, family):
        ref = self.manifest.get("controls", {}).get(family)
        if ref is None:
            raise IntegrityError("Silver publication has no confirmed control projection")
        value = _read(_inside(self.storage.exchange / "silver", ref["uri"]))
        if (
            digest(value) != ref["hash"]
            or value.get("schema_id") != "fenix-control-snapshot-v1"
            or value.get("family") != family
        ):
            raise IntegrityError("Control projection differs from its immutable proof")
        table_ref = self.manifest["tables"].get(family)
        if table_ref and (
            value["control_id"] != table_ref["control_id"]
            or value["version"] != table_ref["version"]
        ):
            raise IntegrityError("Control projection differs from its confirmed table")
        return value

    def table(self, family):
        ref = self.manifest["tables"][family]
        table = DeltaTable(str(_inside(self.storage.silver, ref["uri"])), version=ref["version"])
        if (
            table.metadata().id != ref["table_id"]
            or schema_hash(pa.schema(table.schema().to_arrow())) != ref["schema_id"]
        ):
            raise IntegrityError("Published Silver table identity or schema changed")
        return table

    def batches(self, family, *, batch_size, expression=None):
        yield from (
            self.table(family)
            .to_pyarrow_dataset()
            .scanner(filter=expression, batch_size=batch_size, use_threads=False)
            .to_batches()
        )


def publish_family(config, storage, *, lane, identity, family, captured_ids=(), slot_receipts=None):
    lock = storage.silver / f".writer-{config.plant}-{family}.lock"
    with portalocker.Lock(str(lock), mode="a", timeout=0):
        return _publish_family(
            config,
            storage,
            lane=lane,
            identity=identity,
            family=family,
            captured_ids=captured_ids,
            slot_receipts=slot_receipts,
        )


def _publish_family(
    config, storage, *, lane, identity, family, captured_ids=(), slot_receipts=None
):
    """Called by the owner only after this family's confirmed transformation."""
    cfg = replace(config, family=family)
    if not cfg.control_path.exists():
        return None
    with closing(sqlite3.connect(cfg.control_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        if db.execute(
            "SELECT 1 FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN') LIMIT 1"
        ).fetchone():
            raise IntegrityError("Cannot publish unconfirmed ingestion work")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        table = (
            DeltaTable(str(cfg.table_path))
            if DeltaTable.is_deltatable(str(cfg.table_path))
            else None
        )
        version = table.version() if table is not None else None
        if settings.get("last_version", "None") != str(version):
            raise IntegrityError("Silver table does not match its owner's confirmation")
        control_id = settings["control_id"]
        operation, confirmed_at = None, None
        if table is not None:
            accepted = db.execute(
                "SELECT operation_id,confirmed_at FROM batches WHERE delta_version=? "
                "AND confirmed_at IS NOT NULL ORDER BY confirmed_at LIMIT 1",
                (version,),
            ).fetchone()
            if accepted is None:
                raise IntegrityError("Silver version lacks an owner's receipt")
            operation, confirmed_at = accepted
            commit = table.history(1)[0]
            if commit.get("ingesta_operation_id") != operation or (
                commit.get("ingesta_control_id") != control_id
                or commit.get("ingesta_contract") != cfg.contract.name
                or commit.get("ingesta_plant") != cfg.plant
            ):
                raise IntegrityError("Silver commit provenance differs from its owner's receipt")
        horizons = {
            name: json.loads(settings.get(name) or "{}")
            for name in ("observed_until", "live_until")
        }
        rejected = [
            r[0] for r in db.execute("SELECT row_hash FROM review_rejections ORDER BY row_hash")
        ]
        if cfg.read_max_rows is not None and len(rejected) > cfg.read_max_rows:
            raise IntegrityError("Review publication exceeds its explicit row budget")
        from .pipeline_control import confirmed_control

        cases, meta, extra = confirmed_control(
            cfg, db, settings, version, allow_empty=True, live_registry=lane == "live"
        )
        if (
            cfg.read_max_rows is not None
            and len(cases) + len(extra.get("composition", {})) > cfg.read_max_rows
        ):
            raise IntegrityError("Control publication exceeds its explicit row budget")
        control_payload = {
            "schema_id": "fenix-control-snapshot-v1",
            "family": family,
            "control_id": control_id,
            "version": version,
            "cases": cases,
            "meta": meta,
            "extra": extra,
            "archive_context": cfg.archive_context,
            "processing_window": cfg.processing_window,
        }
    root = storage.exchange / "silver"
    root.mkdir(parents=True, exist_ok=True)
    refs, controls, parent = {}, {}, None
    if (root / "current.json").exists():
        previous = SilverSnapshot.open(storage)
        if previous.manifest["identity"] != identity or previous.manifest["lane"] != lane:
            raise IntegrityError("Silver publication belongs to another lane")
        refs = dict(previous.manifest["tables"])
        controls = dict(previous.manifest.get("controls", {}))
        parent = previous.manifest["publication_id"]
    proof = digest(control_payload)
    (root / "controls").mkdir(exist_ok=True)
    immutable_json(root / "controls" / (proof + ".json"), control_payload)
    controls[family] = {"hash": proof, "uri": "controls/" + proof + ".json"}
    if family == "LISTA_AR" and table is None and slot_receipts is not None:
        controls[family]["slot_receipts"] = slot_receipts
    if table is not None:
        previous_ref = refs.get(family, {})
        owner_confirmed_at = confirmed_at
        confirmed_at = (
            previous_ref.get("confirmed_at") if previous_ref.get("version") == version else now()
        )
        if not captured_ids and previous_ref.get("version") == version:
            captured_ids = previous_ref.get("captured_ids", ())
        refs[family] = {
            "uri": cfg.table_path.relative_to(storage.silver).as_posix(),
            "table_id": table.metadata().id,
            "version": version,
            "control_id": control_id,
            "operation_id": operation,
            "confirmed_at": confirmed_at,
            "owner_confirmed_at": owner_confirmed_at,
            "schema_id": schema_hash(pa.schema(table.schema().to_arrow())),
            "captured_ids": list(captured_ids),
            "rejected_hashes": rejected,
            **horizons,
        }
        if slot_receipts is not None:
            refs[family]["slot_receipts"] = slot_receipts
        elif "slot_receipts" in previous_ref:
            refs[family]["slot_receipts"] = previous_ref["slot_receipts"]
    if (
        parent
        and refs == previous.manifest["tables"]
        and controls == previous.manifest.get("controls", {})
    ):
        return previous.manifest
    payload = {
        "schema_id": "fenix-silver-snapshot-v1",
        "lane": lane,
        "identity": identity,
        "tables": refs,
        "controls": controls,
        "parent": parent,
    }
    publication_id = digest(payload)
    manifest = {**payload, "publication_id": publication_id}
    publications = root / "publications"
    publications.mkdir(exist_ok=True)
    immutable_json(publications / (publication_id + ".json"), manifest)
    atomic_json(root / "current.json", {"publication_id": publication_id})
    return manifest


def confirmation_age(ref, technical_now):
    from datetime import datetime

    stamp = datetime.fromisoformat(ref["confirmed_at"])
    if stamp.tzinfo is None or technical_now.tzinfo is None:
        raise IntegrityError("Archive clocks require timezone-aware confirmation instants")
    return (technical_now - stamp).total_seconds()
