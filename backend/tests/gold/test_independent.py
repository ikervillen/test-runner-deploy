"""Independent Gold reads confirmed publications without opening a foreign control."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pyarrow as pa
import pytest

from app.gold.common import IntegrityError
from app.gold.evidence import Evidence, registry, save


@pytest.fixture
def native(monkeypatch):
    candidate = Path(__file__).resolve().parents[4] / "Ingesta_Delta_Lake-isolation" / "src"
    if candidate.exists():
        monkeypatch.syspath_prepend(str(candidate))
    return pytest.importorskip("ingesta_delta_lake")


@pytest.fixture
def independent(tmp_path, native):
    from ingesta_delta_lake.config import Config
    from ingesta_delta_lake.pipeline_intake import Intake
    from ingesta_delta_lake.pipeline_policy import STORES, IndependentPipelines
    from ingesta_delta_lake.pipeline_runtime import PipelineWorker

    source = tmp_path / "fenix" / "AV21"
    source.mkdir(parents=True)
    cfg = Config(
        plant="AIALA",
        input_root=source.parent.resolve(),
        sources=(("AV21", source.resolve()),),
        bronze_root=(tmp_path / "unused-bronze").resolve(),
        silver_root=(tmp_path / "unused-silver").resolve(),
        control_root=(tmp_path / "unused-control").resolve(),
        stable_wait_seconds=0.001,
    )
    template = json.loads(
        (Path(native.__file__).parents[2] / "deployment/pipelines.example.json").read_text()
    )
    for lane in ("live", "history"):
        template[lane] = {name: str((tmp_path / lane / name).resolve()) for name in STORES}
    path = tmp_path / "pipelines.json"
    path.write_text(json.dumps(template))
    plan = IndependentPipelines.load(path)
    worker = PipelineWorker(plan, "live", cfg)
    worker.prepare()
    (source / "SIP_MODELO.csv").write_text("2;MODEL;1;\n")
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=(("SIP_MODELO",),))
    return plan, cfg


def test_independent_bridge_reads_no_active_sqlite(independent, monkeypatch):
    from app.gold.independent_inputs import export

    plan, cfg = independent
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("Gold opened SQLite"))
    result = export(plan, "live", cfg)
    assert result.rows("SIP_MODELO", max_rows=10) == [
        {"id_modelo_fenix": 2, "codigo_modelo_botella": "MODEL"}
    ]
    assert result.rows("LISTA_RECH", max_rows=10) == []
    assert result.proofs(max_rows=10)["SIP_MODELO"]
    assert result.document["lane"] == "live"
    assert not plan.history.control.exists()
    assert export(plan, "live", cfg).publication_id == result.publication_id


def test_evidence_verifies_bytes_and_rejects_unbounded_read(tmp_path, native):
    evidence = save(tmp_path, {}, {"rows": pa.table({"a": [1, 2]})})
    with pytest.raises(IntegrityError, match="budget"):
        evidence.table("rows", max_rows=1)
    path = tmp_path / evidence.document["tables"][0]["uri"]
    path.write_bytes(b"corrupt")
    with pytest.raises(IntegrityError, match="bytes"):
        Evidence.open(tmp_path).table("rows", max_rows=2)


def test_registry_never_resolves_same_revision_by_arrival(native):
    from app.gold.common import digest

    values = [{"a": {"revision": 1}}, {"a": {"revision": 1, "actor": "other"}}]
    with pytest.raises(IntegrityError, match="revisions"):
        registry([{"registry": r, "registry_hash": digest(r)} for r in values])


def test_independent_gold_publishes_self_contained_evidence(independent, tmp_path):
    from datetime import datetime

    from app.gold.common import read_json
    from app.gold.independent_inputs import export, published_evidence
    from app.gold.pipeline import run
    from app.gold.publications import Snapshot

    plan, cfg = independent
    evidence = export(plan, "live", cfg)
    root = plan.live.gold / "published"
    from .test_pipeline import bundle

    kwargs = dict(
        confirmed_input=evidence,
        bundle=bundle(),
        config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
        cutoff=datetime(2026, 11, 17, 16, 40),
        environment=cfg.environment,
        plant=cfg.plant,
    )
    result = run(evidence.root, root, **kwargs)
    assert result["status"] == "PUBLISHED"
    assert published_evidence(root).publication_id == evidence.publication_id
    assert Snapshot.open(root, require_gold_contract=True).rows("productions") == []
    assert run(evidence.root, root, **kwargs)["status"] == "UNCHANGED"
