from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.api.errors import ApplicationError
from app.domain.models import Production
from app.infrastructure.repositories.segment_dar_profiles import JsonSegmentDarProfileRepository

REQUIRED_ARTIFACTS = frozenset(
    {
        "mia",
        "alerts_rejections",
        "inspected_totals",
        "hot_end_yield",
        "severity",
        "mdl_normalization",
        "mia_catalog",
    }
)


@dataclass(frozen=True, slots=True)
class BatchArtifact:
    name: str
    path: Path
    checksum: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ValidatedBatch:
    batch_id: str
    artifacts: tuple[BatchArtifact, ...]
    production_count: int


def validate_batch(
    batch_root: Path,
    dar_profiles: JsonSegmentDarProfileRepository,
) -> ValidatedBatch:
    """Validate a staged, self-contained release before publication."""
    root = batch_root.resolve()
    descriptor_path = root / "batch.json"
    try:
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _invalid_batch("batch.json no existe o no es JSON valido.") from exc
    if not isinstance(descriptor, dict):
        raise _invalid_batch("batch.json debe contener un objeto.")
    batch_id = _required_text(descriptor, "batch_id")
    artifacts_raw = descriptor.get("artifacts")
    if not isinstance(artifacts_raw, list):
        raise _invalid_batch("artifacts debe ser una lista.")

    artifacts: list[BatchArtifact] = []
    observed_names: set[str] = set()
    for raw in artifacts_raw:
        if not isinstance(raw, dict):
            raise _invalid_batch("Cada artefacto debe ser un objeto.")
        name = _required_text(raw, "name")
        if name in observed_names:
            raise _invalid_batch(f"El artefacto {name} esta duplicado.")
        observed_names.add(name)
        path = _safe_path(root, _required_text(raw, "path"))
        if not path.exists():
            raise _invalid_batch(f"No existe el artefacto {name}: {path}.")
        artifacts.append(
            BatchArtifact(
                name=name,
                path=path,
                checksum=_checksum(path),
                size_bytes=_size_bytes(path),
            )
        )
    missing = sorted(REQUIRED_ARTIFACTS - observed_names)
    if missing:
        raise _invalid_batch(f"Faltan artefactos obligatorios: {', '.join(missing)}.")
    _validate_productions(descriptor.get("productions"), dar_profiles)
    return ValidatedBatch(
        batch_id,
        tuple(sorted(artifacts, key=lambda item: item.name)),
        len(descriptor["productions"]),
    )


def publish_batch(batch_root: Path, published_root: Path, batch: ValidatedBatch) -> Path:
    """Publish an already validated batch through an immutable release directory."""
    source = batch_root.resolve()
    destination_root = published_root.resolve()
    releases = destination_root / "releases"
    release = releases / batch.batch_id
    if release.exists():
        raise ApplicationError(
            "BATCH_ALREADY_PUBLISHED",
            "El lote ya existe en publicaciones.",
            status_code=409,
            details={"batch_id": batch.batch_id},
        )
    releases.mkdir(parents=True, exist_ok=True)
    temporary = releases / f".{batch.batch_id}.publishing"
    if temporary.exists():
        raise _invalid_batch("Existe una publicacion incompleta con el mismo identificador.")
    shutil.copytree(source, temporary)
    validation = {
        "batch_id": batch.batch_id,
        "artifacts": [asdict(item) | {"path": str(item.path)} for item in batch.artifacts],
    }
    (temporary / "validation.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    os.replace(temporary, release)
    pointer = destination_root / "current.json"
    pointer_tmp = destination_root / ".current.json.tmp"
    pointer_tmp.write_text(json.dumps({"batch_id": batch.batch_id}), encoding="utf-8")
    os.replace(pointer_tmp, pointer)
    return release


def _validate_productions(raw: Any, dar_profiles: JsonSegmentDarProfileRepository) -> None:
    if not isinstance(raw, list) or not raw:
        raise _invalid_batch("productions debe contener al menos una produccion.")
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise _invalid_batch("Cada produccion debe ser un objeto.")
        production_id = _required_text(item, "id")
        if production_id in seen:
            raise _invalid_batch(f"La produccion {production_id} esta duplicada.")
        seen.add(production_id)
        production = Production(
            id=production_id,
            line=_required_text(item, "line"),
            start_day=date.fromisoformat(_required_text(item, "start_day")),
            end_day=date.fromisoformat(_required_text(item, "end_day")),
            branches=(),
            bottle_model_id=_required_text(item, "bottle_model_id"),
            segment_id=_required_text(item, "segment_id"),
            dar_profile_id=_required_text(item, "dar_profile_id"),
        )
        if production.start_day > production.end_day:
            raise _invalid_batch(f"La produccion {production_id} tiene un intervalo invalido.")
        dar_profiles.profile_for(production)


def _safe_path(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise _invalid_batch("Un artefacto apunta fuera del lote de staging.")
    return path


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    paths: tuple[Path, ...]
    if path.is_file():
        paths = (path,)
    else:
        paths = tuple(sorted(item for item in path.rglob("*") if item.is_file()))
    for item in paths:
        digest.update(item.relative_to(path.parent if path.is_file() else path).as_posix().encode())
        with item.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def _size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_batch(f"Falta {key}.")
    return value.strip()


def _invalid_batch(message: str) -> ApplicationError:
    return ApplicationError("INVALID_INGESTION_BATCH", message, status_code=422)
