"""Boundary cases for the scalar formulas shared by the historical and Gold paths."""

import pytest

from app.domain.score_math import (
    _dar_score,
    _dar_threshold_label,
    _ScoreConfig,
    _sigma_score,
    _zero_aql_score,
    _zero_aql_threshold_label,
)


@pytest.mark.parametrize(
    "exposure,expected,label",
    [
        (0, 0, "Sin presencia"),
        (0.005, 5, "0 < exposicion <= 0.01 %"),
        (0.01, 5, "0 < exposicion <= 0.01 %"),
        (0.03, 5, "0.01 % < exposicion < 0.05 %"),
        (0.05, 10, "exposicion >= 0.05 %"),
    ],
)
def test_zero_aql_policy_boundaries(exposure, expected, label):
    assert _zero_aql_score(exposure, {}) == expected
    assert _zero_aql_threshold_label(exposure, {}) == label
    assert _dar_score(exposure, {}) == expected
    assert _dar_threshold_label(exposure, {}) == label


@pytest.mark.parametrize(
    "exposure,expected,label", [(0, 0, "Sin presencia"), (0.001, 10, "Presencia > 0")]
)
def test_disabled_progression_uses_explicit_presence_policy(exposure, expected, label):
    config = {
        "zero_or_near_zero_aql_policy": {"enabled": False},
        "progressive_thresholds_percent": {"enabled": False},
    }
    assert _zero_aql_score(exposure, config) == expected
    assert _zero_aql_threshold_label(exposure, config) == label
    assert _dar_score(exposure, config) == expected
    assert _dar_threshold_label(exposure, config) == label


def test_invalid_progressive_thresholds_fall_back_to_presence():
    config = {"progressive_thresholds_percent": {"score_5": 0.4, "score_10": 0.1}}
    assert _dar_score(0, config) == 0
    assert _dar_score(0.01, config) == 10
    assert _dar_threshold_label(0.01, config) == "Presencia DAR"


@pytest.mark.parametrize("z,expected", [(-1, 0), (0, 0), (1, 1), (1.5, 3), (2.72, 10), (10, 10)])
def test_sigma_default_curve_is_interpolated_and_capped(z, expected):
    assert _sigma_score(z, _ScoreConfig(1, {})) == pytest.approx(expected)
