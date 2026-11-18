"""Real Delta evidence preserves changed keys, reversions and crash recovery."""

from contextlib import closing
from dataclasses import replace

import pyarrow as pa
import pytest
from deltalake import DeltaTable
from test_cont_zc import line

from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.delta import DeltaStore
from ingesta_delta_lake.service import run
from ingesta_delta_lake.zc_storage import ZcControl, ZcStore


def changes(config, start, end):
    reader = DeltaTable(str(config.table_path)).load_cdf(start, end)
    return pa.RecordBatchReader.from_stream(reader).read_all().to_pylist()


def test_independent_catalog_changes_are_readable_after_restart(config, source):
    config = replace(config, change_data_feed=True)
    run(config)
    source.write_text("754;3081/360;1;\n755;3082/360;1;\n")
    run(config)
    assert [(r["id_modelo_fenix"], r["_commit_version"]) for r in changes(config, 1, 1)] == [
        (755, 1)
    ]
    table = DeltaTable(str(config.table_path))
    assert table.metadata().configuration["delta.enableExpiredLogCleanup"] == "false"
    run(config)
    assert table.version() == 1


def test_counter_reversion_keeps_each_confirmed_revision(config):
    config = replace(
        config, family="CONT_ZC", timestamp_policy="local_naive_test_v1", change_data_feed=True
    )
    source = config.sources[0][1] / "CONT_ZC.csv"
    source.write_text(line())
    run(config)
    with closing(ZcControl(config)) as control:
        store = ZcStore(config, control.control_id)
        for version, value in enumerate((1700, 1614)):
            row = next(iter(store.read()[1].values()))
            store.insert([{**row, "CONT_ZC_1": value}], f"revision-{version}", version)
    events = changes(config, 0, 2)
    assert {r["_commit_version"] for r in events} == {0, 1, 2}
    # CDF reports each previous/new value, including a return to A.
    assert any(r["_change_type"] == "update_preimage" for r in events)
    assert any(
        r["_change_type"] == "update_postimage" and r["_commit_version"] == 2 for r in events
    )


def test_existing_legacy_table_requires_explicit_migration(config, source):
    run(config)
    with pytest.raises(IntegrityError, match="migrate explicitly"):
        DeltaStore(replace(config, change_data_feed=True), "unused").table()
