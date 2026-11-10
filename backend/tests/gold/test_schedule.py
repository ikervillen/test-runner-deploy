import json
from datetime import UTC, datetime, timedelta

import pytest

from app.gold import schedule
from app.gold.cli import main
from app.gold.common import IntegrityError, atomic_json

from .test_pipeline import CUTOFF, bundle
from .test_pipeline import calculation as calculation


@pytest.mark.parametrize(
    ("moment", "cutoff"),
    [
        (datetime(2026, 11, 6, 15, 40), datetime(2026, 11, 6, 15, 40)),
        (datetime(2026, 11, 6, 15, 49, 59, 999), datetime(2026, 11, 6, 15, 40)),
        (datetime(2026, 11, 6, 0, 5), datetime(2026, 11, 6, 0, 0)),
    ],
)
def test_attempt_ceiling_is_floored_to_ten_minutes_without_lag(moment, cutoff):
    assert schedule.attempt_cutoff(moment) == cutoff


def test_attempt_ceiling_rejects_aware_moments():
    with pytest.raises(IntegrityError, match="local industrial moment"):
        schedule.attempt_cutoff(datetime(2026, 11, 6, 15, 40, tzinfo=UTC))


def test_publish_runs_one_idempotent_iteration(calculation, tmp_path, capsys, monkeypatch):
    snap, config = calculation
    atomic_json(tmp_path / "catalogs.json", bundle())
    atomic_json(tmp_path / "score.json", config)
    settings_path = tmp_path / "settings.json"
    atomic_json(
        settings_path,
        {
            "schema_id": "gold-settings-v1",
            "input_root": str(snap.root),
            "output_root": "output",
            "environment": "test",
            "plant": "AV",
            "score_config": "score.json",
            "catalog_bundle": "catalogs.json",
            "poll_seconds": 30,
            "max_rows_per_source": 1000,
            "max_total_rows": 10000,
        },
    )
    monkeypatch.setattr(schedule, "require_continuous_source", lambda root: None)

    def publish(at: datetime) -> dict:
        assert (
            main(
                [
                    "publish",
                    "--settings",
                    str(settings_path),
                    "--at",
                    at.isoformat(),
                    "--lane",
                    "all",
                ]
            )
            == 0
        )
        return json.loads(capsys.readouterr().out)

    def coverage(at: datetime) -> dict:
        assert main(["coverage", "--settings", str(settings_path), "--at", at.isoformat()]) == 0
        return json.loads(capsys.readouterr().out)

    before = coverage(CUTOFF)
    first = publish(CUTOFF)
    repeated = publish(CUTOFF + timedelta(minutes=9))
    after = coverage(CUTOFF)
    assert (first["status"], repeated["status"]) == ("PUBLISHED", "UNCHANGED")
    assert first["source"] == repeated["source"]
    assert first["source"]["publication_id"] == snap.publication_id
    assert first["coverage"] == {
        "ceiling": CUTOFF.isoformat(),
        "blocked_productions": 0,
        "max_lag_seconds": 0,
    }
    assert (before["status"], before["gold_input_publication_id"]) == ("READY", None)
    assert (after["status"], after["gold_input_publication_id"]) == (
        "NOTHING_NEW",
        snap.publication_id,
    )
