"""Producer-owned immutable Silver snapshots; readers never open active SQLite."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pyarrow as pa
from deltalake import DeltaTable

from .common import IntegrityError, digest
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
        if _read(path) != value:
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


def publish_family(config, storage, *, lane, identity, family, captured_ids=()):
    """Called by the owner only after this family's confirmed transformation."""
    cfg = replace(config, family=family)
    if not cfg.control_path.exists() or not DeltaTable.is_deltatable(str(cfg.table_path)):
        return None
    with closing(sqlite3.connect(cfg.control_path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        if db.execute(
            "SELECT 1 FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN') LIMIT 1"
        ).fetchone():
            raise IntegrityError("Cannot publish unconfirmed ingestion work")
        settings = dict(db.execute("SELECT key,value FROM settings"))
        table = DeltaTable(str(cfg.table_path))
        version = table.version()
        if settings.get("last_version") != str(version):
            raise IntegrityError("Silver table does not match its owner's confirmation")
        accepted = db.execute(
            "SELECT operation_id,confirmed_at FROM batches WHERE delta_version=? "
            "AND confirmed_at IS NOT NULL ORDER BY confirmed_at LIMIT 1",
            (version,),
        ).fetchone()
        if accepted is None:
            raise IntegrityError("Silver version lacks an owner's receipt")
        control_id = settings["control_id"]
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
    root = storage.exchange / "silver"
    root.mkdir(parents=True, exist_ok=True)
    refs, parent = {}, None
    if (root / "current.json").exists():
        previous = SilverSnapshot.open(storage)
        if previous.manifest["identity"] != identity or previous.manifest["lane"] != lane:
            raise IntegrityError("Silver publication belongs to another lane")
        refs = dict(previous.manifest["tables"])
        parent = previous.manifest["publication_id"]
    refs[family] = {
        "uri": cfg.table_path.relative_to(storage.silver).as_posix(),
        "table_id": table.metadata().id,
        "version": version,
        "control_id": control_id,
        "operation_id": operation,
        "confirmed_at": confirmed_at,
        "schema_id": schema_hash(pa.schema(table.schema().to_arrow())),
        "captured_ids": list(captured_ids),
        **horizons,
    }
    if parent and refs == previous.manifest["tables"]:
        return previous.manifest
    payload = {
        "schema_id": "fenix-silver-snapshot-v1",
        "lane": lane,
        "identity": identity,
        "tables": refs,
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
