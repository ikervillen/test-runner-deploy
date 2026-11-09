from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.errors import ApplicationError
from app.infrastructure.ingestion.batches import publish_batch, validate_batch
from app.infrastructure.repositories.segment_dar_profiles import JsonSegmentDarProfileRepository


def test_validated_batch_is_published_atomically(tmp_path: Path) -> None:
    batch_root = tmp_path / "staging" / "batch-001"
    _write_batch(batch_root)
    profiles = JsonSegmentDarProfileRepository(batch_root / "dar_profiles.json")

    batch = validate_batch(batch_root, profiles)
    release = publish_batch(batch_root, tmp_path / "published", batch)

    assert release.name == "batch-001"
    assert (release / "validation.json").is_file()
    assert json.loads((tmp_path / "published" / "current.json").read_text()) == {
        "batch_id": "batch-001"
    }


def test_batch_rejects_artifacts_outside_staging(tmp_path: Path) -> None:
    batch_root = tmp_path / "staging" / "batch-001"
    _write_batch(batch_root)
    descriptor = json.loads((batch_root / "batch.json").read_text())
    descriptor["artifacts"][0]["path"] = "../../outside"
    (batch_root / "batch.json").write_text(json.dumps(descriptor), encoding="utf-8")
    profiles = JsonSegmentDarProfileRepository(batch_root / "dar_profiles.json")

    with pytest.raises(ApplicationError, match="fuera del lote"):
        validate_batch(batch_root, profiles)


def _write_batch(root: Path) -> None:
    root.mkdir(parents=True)
    for name in (
        "mia",
        "alerts_rejections",
        "inspected_totals",
        "hot_end_yield",
        "severity",
        "mdl_normalization",
        "mia_catalog",
    ):
        (root / "payload" / name).mkdir(parents=True, exist_ok=True)
        (root / "payload" / name / "source.txt").write_text(name, encoding="utf-8")
    (root / "dar_profiles.json").write_text(
        json.dumps(
            {
                "default_profile_id": "profile-v1",
                "profiles": [
                    {
                        "id": "profile-v1",
                        "segment_id": "segment-v1",
                        "segment_label": "Segmento",
                        "version": 1,
                        "groups": [{"id": "dar", "label": "DAR", "normalized_codes": [1]}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "batch.json").write_text(
        json.dumps(
            {
                "batch_id": "batch-001",
                "artifacts": [
                    {"name": name, "path": f"payload/{name}"}
                    for name in (
                        "mia",
                        "alerts_rejections",
                        "inspected_totals",
                        "hot_end_yield",
                        "severity",
                        "mdl_normalization",
                        "mia_catalog",
                    )
                ],
                "productions": [
                    {
                        "id": "AV23_PROD_100",
                        "line": "AV23",
                        "start_day": "2026-01-01",
                        "end_day": "2026-01-02",
                        "bottle_model_id": "model-v1",
                        "segment_id": "segment-v1",
                        "dar_profile_id": "profile-v1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
