import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from ingesta_delta_lake import supervision_export as module
from ingesta_delta_lake.service import run


@pytest.mark.parametrize("format_number", [2, 3])
def test_deferred_observation_is_fresh_without_creating_an_attempt(
    config, source, monkeypatch, format_number
):
    config = replace(config, control_format=format_number)
    run(config)
    with closing(sqlite3.connect(config.control_path)) as db:
        before = list(db.iterdump())
    monkeypatch.setenv("INGESTA_SUPERVISION_EXPORT", "1")
    monkeypatch.setattr(module, "INTERVAL", 0)
    result = module.deferred(config)
    assert result["status"] == "DEFERRED_BACKLOG" and not result["attempted"]
    path = config.control_root / "supervision/control.sqlite.json"
    observation = json.loads(path.read_text())
    assert observation["complete"]
    assert observation["stats"]["dependency_status"] == "MIA_BACKLOG"
    assert observation["stats"]["last_attempt"]["status"] == "APPLIED"
    assert observation["stats"]["attempted_this_observation"] is False
    assert observation["publication"]["delta_version"] == 0
    with closing(sqlite3.connect(config.control_path)) as db:
        assert list(db.iterdump()) == before
    assert run(config)["status"] == "NO_CHANGE"
    assert json.loads(path.read_text())["stats"]["dependency_status"] is None
    assert len(list((config.table_path / "_delta_log").glob("*.json"))) == 1


def test_absent_deferred_control_is_not_created(config, monkeypatch):
    monkeypatch.setenv("INGESTA_SUPERVISION_EXPORT", "1")
    assert module.deferred(config)["dependency_status"] == "MIA_BACKLOG"
    assert not config.control_root.exists()
