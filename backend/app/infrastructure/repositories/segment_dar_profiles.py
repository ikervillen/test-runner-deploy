from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.api.errors import ApplicationError
from app.domain.models import Production


@dataclass(frozen=True, slots=True)
class DarDefectGroup:
    id: str
    label: str
    normalized_codes: frozenset[int]


@dataclass(frozen=True, slots=True)
class SegmentDarProfile:
    id: str
    segment_id: str
    segment_label: str
    version: int
    groups: tuple[DarDefectGroup, ...]

    def group_for_code(self, code: int | None) -> str | None:
        if code is None:
            return None
        for group in self.groups:
            if code in group.normalized_codes:
                return group.label
        return None


class JsonSegmentDarProfileRepository:
    """Loads auditable DAR profiles keyed by normalized MDL defect code."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._cached_version = -1
        self._default_profile_id = ""
        self._profiles: dict[str, SegmentDarProfile] = {}

    def profile_for(self, production: Production) -> SegmentDarProfile:
        self._ensure_loaded()
        profile_id = production.dar_profile_id or self._default_profile_id
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ApplicationError(
                "DAR_PROFILE_NOT_FOUND",
                "No existe el perfil DAR asignado a la produccion.",
                status_code=503,
                details={"production_id": production.id, "dar_profile_id": profile_id},
            )
        if production.segment_id and production.segment_id != profile.segment_id:
            raise ApplicationError(
                "DAR_PROFILE_SEGMENT_MISMATCH",
                "El perfil DAR no corresponde con el segmento de la produccion.",
                status_code=503,
                details={
                    "production_id": production.id,
                    "segment_id": production.segment_id,
                    "profile_segment_id": profile.segment_id,
                },
            )
        return profile

    def version(self) -> int:
        try:
            return self._path.stat().st_mtime_ns
        except FileNotFoundError as exc:
            raise ApplicationError(
                "DAR_PROFILE_CATALOG_NOT_FOUND",
                "No se encuentra el catalogo de perfiles DAR.",
                status_code=503,
                details={"path": str(self._path)},
            ) from exc

    def _ensure_loaded(self) -> None:
        version = self.version()
        if version == self._cached_version:
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApplicationError(
                "INVALID_DAR_PROFILE_CATALOG",
                "El catalogo de perfiles DAR no es valido.",
                status_code=503,
                details={"path": str(self._path)},
            ) from exc
        self._default_profile_id, self._profiles = _parse_profiles(raw)
        self._cached_version = version


def _parse_profiles(raw: Any) -> tuple[str, dict[str, SegmentDarProfile]]:
    if not isinstance(raw, dict):
        raise _invalid_catalog("El catalogo debe ser un objeto JSON.")
    default_profile_id = raw.get("default_profile_id")
    profiles_raw = raw.get("profiles")
    if not isinstance(default_profile_id, str) or not default_profile_id.strip():
        raise _invalid_catalog("Falta default_profile_id.")
    if not isinstance(profiles_raw, list) or not profiles_raw:
        raise _invalid_catalog("profiles debe contener al menos un perfil.")

    profiles: dict[str, SegmentDarProfile] = {}
    for item in profiles_raw:
        if not isinstance(item, dict):
            raise _invalid_catalog("Cada perfil DAR debe ser un objeto.")
        profile_id = _required_text(item, "id")
        if profile_id in profiles:
            raise _invalid_catalog(f"El perfil DAR {profile_id} esta duplicado.")
        groups_raw = item.get("groups")
        if not isinstance(groups_raw, list) or not groups_raw:
            raise _invalid_catalog(f"El perfil DAR {profile_id} no contiene grupos.")
        groups: list[DarDefectGroup] = []
        observed_codes: set[int] = set()
        for group_raw in groups_raw:
            if not isinstance(group_raw, dict):
                raise _invalid_catalog(f"El perfil DAR {profile_id} contiene un grupo invalido.")
            codes_raw = group_raw.get("normalized_codes")
            if not isinstance(codes_raw, list) or not codes_raw:
                group_id = _required_text(group_raw, "id")
                raise _invalid_catalog(f"El grupo DAR {group_id} no contiene codigos normalizados.")
            codes = frozenset(_positive_int(code) for code in codes_raw)
            duplicated = observed_codes.intersection(codes)
            if duplicated:
                raise _invalid_catalog(
                    "El perfil DAR "
                    f"{profile_id} asigna codigos a mas de un grupo: {sorted(duplicated)}."
                )
            observed_codes.update(codes)
            groups.append(
                DarDefectGroup(
                    id=_required_text(group_raw, "id"),
                    label=_required_text(group_raw, "label"),
                    normalized_codes=codes,
                )
            )
        profiles[profile_id] = SegmentDarProfile(
            id=profile_id,
            segment_id=_required_text(item, "segment_id"),
            segment_label=_required_text(item, "segment_label"),
            version=_positive_int(item.get("version")),
            groups=tuple(groups),
        )
    if default_profile_id not in profiles:
        raise _invalid_catalog("default_profile_id no existe en profiles.")
    return default_profile_id, profiles


def _required_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_catalog(f"Falta {key}.")
    return value.strip()


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        raise _invalid_catalog("Los codigos y versiones deben ser enteros positivos.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise _invalid_catalog("Los codigos y versiones deben ser enteros positivos.") from exc
    if parsed <= 0:
        raise _invalid_catalog("Los codigos y versiones deben ser enteros positivos.")
    return parsed


def _invalid_catalog(message: str) -> ApplicationError:
    return ApplicationError("INVALID_DAR_PROFILE_CATALOG", message, status_code=503)
