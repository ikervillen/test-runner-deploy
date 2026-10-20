"""Descubrimiento de las dos modalidades y captura inmutable de revisiones."""

import hashlib
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .common import CaptureError, IntegrityError, now
from .config import Config

FAMILY = "SIP_MODELO"


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
    result = []
    for origin, folder in config.sources:
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() != ".csv":
                continue
            if not path.stem.upper().endswith(config.family):
                continue  # No incorporar otras familias antes de su entrega.
            resolved = path.resolve()
            if not resolved.is_relative_to(folder):
                raise CaptureError(f"Archivo enlazado fuera del origen: {path}")
            if not resolved.is_file():
                raise CaptureError(f"La entrada no es un archivo: {path}")
            result.append((origin, resolved, parse_name(path.name, config.family)))
    return result


def capture_inputs(config, control, attempt):
    """Stable cache speeds aliases/replay; every live file is still hashed.

    Failed attempts retain all captured revisions, including overwritten/rotated
    paths. Only finish_attempt after durable handling acknowledges this queue.
    """
    known = control.known_captures()
    snapshots, included = [], set()
    for origin, path, stamp in discover(config):
        snapshot = capture(config, origin, path, stamp, known=known)
        control.receipts(attempt, [snapshot])
        snapshots.append(snapshot)
        included.add((origin, snapshot.sha256))
        known.setdefault((origin, snapshot.sha256), snapshot)
    for snapshot in orphan_captures(config, known):
        control.receipts(attempt, [snapshot])
        known[(snapshot.origin, snapshot.sha256)] = snapshot
    for key, snapshot in control.known_captures(pending=True).items():
        if key not in included:
            snapshots.append(snapshot)
    return snapshots


def _read_limited(path: Path, maximum: int) -> bytes:
    with path.open("rb") as file:
        data = file.read(maximum + 1)
    if len(data) > maximum:
        raise CaptureError(f"Archivo por encima del límite de captura: {path}")
    return data


def capture(
    config: Config, origin: str, path: Path, producer_time: str | None, *, known=None
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
                bronze = Path(prior.bronze_path).resolve()
                if not bronze.is_relative_to(config.bronze_root):
                    raise IntegrityError("Evidencia conocida fuera de Bronze")
                with bronze.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != sha:
                        raise IntegrityError("Copia Bronze alterada")
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
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                    raise IntegrityError(f"Copia Bronze alterada: {target}")
            else:
                fd, temporary = tempfile.mkstemp(prefix=".capture-", dir=target.parent)
                try:
                    with os.fdopen(fd, "wb") as file:
                        file.write(data)
                        file.flush()
                        os.fsync(file.fileno())
                    # Crear sin reemplazar una evidencia existente. Solo filesystem local.
                    os.link(temporary, target)
                    if os.name != "nt":
                        directory = target.parent
                        while directory.is_relative_to(config.bronze_root):
                            handle = os.open(directory, os.O_RDONLY)
                            try:
                                os.fsync(handle)
                            finally:
                                os.close(handle)
                            if directory == config.bronze_root:
                                break
                            directory = directory.parent
                finally:
                    Path(temporary).unlink(missing_ok=True)
            return Snapshot(
                origin,
                str(path),
                sha,
                str(target),
                len(data),
                after.st_mtime_ns,
                received_at,
                producer_time,
            )
        except (OSError, CaptureError) as exc:
            if attempt == config.capture_attempts - 1:
                raise CaptureError(f"No se pudo estabilizar {path}: {exc}") from exc
            time.sleep(config.stable_wait_seconds * (attempt + 1))
    raise AssertionError("Captura sin resultado")
