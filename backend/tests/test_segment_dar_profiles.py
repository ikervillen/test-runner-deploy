from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.api.errors import ApplicationError
from app.domain.models import Production
from app.infrastructure.repositories.segment_dar_profiles import JsonSegmentDarProfileRepository


def test_profile_uses_normalized_codes_and_default_profile(tmp_path: Path) -> None:
    path = tmp_path / "segment_dar_profiles.json"
    _write_profiles(path)
    repository = JsonSegmentDarProfileRepository(path)

    profile = repository.profile_for(_production())

    assert profile.id == "vino_ligero_v1"
    assert profile.group_for_code(53) == "Ovalizacion"
    assert profile.group_for_code(414) == "Verticalidad"
    assert profile.group_for_code(999) is None


def test_profile_rejects_segment_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "segment_dar_profiles.json"
    _write_profiles(path)
    repository = JsonSegmentDarProfileRepository(path)

    with pytest.raises(ApplicationError, match="no corresponde") as exc_info:
        repository.profile_for(_production(segment_id="premium"))

    assert exc_info.value.code == "DAR_PROFILE_SEGMENT_MISMATCH"


def test_profile_rejects_code_assigned_to_two_groups(tmp_path: Path) -> None:
    path = tmp_path / "segment_dar_profiles.json"
    _write_profiles(path, duplicate_code=True)
    repository = JsonSegmentDarProfileRepository(path)

    with pytest.raises(ApplicationError, match="mas de un grupo") as exc_info:
        repository.profile_for(_production())

    assert exc_info.value.code == "INVALID_DAR_PROFILE_CATALOG"


def test_profile_rejects_unknown_explicit_profile(tmp_path: Path) -> None:
    path = tmp_path / "segment_dar_profiles.json"
    _write_profiles(path)
    repository = JsonSegmentDarProfileRepository(path)

    with pytest.raises(ApplicationError, match="No existe") as exc_info:
        repository.profile_for(_production(dar_profile_id="inexistente"))

    assert exc_info.value.code == "DAR_PROFILE_NOT_FOUND"


def test_profile_catalog_requires_existing_default_profile(tmp_path: Path) -> None:
    path = tmp_path / "segment_dar_profiles.json"
    _write_profiles(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["default_profile_id"] = "inexistente"
    path.write_text(json.dumps(raw), encoding="utf-8")
    repository = JsonSegmentDarProfileRepository(path)

    with pytest.raises(ApplicationError, match="default_profile_id") as exc_info:
        repository.profile_for(_production())

    assert exc_info.value.code == "INVALID_DAR_PROFILE_CATALOG"


def _production(
    *,
    segment_id: str | None = None,
    dar_profile_id: str | None = None,
) -> Production:
    return Production(
        id="AV32_PROD_001",
        line="AV32",
        start_day=date(2024, 1, 1),
        end_day=date(2024, 1, 2),
        branches=(),
        segment_id=segment_id,
        dar_profile_id=dar_profile_id,
    )


def _write_profiles(path: Path, *, duplicate_code: bool = False) -> None:
    second_group_codes = [53] if duplicate_code else [414]
    path.write_text(
        json.dumps(
            {
                "default_profile_id": "vino_ligero_v1",
                "profiles": [
                    {
                        "id": "vino_ligero_v1",
                        "segment_id": "vino_ligero",
                        "segment_label": "Vino Ligero",
                        "version": 1,
                        "groups": [
                            {
                                "id": "ovalizacion",
                                "label": "Ovalizacion",
                                "normalized_codes": [53],
                            },
                            {
                                "id": "verticalidad",
                                "label": "Verticalidad",
                                "normalized_codes": second_group_codes,
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
