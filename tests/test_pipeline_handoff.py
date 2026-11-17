from contextlib import closing
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from deltalake import DeltaTable
from test_lista_defectos import line
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake.pipeline_handoff import BronzeHandoff
from ingesta_delta_lake.pipeline_intake import Intake
from ingesta_delta_lake.pipeline_runtime import PipelineWorker
from ingesta_delta_lake.sources import Snapshot


def capture_only(worker):
    with closing(
        Intake(worker.source.intake_path, lane=worker.lane, identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=())


def test_history_processes_superseded_alias_from_bronze_after_fenix_overwrites_it(
    config, pipeline_config
):
    config = replace(config, family="LISTA_DEFECTOS", timestamp_policy="local_naive_test_v1")
    live, history = (PipelineWorker(pipeline_config, lane, config) for lane in ("live", "history"))
    live.prepare()
    history.prepare()
    source = config.sources[0][1] / "LISTA_DEFECTOS.csv"
    for inspected in (96, 97):
        source.write_text(line(inspected=inspected))
        capture_only(live)
    source.unlink()
    with closing(
        Intake(history.source.intake_path, lane="history", identity=history.identity)
    ) as intake:
        result = history.cycle(intake, phases=((config.family,),))
        assert result[config.family]["status"] == "APPLIED"
        assert intake.db.execute("SELECT count(*) FROM inputs").fetchone()[0] == 2
        assert not intake.pending(config.family, 10)
        assert (
            intake.db.execute("SELECT count(*) FROM live_imports WHERE eligible=0").fetchone()[0]
            == 1
        )
    accepted = DeltaTable(str(history.source.table_path)).to_pyarrow_table().to_pylist()
    assert len(accepted) == 1 and accepted[0]["INSPECC"] == 96
    assert not live.source.table_path.exists()


def test_current_timed_capture_is_deferred_until_producer_window_advances(config, pipeline_config):
    config = replace(config, family="LISTA_DEFECTOS", timestamp_policy="local_naive_test_v1")
    live, history = (PipelineWorker(pipeline_config, lane, config) for lane in ("live", "history"))
    live.prepare()
    history.prepare()
    source = config.sources[0][1] / "17112026_120000_LISTA_DEFECTOS.csv"
    source.write_text(line())
    capture_only(live)
    with closing(
        Intake(history.source.intake_path, lane="history", identity=history.identity)
    ) as intake:
        handoff = BronzeHandoff(intake, live.storage)
        policy = pipeline_config.policy
        current = policy.source_window(datetime(2026, 11, 17, 12))
        assert handoff.turn(history.source, policy, window=current)["deferred"] == 1
        assert not intake.pending(config.family, 10)
        next_window = policy.source_window(datetime(2026, 11, 17, 12, 10))
        assert handoff.turn(history.source, policy, window=next_window)["deferred"] == 0
        selected = intake.pending(config.family, 10)
        assert len(selected) == 1
        import json

        snapshot = Snapshot(
            **json.loads(
                intake.db.execute(
                    "SELECT descriptor FROM inputs WHERE input_id=?", selected
                ).fetchone()[0]
            )
        )
        assert Path(snapshot.bronze_path).is_relative_to(history.storage.bronze)
        assert Path(snapshot.bronze_path).read_bytes() == source.read_bytes()


def test_reference_catalog_bootstraps_history_without_waiting_eight_hours(config, pipeline_config):
    live, history = (PipelineWorker(pipeline_config, lane, config) for lane in ("live", "history"))
    live.prepare()
    history.prepare()
    source = config.sources[0][1] / "SIP_MODELO.csv"
    source.write_text("2;3082/360;1;\n")
    capture_only(live)
    source.unlink()
    with closing(
        Intake(history.source.intake_path, lane="history", identity=history.identity)
    ) as intake:
        result = history.cycle(intake, phases=(("SIP_MODELO",),))
        assert result["SIP_MODELO"]["status"] == "APPLIED"
    assert DeltaTable(str(history.source.table_path)).count() == 1
