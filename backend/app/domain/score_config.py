"""Score configuration contract shared by the legacy reader and the Gold publisher.

Validation, user overrides and the effective-configuration projection live here so
both readers agree on one definition. Scalar formulas stay in ``score_math``.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Final

from app.domain.errors import ApplicationError
from app.domain.score_math import _mapping, _ScoreConfig

SEVERITIES: Final[tuple[str, ...]] = ("Critico", "Grave", "Mayor", "Menor", "Leve")
SHIFT_NAMES: Final[tuple[str, ...]] = ("Manana", "Tarde", "Noche")

ScoreConfig = _ScoreConfig


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def invalid_score_config(message: str) -> ApplicationError:
    return ApplicationError("INVALID_SCORE_CONFIG", message, status_code=503)


def validate_score_config(raw: Any) -> dict[str, Any]:
    """Return ``raw`` when it is a complete, internally consistent score configuration."""
    if not isinstance(raw, dict):
        raise invalid_score_config("La raiz debe ser un objeto JSON.")
    version = as_int(raw.get("version"))
    if version is None or version < 1:
        raise invalid_score_config("version debe ser un entero positivo.")

    score = _required_mapping(raw, "score")
    weights = _required_mapping(raw, "weights")
    mia = _required_mapping(raw, "mia")
    aql = _required_mapping(raw, "aql")
    dar = _required_mapping(raw, "dar")
    zc = _required_mapping(raw, "zc")

    _required_non_negative(weights, "mia_defect")
    _required_non_negative(weights, "dar")
    _required_non_negative(weights, "zc")
    _validate_categories(_required_mapping(weights, "categories"), "weights.categories")
    _validate_categories(_required_mapping(aql, "category_limits_percent"), "aql")

    absolute_rate = _required_mapping(mia, "absolute_rate_factor")
    _required_non_negative(absolute_rate, "min_relevant_percent")
    _required_positive(absolute_rate, "full_weight_span_percent")

    dar_thresholds = _required_mapping(dar, "progressive_thresholds_percent")
    score_5 = _required_non_negative(dar_thresholds, "score_5")
    score_10 = _required_positive(dar_thresholds, "score_10")
    if score_5 > score_10:
        raise invalid_score_config("dar.score_5 no puede superar dar.score_10.")

    target = _required_mapping(zc, "target_percent")
    _required_positive(target, "default")
    _required_positive(zc, "penalty_divisor_points")

    interpretation = _required_mapping(score, "interpretation")
    good_max = _required_non_negative(interpretation, "good_max")
    observed_max = _required_non_negative(interpretation, "observed_max")
    bad_max = _required_non_negative(interpretation, "bad_max")
    if not good_max <= observed_max <= bad_max:
        raise invalid_score_config("Los limites de estado deben ser crecientes.")
    return raw


def apply_score_config_override(
    config: ScoreConfig,
    override: Mapping[str, Any] | None,
) -> ScoreConfig:
    """Apply the numeric overrides a user may send with a query; unknown keys are ignored."""
    if not override:
        return config
    raw = deepcopy(dict(config.raw))
    root = _mapping(override)

    aql_override = _mapping(root.get("aql"))
    _apply_numeric_overrides(
        raw,
        ("aql", "category_limits_percent"),
        _mapping(aql_override.get("category_limits_percent")),
        SEVERITIES,
    )

    weights_override = _mapping(root.get("weights"))
    _set_nested_float(raw, ("weights", "mia_defect"), weights_override.get("mia_defect"))
    _set_nested_float(raw, ("weights", "dar"), weights_override.get("dar"))
    _set_nested_float(raw, ("weights", "zc"), weights_override.get("zc"))
    _apply_numeric_overrides(
        raw,
        ("weights", "categories"),
        _mapping(weights_override.get("categories")),
        SEVERITIES,
    )

    dar_override = _mapping(root.get("dar"))
    _apply_numeric_overrides(
        raw,
        ("dar", "progressive_thresholds_percent"),
        _mapping(dar_override.get("progressive_thresholds_percent")),
        ("score_5", "score_10"),
    )

    zc_override = _mapping(root.get("zc"))
    _set_nested_float(
        raw,
        ("zc", "target_percent", "default"),
        _mapping(zc_override.get("target_percent")).get("default"),
    )

    score_override = _mapping(root.get("score"))
    _apply_numeric_overrides(
        raw,
        ("score", "interpretation"),
        _mapping(score_override.get("interpretation")),
        ("good_max", "observed_max", "bad_max"),
    )
    return ScoreConfig(version=config.version, raw=_mapping(raw))


def effective_score_config(config: ScoreConfig) -> dict[str, Any]:
    """Project the parameters that are echoed back to the client, with defaults filled in."""
    weights = _mapping(config.weights)
    category_weights = _mapping(weights.get("categories"))
    aql_limits = _mapping(config.aql.get("category_limits_percent"))
    dar_thresholds = _mapping(config.dar.get("progressive_thresholds_percent"))
    zc_target = _mapping(config.zc.get("target_percent"))
    interpretation = _mapping(config.score.get("interpretation"))
    return {
        "aql": {
            "category_limits_percent": {
                severity: float(aql_limits.get(severity, 0.0)) for severity in SEVERITIES
            }
        },
        "weights": {
            "mia_defect": float(weights.get("mia_defect", 1.0)),
            "categories": {
                severity: float(category_weights.get(severity, 0.0)) for severity in SEVERITIES
            },
            "dar": float(weights.get("dar", 8.0)),
            "zc": float(weights.get("zc", 2.0)),
        },
        "dar": {
            "progressive_thresholds_percent": {
                "score_5": float(dar_thresholds.get("score_5", 0.1)),
                "score_10": float(dar_thresholds.get("score_10", 0.4)),
            }
        },
        "zc": {
            "target_percent": {
                "default": float(zc_target.get("default", 95.0)),
            }
        },
        "score": {
            "interpretation": {
                "good_max": float(interpretation.get("good_max", 40.0)),
                "observed_max": float(interpretation.get("observed_max", 80.0)),
                "bad_max": float(interpretation.get("bad_max", 120.0)),
            }
        },
    }


def _apply_numeric_overrides(
    raw: dict[str, Any],
    path: tuple[str, ...],
    values: Mapping[str, Any],
    keys: tuple[str, ...],
) -> None:
    for key in keys:
        _set_nested_float(raw, (*path, key), values.get(key))


def _set_nested_float(raw: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    number = as_float(value)
    if number is None:
        return
    target: dict[str, Any] = raw
    for key in path[:-1]:
        child = target.get(key)
        if not isinstance(child, dict):
            child = {}
            target[key] = child
        target = child
    target[path[-1]] = number


def _validate_categories(values: Mapping[str, Any], path: str) -> None:
    for severity in SEVERITIES:
        if severity not in values:
            raise invalid_score_config(f"Falta {path}.{severity}.")
        if as_float(values[severity]) is None or float(values[severity]) < 0:
            raise invalid_score_config(f"{path}.{severity} debe ser un numero no negativo.")


def _required_mapping(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise invalid_score_config(f"Falta el objeto {key}.")
    return value


def _required_non_negative(raw: Mapping[str, Any], key: str) -> float:
    value = as_float(raw.get(key))
    if value is None or value < 0:
        raise invalid_score_config(f"{key} debe ser un numero no negativo.")
    return value


def _required_positive(raw: Mapping[str, Any], key: str) -> float:
    value = as_float(raw.get(key))
    if value is None or value <= 0:
        raise invalid_score_config(f"{key} debe ser un numero positivo.")
    return value
