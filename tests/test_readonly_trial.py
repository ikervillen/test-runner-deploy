import sqlite3
from contextlib import closing

import pytest

from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.control_inventory import inventory
from ingesta_delta_lake.control_trial import trial
from ingesta_delta_lake.service import run


def test_stopped_trial_opens_source_immutable_without_creating_sidecars(
    config, source, tmp_path, monkeypatch
):
    run(config)
    calls = []
    original = sqlite3.connect

    def connect(path, *args, **kwargs):
        calls.append(str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert trial(config, tmp_path / "readonly-trial")["status"] == "TRIAL_VERIFIED"
    source_connections = [p for p in calls if p.startswith(config.control_path.as_uri())]
    assert source_connections and all("immutable=1" in p for p in source_connections)
    assert not config.control_path.with_name(config.control_path.name + "-wal").exists()


def test_stopped_inventory_never_ignores_uncheckpointed_wal(config, source, tmp_path):
    run(config)
    with closing(sqlite3.connect(config.control_path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO settings VALUES('committed_in_wal','1')")
        db.commit()
        with pytest.raises(IntegrityError, match="WAL"):
            inventory(config.control_path, stopped=True)
        with pytest.raises(IntegrityError, match="WAL"):
            trial(config, tmp_path / "blocked-trial")
