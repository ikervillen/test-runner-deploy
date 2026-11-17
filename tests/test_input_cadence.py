import os
import sqlite3
from dataclasses import replace

import pytest
from deltalake import DeltaTable
from test_mia_batches import csv_row

from ingesta_delta_lake import defects_service, sources
from ingesta_delta_lake.common import ConfigurationError
from ingesta_delta_lake.contracts import CONTRACTS
from ingesta_delta_lake.service import run


def test_fixed_overwrite_and_new_alias_are_captured_when_scan_becomes_due(
    config, source, monkeypatch
):
    cfg = replace(config, input_scan_interval_seconds=600)
    clock = [100]
    monkeypatch.setattr(sources.time, "time", lambda: clock[0])
    assert run(cfg)["input_scan_performed"] is True
    stat = source.stat()
    source.write_text("755;3082/360;1;\n", encoding="utf-8")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    alias = source.with_name("20102026_100000_SIP_MODELO.csv")
    alias.write_text("756;3083/360;1;\n", encoding="utf-8")
    clock[0] = 200
    assert run(cfg)["input_scan_performed"] is False
    assert DeltaTable(str(cfg.table_path)).version() == 0
    clock[0] = 700
    result = run(cfg)
    assert result["input_scan_performed"] is True and result["inserted_this_attempt"] == 2
    assert DeltaTable(str(cfg.table_path)).to_pyarrow_table().num_rows == 3


@pytest.mark.parametrize("family", ["LISTA_RECH", "LISTA_DEFECTOS"])
def test_backlog_continues_between_scans_without_rediscovery(config, monkeypatch, family):
    cfg = replace(
        config,
        family=family,
        timestamp_policy="local_naive_test_v1",
        input_scan_interval_seconds=600,
        mia_batches_per_turn=1,
    )
    for i in range(20):
        hour = 10 + i // 6
        minute = (i % 6) * 10
        stamp = hour * 10000 + minute * 100
        path = cfg.input_root / "AV21" / f"14102026_{stamp:06d}_{family}.csv"
        path.write_text(csv_row(family, i), encoding="utf-8")
    first = run(cfg)
    assert first["processed_versions"] == 20 and first["remaining_versions"] == 0
    assert first["input_scan_performed"] is True and first["published_rows"] == 20
    with monkeypatch.context() as patch:
        patch.setattr(defects_service, "discover", lambda *_: pytest.fail("Repeated scan"))
        second = run(cfg)
        assert second["processed_versions"] == 0 and second["input_scan_performed"] is False
        assert second["published_rows"] == 20
        replay = run(cfg)
        assert replay["inserted_this_attempt"] == 0
    assert DeltaTable(str(cfg.table_path)).version() == 19


def test_failed_discovery_retries_immediately_and_retains_captured_revision(
    config, source, monkeypatch
):
    cfg = replace(config, input_scan_interval_seconds=600)
    with monkeypatch.context() as patch:
        original = sources.orphan_captures

        def fail(*_):
            raise OSError("Interrupted discovery")

        patch.setattr(sources, "orphan_captures", fail)
        with pytest.raises(OSError, match="discovery"):
            run(cfg)
        patch.setattr(sources, "orphan_captures", original)
    source.unlink()
    result = run(cfg)
    assert result["input_scan_performed"] is True and result["inserted_this_attempt"] == 1


def test_processing_failure_retries_pending_capture_without_waiting_for_next_scan(
    config, source, monkeypatch
):
    cfg = replace(config, input_scan_interval_seconds=600)
    original = CONTRACTS["SIP_MODELO"]

    def fail(*_):
        raise OSError("Interrupted transformation")

    with monkeypatch.context() as patch:
        patch.setitem(CONTRACTS, "SIP_MODELO", replace(original, parse=fail))
        with pytest.raises(OSError, match="transformation"):
            run(cfg)
    source.unlink()
    result = run(cfg)
    assert result["input_scan_performed"] is False and result["inserted_this_attempt"] == 1


def test_new_activation_refreshes_immediately_and_backward_clock_never_blocks_scan(
    config, source, monkeypatch
):
    cfg = replace(config, input_scan_interval_seconds=600)
    clock = [1000]
    monkeypatch.setattr(sources.time, "time", lambda: clock[0])
    monkeypatch.setenv("INGESTA_ACTIVATION", "a" * 32)
    run(cfg)
    assert run(cfg)["input_scan_performed"] is False
    monkeypatch.setenv("INGESTA_ACTIVATION", "b" * 32)
    assert run(cfg)["input_scan_performed"] is True
    clock[0] = 500
    assert run(cfg)["input_scan_performed"] is True


def test_source_configuration_change_forces_scan_and_manual_default_always_scans(config, source):
    cfg = replace(config, input_scan_interval_seconds=600)
    run(cfg)
    other = config.input_root / "AV22"
    other.mkdir()
    cfg = replace(cfg, sources=(*cfg.sources, ("AV22", other)))
    (other / "SIP_MODELO.csv").write_text("755;3082/360;1;\n", encoding="utf-8")
    assert run(cfg)["inserted_this_attempt"] == 1
    assert run(config)["input_scan_performed"] is True


@pytest.mark.parametrize("interval", [-1, 86401, True, 0.5])
def test_invalid_discovery_interval(config, interval):
    with pytest.raises(ConfigurationError, match="input_scan"):
        replace(config, input_scan_interval_seconds=interval).validate()


def test_scan_window_is_persisted_only_after_all_captures(config, source):
    cfg = replace(config, input_scan_interval_seconds=600)
    run(cfg)
    with sqlite3.connect(cfg.control_path) as db:
        value = db.execute("SELECT value FROM settings WHERE key='last_input_scan'").fetchone()[0]
        assert "started" in value and "signature" in value
