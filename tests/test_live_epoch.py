from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from deltalake import DeltaTable
from test_mia_batches import csv_row

from deployment.independent import configure_fresh_live
from ingesta_delta_lake.pipeline_intake import Candidate
from ingesta_delta_lake.pipeline_policy import PipelinePolicy
from ingesta_delta_lake.pipeline_runtime import prospective
from ingesta_delta_lake.service import run


def test_cumulative_rejections_only_publish_observations_from_t0(config, monkeypatch):
    monkeypatch.setenv("FENIX_LIVE_T0", "2026-10-14T00:10:00")
    cfg = replace(
        config, family="LISTA_RECH", control_format=3, timestamp_policy="local_naive_test_v1"
    )
    source = cfg.sources[0][1] / "LISTA_RECH.csv"
    source.write_text(
        csv_row(cfg.family, 0) + csv_row(cfg.family, 10) + csv_row(cfg.family, 20),
        encoding="utf-8",
    )
    run(cfg)
    facts = DeltaTable(str(cfg.table_path)).to_pyarrow_table().to_pylist()
    assert {row["FECHA"].minute for row in facts} == {10, 20}


def test_prospective_discovery_only_keeps_latest_context_and_eligible_deliveries():
    old = Candidate("AV21", Path("old.csv"), "LISTA_RECH", "2026-11-24T09:00:00")
    new = Candidate("AV21", Path("new.csv"), "LISTA_RECH", "2026-11-24T09:10:00")
    mp9 = Candidate("AV21", Path("catalog.csv"), "MP9_CAMBIO_MODELO", "2026-11-24T08:50:00")
    policy = SimpleNamespace(window_seconds=600)
    policy.source_window = lambda clock: PipelinePolicy.source_window(policy, clock)
    bootstrap, lanes, window = prospective(
        {"live": [new], "history": [old, mp9]}, policy, datetime(2026, 11, 24, 9, 10)
    )
    assert lanes == {"live": [new], "history": []}
    assert bootstrap == [replace(mp9, producer_time=None)]
    assert window.start == new.clock


def test_fresh_compose_uses_new_live_volumes_and_no_history_mounts():
    mounts = [
        {"source": "live-gold", "target": "/pipelines/live/gold"},
        {"source": "history-gold", "target": "/pipelines/history/gold"},
    ]
    compose = {
        "name": "example",
        "services": {
            **{
                name: {"volumes": list(mounts)} for name in ("ingesta-live", "gold-live", "backend")
            },
            "initialize": {"volumes": list(mounts), "command": ["--uid", "1", "--gid", "2"]},
        },
        "volumes": {"live-gold": {"name": "old-live"}, "history-gold": {"name": "old-history"}},
    }
    result = configure_fresh_live(compose, "a" * 40, "2026-11-24T09:10:00")
    assert result["volumes"]["live-gold"]["name"] == "example-aaaaaaaaaaaa-live-gold"
    assert result["volumes"]["history-gold"]["name"] == "old-history"
    assert result["services"]["initialize"]["command"][-1] == "/pipelines/live/gold"
    for name in ("ingesta-live", "gold-live", "backend"):
        svc = result["services"][name]
        assert svc["environment"]["FENIX_LIVE_T0"] == "2026-11-24T09:10:00"
        assert svc["volumes"] == mounts[:1]
