from dataclasses import replace

import pytest
from test_mia_batches import csv_row

from ingesta_delta_lake.common import ConfigurationError
from ingesta_delta_lake.service import run


@pytest.mark.parametrize("family", ["LISTA_RECH", "LISTA_DEFECTOS"])
def test_origin_cursor_and_budget_survive_new_attempts(config, family):
    other = config.input_root / "AV22"
    other.mkdir()
    cfg = replace(
        config,
        family=family,
        timestamp_policy="local_naive_test_v1",
        sources=(*config.sources, ("AV22", other)),
        mia_batches_per_turn=1,
    )
    for _origin, folder in cfg.sources:
        for i in range(10):
            (folder / f"14102026_{90000 + i}_{family}.csv").write_text(
                csv_row(family, i), encoding="utf-8"
            )
    first = run(cfg)
    assert first["status"] == "BACKLOG_PENDING"
    assert first["processed_versions"] == 8 and first["remaining_versions"] == 12
    assert first["pending_by_origin"] == {"AV21": 2, "AV22": 10}
    second = run(cfg)
    assert second["pending_by_origin"] == {"AV21": 2, "AV22": 2}
    assert second["published_rows"] == 16
    run(cfg)
    final = run(cfg)
    assert final["remaining_versions"] == 0 and final["published_rows"] == 20


def test_dictionary_alias_replay_has_no_stability_wait_but_detects_same_stat_overwrite(
    config, source, monkeypatch
):
    run(config)
    from ingesta_delta_lake import sources

    monkeypatch.setattr(
        sources.time, "sleep", lambda *_: pytest.fail("Stable hash should skip wait")
    )
    assert run(config)["status"] == "NO_CHANGE"


def test_unhandled_snapshot_survives_rotation_before_processing(config, source, monkeypatch):
    from ingesta_delta_lake import sources

    original = sources.capture
    later = source.with_name("14102026_100000_SIP_MODELO.csv")
    later.write_text("2;1111/222;1;\n", encoding="utf-8")

    def capture(cfg, origin, path, stamp, **kwargs):
        if path == later:
            raise OSError("Interrupted after first immutable capture")
        return original(cfg, origin, path, stamp, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sources, "capture", capture)
        # Discover sorts the dated name first, so explicitly order the test inputs.
        patch.setattr(
            sources, "discover", lambda _: [("AV21", source, None), ("AV21", later, None)]
        )
        with pytest.raises(OSError):
            run(config)
    source.unlink()
    assert run(config)["inserted_this_attempt"] == 2


@pytest.mark.parametrize("value", [0, True, -1, 101, 1.5])
def test_invalid_turn_budget(config, value):
    with pytest.raises(ConfigurationError):
        replace(config, mia_batches_per_turn=value).validate()


def test_fixed_name_same_size_and_mtime_is_still_hashed(config, source, monkeypatch):
    import os

    from ingesta_delta_lake import sources

    run(config)
    stat = source.stat()
    source.write_text("755;3082/360;1;\n", encoding="utf-8")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert source.stat().st_size == stat.st_size
    sleeps = []
    monkeypatch.setattr(sources.time, "sleep", sleeps.append)
    assert run(config)["inserted_this_attempt"] == 1
    assert sleeps


def test_generic_capture_gap_recovers_orphan_after_original_disappears(config, source, monkeypatch):
    from ingesta_delta_lake.control import Control

    with monkeypatch.context() as patch:
        patch.setattr(Control, "receipts", lambda *_: (_ for _ in ()).throw(OSError("loss")))
        with pytest.raises(OSError):
            run(config)
    source.unlink()
    assert run(config)["inserted_this_attempt"] == 1


def test_progress_reports_wall_time_rate_and_approximate_eta(config, monkeypatch):
    from contextlib import closing

    from ingesta_delta_lake import defects_control

    cfg = replace(config, family="LISTA_RECH", timestamp_policy="local_naive_test_v1")
    cfg.control_root.mkdir()
    with closing(defects_control.DefectsControl(cfg)) as control:
        monkeypatch.setattr(defects_control.time, "time", lambda: 100)
        assert control.progress(4, 12)["estimated_backlog_seconds"] is None
        monkeypatch.setattr(defects_control.time, "time", lambda: 110)
        result = control.progress(12, 4)
        assert result["versions_per_wall_second"] == 0.8
        assert result["estimated_backlog_seconds"] == 5
