"""Non-destructive, hash-verified backup/restore of a quiescent local Gold store."""

import hashlib
import os
import shutil
from pathlib import Path
from uuid import uuid4

import portalocker

from .common import IntegrityError, atomic_json, digest, read_json, utc_now, within
from .publications import Snapshot


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def disjoint(source, destination):
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
    if (
        source == destination
        or source.is_relative_to(destination)
        or destination.is_relative_to(source)
    ):
        raise IntegrityError("Backup/restore destinations must be independent directories")
    if destination.exists():
        raise IntegrityError("Backup/restore refuses an existing destination")
    return source, destination


def copy_file(source, target):
    if source.is_symlink():
        raise IntegrityError("Backup refuses symbolic links")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def create(source, destination):
    source, destination = disjoint(source, destination)
    with (
        portalocker.Lock(source / "pipeline.lock", timeout=0),
        portalocker.Lock(source / "writer.lock", timeout=0),
    ):
        snap = Snapshot.open(source, require_gold_contract=True)
        files = []
        destination.mkdir(parents=True)
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise IntegrityError("Backup refuses symbolic links")
            if not path.is_file() or path.suffix in {".lock", ".tmp"}:
                continue
            relative = path.relative_to(source).as_posix()
            target = within(destination, "store/" + relative)
            copy_file(path, target)
            files.append(
                dict(path="store/" + relative, bytes=target.stat().st_size, sha256=checksum(target))
            )
        manifest = {
            "schema_id": "quality-gold-backup-v1",
            "backup_id": uuid4().hex,
            "publication_id": snap.publication_id,
            "created_at": utc_now(),
            "files": files,
        }
        atomic_json(destination / "backup.json", manifest)
        atomic_json(destination / "READY.json", {"manifest_hash": digest(manifest)})
    result = verify(destination)
    atomic_json(
        source / "backup-status.json",
        {
            "status": "VERIFIED",
            "last_success_at": utc_now(),
            "publication_id": result["publication_id"],
            "destination": str(destination),
        },
    )
    return result


def verify(root):
    root = Path(root).resolve(strict=True)
    manifest = read_json(root / "backup.json", max_bytes=64 * 1024**2)
    if (
        manifest.get("schema_id") != "quality-gold-backup-v1"
        or digest(manifest) != read_json(root / "READY.json")["manifest_hash"]
    ):
        raise IntegrityError("Backup manifest hash/schema mismatch")
    paths = set()
    for row in manifest["files"]:
        if row["path"] in paths or not row["path"].startswith("store/"):
            raise IntegrityError("Invalid or duplicate backup path")
        paths.add(row["path"])
        path = within(root, row["path"])
        if (
            path.is_symlink()
            or path.stat().st_size != row["bytes"]
            or checksum(path) != row["sha256"]
        ):
            raise IntegrityError("Backup file checksum/size mismatch")
    actual = {p.relative_to(root).as_posix() for p in (root / "store").rglob("*") if p.is_file()}
    if actual != paths:
        raise IntegrityError("Backup contains unlisted or missing files")
    snap = Snapshot.open(root / "store", require_gold_contract=True)
    if snap.publication_id != manifest["publication_id"]:
        raise IntegrityError("Backup publication differs from manifest")
    return {
        "status": "VERIFIED",
        "publication_id": snap.publication_id,
        "files": len(paths),
        "bytes": sum(r["bytes"] for r in manifest["files"]),
    }


def restore(source, destination):
    source, destination = disjoint(source, destination)
    verified = verify(source)
    manifest = read_json(source / "backup.json", max_bytes=64 * 1024**2)
    destination.mkdir(parents=True)
    for row in manifest["files"]:
        target = within(destination, row["path"][len("store/") :])
        copy_file(within(source, row["path"]), target)
        if checksum(target) != row["sha256"]:
            raise IntegrityError("Restored file verification failed")
    snap = Snapshot.open(destination, require_gold_contract=True)
    if snap.publication_id != verified["publication_id"]:
        raise IntegrityError("Restored publication differs from backup")
    return {**verified, "status": "RESTORED"}


def drill(gold_root, scratch):
    """Restore the latest backup into scratch, read every table, then discard the copy."""
    gold_root = Path(gold_root).resolve(strict=True)
    status_path = gold_root / "backup-status.json"
    if not status_path.exists():
        raise IntegrityError("No verified backup to drill")
    backup = read_json(status_path)["destination"]
    target = Path(scratch) / uuid4().hex
    try:
        restored = restore(backup, target)
        snap = Snapshot.open(target, require_gold_contract=True)
        rows = sum(
            snap.table(r["name"]).to_pyarrow_dataset().count_rows() for r in snap.manifest["tables"]
        )
    finally:
        shutil.rmtree(target, ignore_errors=True)
    result = {
        "status": "RESTORE_VERIFIED",
        "backup": backup,
        "publication_id": restored["publication_id"],
        "files": restored["files"],
        "rows": rows,
    }
    atomic_json(gold_root / "restore-status.json", {**result, "last_success_at": utc_now()})
    return result
