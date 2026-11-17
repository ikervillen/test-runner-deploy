import hashlib
import json
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from test_pipeline_runtime import pipeline_config as pipeline_config

from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.pipeline_bronze import BronzeFeed
from ingesta_delta_lake.pipeline_intake import Intake
from ingesta_delta_lake.pipeline_runtime import PipelineWorker
from ingesta_delta_lake.runtime import atomic_json
from ingesta_delta_lake.sources import Snapshot, capture, capture_identity


def test_interrupted_capture_recovers_before_mutable_fenix_alias_is_read_again(
    config, pipeline_config
):
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    path = config.sources[0][1] / "SIP_MODELO.csv"
    path.write_text("2;3082/360;1;\n")
    original = capture(worker.source, "AV21", path, None)
    path.write_text("3;3083/360;1;\n")
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        result = worker.cycle(intake, phases=(("SIP_MODELO",),))
        assert result["SIP_MODELO"]["status"] == "APPLIED"
        assert intake.db.execute("SELECT count(*) FROM inputs").fetchone()[0] == 2
        row = intake.db.execute(
            "SELECT descriptor FROM inputs WHERE sha256=?", (original.sha256,)
        ).fetchone()
        assert json.loads(row[0]) == original.descriptor()
        assert not list((worker.storage.checkpoint / "capture-intents").glob("*.json"))
    entries = worker.storage.exchange / "bronze" / "entries"
    payloads = [json.loads(p.read_text()) for p in sorted(entries.glob("*.json"))]
    assert len(payloads) == 2
    assert payloads[0]["snapshot"] == original.descriptor()
    assert Path(payloads[0]["snapshot"]["bronze_path"]).read_text() == "2;3082/360;1;\n"
    feed = BronzeFeed(worker.storage)
    first = list(feed.captures(after=0, limit=1))
    assert (
        len(first) == 1
        and feed.read(first[0], max_file_bytes=1024) == Path(original.bronze_path).read_bytes()
    )
    assert len(list(feed.captures(after=first[0].sequence, limit=1))) == 1
    value = payloads[1]
    value["family"] = "foreign"
    (entries / "2.json").write_text(json.dumps(value))
    with pytest.raises(IntegrityError, match="envelope"):
        list(feed.captures(after=1, limit=1))


@pytest.mark.parametrize("complete", [True, False])
def test_capture_recovers_fsynced_staging_bytes_and_retains_incomplete_evidence(
    config, pipeline_config, complete
):
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    body = b"2;3082/360;1;\n"
    sha = hashlib.sha256(body).hexdigest()
    target = worker.storage.bronze / "AIALA" / "AV21" / "SIP_MODELO" / "2026-11-17" / (sha + ".csv")
    target.parent.mkdir(parents=True)
    staged = target.parent / ".capture-interrupted"
    staged.write_bytes(body if complete else body[:3])
    snapshot = Snapshot(
        "AV21",
        str(config.sources[0][1] / "SIP_MODELO.csv"),
        sha,
        str(target),
        len(body),
        1,
        "2026-11-17T16:00:00+00:00",
        None,
    )
    intent = (
        worker.storage.checkpoint
        / "capture-intents"
        / (capture_identity("SIP_MODELO", snapshot) + ".json")
    )
    intent.parent.mkdir()
    atomic_json(
        intent,
        {"family": "SIP_MODELO", "snapshot": snapshot.descriptor(), "staging_path": str(staged)},
    )
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        assert intake.recover_captures(worker.source, limit=1) == int(complete)
        if complete:
            assert target.read_bytes() == body and not staged.exists() and not intent.exists()
        else:
            assert not target.exists() and staged.exists() and intent.exists()
            assert intake.db.execute("SELECT count(*) FROM failures").fetchone()[0] == 1


def test_bronze_publication_replays_lost_checkpoint_without_duplicate_envelopes(
    config, pipeline_config, monkeypatch
):
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    path = config.sources[0][1] / "SIP_MODELO.csv"
    path.write_text("2;3082/360;1;\n")
    original = capture(worker.source, "AV21", path, None)
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        intake.remember("SIP_MODELO", original)
        with monkeypatch.context() as patch:
            patch.setattr(
                intake, "set", lambda *_: (_ for _ in ()).throw(OSError("lost checkpoint"))
            )
            with pytest.raises(OSError):
                intake.publish_captures(worker.storage.exchange, limit=1)
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        assert intake.publish_captures(worker.storage.exchange, limit=1) == 1
        assert intake.publish_captures(worker.storage.exchange, limit=1) == 0
    root = worker.storage.exchange / "bronze"
    assert len(list((root / "entries").glob("*.json"))) == 1
    assert json.loads((root / "current.json").read_text())["sequence"] == 1


def test_invalid_capture_intention_cannot_escape_its_origin(config, pipeline_config):
    worker = PipelineWorker(pipeline_config, "live", config)
    worker.prepare()
    path = config.sources[0][1] / "SIP_MODELO.csv"
    path.write_text("2;3082/360;1;\n")
    original = capture(worker.source, "AV21", path, None)
    forged = replace(original, bronze_path=str(worker.storage.bronze.parent / "foreign.csv"))
    intent = (
        worker.storage.checkpoint
        / "capture-intents"
        / (capture_identity("SIP_MODELO", forged) + ".json")
    )
    atomic_json(
        intent, {"family": "SIP_MODELO", "snapshot": forged.descriptor(), "staging_path": None}
    )
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        assert intake.recover_captures(worker.source, limit=1) == 0
        assert not Path(forged.bronze_path).exists()
