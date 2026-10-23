from contextlib import closing
from dataclasses import replace

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.control_trial import trial
from ingesta_delta_lake.service import run


def test_isolated_trial_never_replaces_source_and_keeps_binding(config, source, tmp_path):
    run(config)
    before = config.control_path.read_bytes()
    delta_version = DeltaTable(str(config.table_path)).version()
    report = trial(config, tmp_path / "trial")
    assert report["status"] == "TRIAL_VERIFIED" and report["source_sql_changes"] == 0
    assert config.control_path.read_bytes() == before
    assert DeltaTable(str(config.table_path)).version() == delta_version
    cloned = replace(config, control_root=tmp_path / "trial" / config.family)
    with closing(Control(cloned)) as control:
        assert control.compact
    with closing(Control(config)) as control:
        assert not control.compact
    with pytest.raises(FileExistsError):
        trial(config, tmp_path / "trial")


def test_trial_rejects_overlapping_roots(config, source):
    run(config)
    with pytest.raises(IntegrityError):
        trial(config, config.control_root / "trial")


def test_new_control_uses_explicit_format_without_migrating_existing(config, source):
    run(replace(config, control_format=3))
    with closing(Control(config)) as control:
        assert control.compact
        assert not control.db.execute("SELECT 1 FROM row_intents").fetchone()
