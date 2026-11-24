"""Descubrimiento de las dos modalidades y captura inmutable de revisiones."""

import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .common import CaptureError, IntegrityError, canonical, digest, history_yields, now, slot_stamp
from .config import Config

FAMILY = "SIP_MODELO"
# Silver reads these exports only. Bronze keeps every other CSV Fenix drops.
SILVER_EXPORTS = frozenset(
    {
        "LISTA_DEFECTOS",
        "LISTA_RECH",
        "CONT_ZC",
        "LISTA_AR",
        "MP9_CAMBIO_MODELO",
        "SIP_MODELO",
        "SIP_DEFECTO",
        "SIP_DEFECTO_STATUS",
        "SIP_DEFECTO_AR",
        "SIP_MAQUINA_MOLDEO",
        "SIP_LINEA_ZF",
    }
)
_EXPORT_NAME = re.compile(r"^(?:\d{8}(?:_\d{5,6})?_)?(.+)\.csv$", re.IGNORECASE)


@dataclass(frozen=True)
class Snapshot:
    origin: str
    source_path: str
    sha256: str
    bronze_path: str
    size: int
    mtime_ns: int
    received_at: str
    producer_time: str | None

    def descriptor(self) -> dict:
        return asdict(self)


def orphan_captures(config, known):
    """Recover a crash between durable Bronze capture and its SQLite receipt.

    Only the writer's exact origin/family/hash layout is eligible. The original
    export path/time is unknown; expose the actual Bronze path, never invent it.
    """
    for origin, _ in config.sources:
        root = config.bronze_root / config.plant / origin / config.family
        for path in sorted(root.glob("*/*.csv")):
            if not re.fullmatch(r"[0-9a-f]{64}", path.stem):
                raise IntegrityError("Unexpected file in immutable capture namespace")
            if (origin, path.stem) in known:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise IntegrityError("Orphan capture outside its origin/family")
            with resolved.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != path.stem:
                    raise IntegrityError("Orphan Bronze capture hash differs from its name")
            stat = resolved.stat()
            yield Snapshot(
                origin,
                str(resolved),
                path.stem,
                str(resolved),
                stat.st_size,
                0,
                datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                None,
            )


def export_kind(name: str) -> str | None:
    """Suffix of a Fenix CSV. The match is the whole suffix, not a shared ending."""
    match = _EXPORT_NAME.fullmatch(name)
    return match.group(1).upper() if match else None


def parse_name(name: str, family: str = FAMILY) -> str | None:
    match = re.fullmatch(
        rf"^(?:(\d{{8}})(?:_(\d{{5,6}}))?_)?{re.escape(family)}\.csv$", name, re.IGNORECASE
    )
    if not match:
        raise CaptureError(f"Nombre {family} no reconocido: {name}")
    day, clock = match.groups()
    if day is None:
        return None
    formats = ("%d%m%Y",) if clock else ("%Y%m%d", "%d%m%Y")
    interpretations = set()
    for fmt in formats:
        try:
            parsed = datetime.strptime(day, fmt)
            if clock:
                hms = clock.zfill(6)
                parsed = parsed.replace(
                    hour=int(hms[:2]), minute=int(hms[2:4]), second=int(hms[4:])
                )
            interpretations.add(parsed.isoformat() if clock else parsed.date().isoformat())
        except ValueError:
            continue
    if len(interpretations) != 1:
        raise CaptureError(f"Fecha/hora de nombre inválida o ambigua: {name}")
    # Marca local declarada; no convertirla a UTC ni usarla como fecha de negocio.
    return interpretations.pop()


def discover(config: Config) -> list[tuple[str, Path, str | None]]:
    """Files of this Silver family. A longer or different suffix is another export."""
    if config.family.upper() not in SILVER_EXPORTS:
        return []
    result = []
    for origin, folder in config.sources:
        root = folder.resolve()
        # DirEntry reuses directory metadata. Resolving/stating every ordinary
        # filename adds thousands of cross-WSL calls; only links need realpath.
        with os.scandir(root) as entries:
            selected = sorted(
                (e for e in entries if export_kind(e.name) == config.family.upper()),
                key=lambda e: e.name,
            )
        for entry in selected:
            path = root / entry.name
            if entry.is_symlink():
                resolved = path.resolve()
                if not resolved.is_relative_to(root):
                    raise CaptureError(f"Archivo enlazado fuera del origen: {path}")
                regular = resolved.is_file()
            else:
                resolved = path
                regular = entry.is_file(follow_symlinks=False)
            if not regular:
                raise CaptureError(f"La entrada no es un archivo: {path}")
            result.append((origin, resolved, parse_name(entry.name, config.family)))
    return result


@contextmanager
def input_scan(config, control):
    """Separate periodic source verification from durable backlog consumption.

    Timestamp the start, confirm only a complete discovery/capture pass. Failed
    scans retry immediately; successful captures remain queued independently.
    """
    if config.intake_path is not None:
        control.input_scan_performed = bool(config.intake_ids)
        yield control.input_scan_performed
        return
    started = time.time()
    signature = digest(
        [
            "periodic-input-scan-v1",
            config.family,
            config.encoding,
            [(o, str(p)) for o, p in config.sources],
            os.environ.get("INGESTA_ACTIVATION"),
        ]
    )
    previous = json.loads(control.setting("last_input_scan") or "null")
    due = not (
        config.input_scan_interval_seconds
        and previous
        and previous["signature"] == signature
        and 0 <= started - previous["started"] < config.input_scan_interval_seconds
    )
    control.input_scan_performed = due
    yield due
    if due:
        with control.db:
            control.db.execute(
                "INSERT INTO settings VALUES('last_input_scan',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (canonical({"signature": signature, "started": started}),),
            )


def export_slot(name: str, kind: str) -> str | None:
    """Ten-minute filename slot. A CSV without a clock is not a historical slot."""
    if kind == "OTROS":
        return None
    try:
        return slot_stamp(parse_name(name, kind))
    except CaptureError:
        return None


def _mirror_candidates(config: Config, index: dict) -> tuple[list[dict], dict]:
    """Every CSV in the line folders. Unchanged bytes are not read again."""
    found: list[dict] = []
    counts = {"unchanged": 0, "failed": 0}
    for origin, folder in config.sources:
        root = folder.resolve()
        if not root.is_dir():
            counts["failed"] += 1
            continue
        with os.scandir(root) as entries:
            names = sorted(entry.name for entry in entries if entry.name.lower().endswith(".csv"))
        for name in names:
            path = root / name
            try:
                stat = path.stat()
            except OSError:
                counts["failed"] += 1
                continue
            kind = export_kind(name) or "OTROS"
            previous = index.get(str(path))
            same = (
                previous
                and previous.get("size") == stat.st_size
                and previous.get("mtime_ns") == stat.st_mtime_ns
            )
            if same:
                counts["unchanged"] += 1
            found.append(
                {
                    "origin": origin,
                    "path": path,
                    "kind": kind,
                    "slot": export_slot(name, kind),
                    "same": same,
                }
            )
    return found, counts


def _copy_one(config: Config, index: dict, item: dict) -> str:
    if item["same"]:
        return "unchanged"
    kind = item["kind"]
    try:
        stamp = None if kind == "OTROS" else parse_name(item["path"].name, kind)
    except CaptureError:
        stamp = None
    try:
        snapshot = capture(replace(config, family=kind), item["origin"], item["path"], stamp)
    except CaptureError:
        return "skipped"
    except (OSError, IntegrityError):
        return "failed"
    index[str(item["path"])] = {
        "size": snapshot.size,
        "mtime_ns": snapshot.mtime_ns,
        "sha256": snapshot.sha256,
    }
    return "copied"


def mirror_exports(config: Config, lane: str = "all") -> dict:
    """Copy Fenix into Bronze in two passes, the same way Silver and Gold run.

    ``live`` copies the newest ten-minute slot, plus a file that has no clock.
    ``history`` copies older slots, oldest first, and stops before the live tick.
    One failure does not stop the rest. Silver later opens only the families it knows.
    """
    if lane not in {"live", "history", "all"}:
        raise CaptureError(f"Carril de copia desconocido: {lane}")
    index_path = config.control_root / "bronze-mirror.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    found, counts = _mirror_candidates(config, index)
    newest = max((item["slot"] for item in found if item["slot"]), default=None)
    live = [item for item in found if item["slot"] is None or item["slot"] == newest]
    older: dict[str, list] = {}
    for item in found:
        if item["slot"] and item["slot"] != newest:
            older.setdefault(item["slot"], []).append(item)
    counts.update({"copied": 0, "skipped": 0, "lane": lane})

    def copy_group(group: list) -> None:
        for item in group:
            outcome = _copy_one(config, index, item)
            if outcome == "unchanged":
                continue
            counts[outcome] = counts.get(outcome, 0) + 1

    if lane in {"live", "all"}:
        copy_group(live)
    if lane in {"history", "all"} and not (lane == "history" and history_yields()):
        for slot in sorted(older):
            if history_yields():
                break
            copy_group(older[slot])
    if counts["copied"] or not index_path.exists():
        config.control_root.mkdir(parents=True, exist_ok=True)
        temporary = index_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
        os.replace(temporary, index_path)
    return counts


def capture_inputs(config, control, attempt):
    """Stable cache speeds aliases/replay; every live file is still hashed.

    Failed attempts retain all captured revisions, including overwritten/rotated
    paths. Only finish_attempt after durable handling acknowledges this queue.
    """
    known = control.known_captures()
    snapshots, included, verified = [], set(), set()
    with input_scan(config, control) as due:
        if due:
            for snapshot in available_captures(config, known=known, verified=verified):
                control.receipts(attempt, [snapshot])
                snapshots.append(snapshot)
                included.add((snapshot.origin, snapshot.sha256))
                known.setdefault((snapshot.origin, snapshot.sha256), snapshot)
            if config.intake_path is None:
                for snapshot in orphan_captures(config, known):
                    control.receipts(attempt, [snapshot])
                    known[(snapshot.origin, snapshot.sha256)] = snapshot
    for key, snapshot in control.known_captures(pending=True).items():
        if key not in included:
            snapshots.append(snapshot)
    return snapshots


def available_captures(config, *, known, verified, discoverer=None, capturer=None):
    """Both generic and MIA ingestion share the same selected-Bronze boundary."""
    if config.intake_path is not None:
        from .pipeline_intake import selected_snapshots

        yield from selected_snapshots(config)
        return
    for origin, path, stamp in (discoverer or discover)(config):
        snapshot = (capturer or stable_capture)(
            config, origin, path, stamp, known=known, verified=verified
        )
        if snapshot is not None:
            yield snapshot


def _read_limited(path: Path, maximum: int) -> bytes:
    with path.open("rb") as file:
        data = file.read(maximum + 1)
    if len(data) > maximum:
        raise CaptureError(f"Archivo por encima del límite de captura: {path}")
    return data


def stable_capture(
    config: Config, origin: str, path: Path, producer_time: str | None, *, known=None, verified=None
) -> Snapshot | None:
    """Skip a file that is still being copied. The rest of the family continues."""
    try:
        return capture(config, origin, path, producer_time, known=known, verified=verified)
    except CaptureError:
        return None


def capture(
    config: Config, origin: str, path: Path, producer_time: str | None, *, known=None, verified=None
) -> Snapshot:
    for attempt in range(config.capture_attempts):
        try:
            before = path.stat()
            data = _read_limited(path, config.max_file_bytes)
            sha = hashlib.sha256(data).hexdigest()
            prior = (known or {}).get((origin, sha))
            if prior is not None:
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise CaptureError(f"La entrada sigue cambiando: {path}")
                identity = (origin, sha, prior.bronze_path)
                bronze = Path(prior.bronze_path)
                if verified is None or identity not in verified:
                    bronze = bronze.resolve()
                    if not bronze.is_relative_to(config.bronze_root):
                        raise IntegrityError("Evidencia conocida fuera de Bronze")
                    with bronze.open("rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != sha:
                            raise IntegrityError("Copia Bronze alterada")
                    if verified is not None:
                        verified.add(identity)
                # The bytes match a previously stable immutable capture. Always hash
                # input, including overwrites that preserve filename/size/mtime.
                return Snapshot(
                    origin,
                    str(path),
                    sha,
                    str(bronze),
                    len(data),
                    after.st_mtime_ns,
                    now(),
                    producer_time,
                )
            time.sleep(config.stable_wait_seconds)
            again = _read_limited(path, config.max_file_bytes)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, data) != (
                after.st_size,
                after.st_mtime_ns,
                again,
            ):
                raise CaptureError(f"La entrada sigue cambiando: {path}")
            received_at = now()
            target = (
                config.bronze_root
                / config.plant
                / origin
                / config.family
                / received_at[:10]
                / f"{sha}.csv"
            )
            snapshot = Snapshot(
                origin,
                str(path),
                sha,
                str(target),
                len(data),
                after.st_mtime_ns,
                received_at,
                producer_time,
            )
            publish_capture(config, snapshot, data)
            return snapshot

        except (OSError, CaptureError) as exc:
            if attempt == config.capture_attempts - 1:
                raise CaptureError(f"No se pudo estabilizar {path}: {exc}") from exc
            time.sleep(config.stable_wait_seconds * (attempt + 1))
    raise AssertionError("Captura sin resultado")


def capture_identity(family, snapshot):
    return digest([snapshot.origin, family, snapshot.source_path, snapshot.sha256])


def _sync_capture_directories(root, directory):
    if os.name == "nt":
        return
    while directory.is_relative_to(root):
        handle = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
        if directory == root:
            break
        directory = directory.parent


def _capture_path(config, origin, target, *, recovery=False):
    origin_path = config.bronze_root / config.plant / origin
    family_path = origin_path / config.family
    action = "Capture recovery" if recovery else "Capture"
    # On Windows, resolving a missing path can retain a short-name ancestor.
    # Another family can create that ancestor between the independent resolutions.
    for _ in range(3 if os.name == "nt" else 1):
        if target.is_symlink():
            raise IntegrityError(f"{action} cannot follow a symlink")
        namespace = family_path.resolve()
        resolved = target.resolve()
        if namespace.parent != origin_path.resolve():
            error = f"{action} family escapes its origin"
        elif not namespace.is_relative_to(
            config.bronze_root.resolve()
        ) or not resolved.is_relative_to(namespace):
            error = f"{action} escapes its Bronze namespace"
        else:
            return resolved
    raise IntegrityError(error)


def publish_capture(config, snapshot, data):
    """Durable bytes with a recoverable descriptor before publishing the blob."""
    if snapshot.origin not in dict(config.sources):
        raise IntegrityError("Capture belongs to an unknown origin")
    target = _capture_path(config, snapshot.origin, Path(snapshot.bronze_path))
    if len(data) != snapshot.size or hashlib.sha256(data).hexdigest() != snapshot.sha256:
        raise IntegrityError("Capture differs from its stable source evidence")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary, fd = None, None
    try:
        if not target.exists():
            fd, temporary = tempfile.mkstemp(prefix=".capture-", dir=target.parent)
        if config.intake_path is not None:
            from .runtime import atomic_json

            root = config.intake_path.parent / "capture-intents"
            root.mkdir(exist_ok=True)
            descriptor = root / (capture_identity(config.family, snapshot) + ".json")
            atomic_json(
                descriptor,
                {
                    "family": config.family,
                    "snapshot": snapshot.descriptor(),
                    "staging_path": temporary,
                },
            )
            _sync_capture_directories(config.intake_path.parent, root)
        if temporary is not None:
            stream = os.fdopen(fd, "wb")
            fd = None
            with stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, target)
        elif (
            hashlib.sha256(_read_limited(target, config.max_file_bytes)).hexdigest()
            != snapshot.sha256
        ):
            raise IntegrityError(f"Copia Bronze alterada: {target}")
        _sync_capture_directories(config.bronze_root, target.parent)
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def recover_capture(config, document):
    snapshot = Snapshot(**document["snapshot"])
    if snapshot.origin not in dict(config.sources):
        raise IntegrityError("Capture recovery belongs to an unknown origin")
    target = Path(snapshot.bronze_path)
    _capture_path(config, snapshot.origin, target, recovery=True)
    staged = Path(document["staging_path"]) if document.get("staging_path") else None
    if staged is not None and (
        staged.parent != target.parent
        or not staged.name.startswith(".capture-")
        or staged.is_symlink()
    ):
        raise IntegrityError("Capture staging file escapes its namespace")
    candidate = target if target.exists() else staged
    if candidate is None or not candidate.is_file():
        raise IntegrityError("Stable capture publication was interrupted before durable bytes")
    data = _read_limited(candidate, config.max_file_bytes)
    if len(data) != snapshot.size or hashlib.sha256(data).hexdigest() != snapshot.sha256:
        raise IntegrityError("Interrupted capture has incomplete or changed bytes")
    if not target.exists():
        os.link(staged, target)
        _sync_capture_directories(config.bronze_root, target.parent)
    if staged is not None:
        staged.unlink(missing_ok=True)
    return snapshot
