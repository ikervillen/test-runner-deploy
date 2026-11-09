"""Versioned score configuration loaded from disk and validated by the domain."""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.errors import ApplicationError
from app.domain.score_config import ScoreConfig, validate_score_config


def load_score_config(path: Path) -> ScoreConfig:
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplicationError(
            "INVALID_SCORE_CONFIG",
            "No se pudo leer la configuracion versionada del score.",
            status_code=503,
            details={"path": str(path)},
        ) from exc
    validated = validate_score_config(raw)
    return ScoreConfig(version=int(validated["version"]), raw=validated)
