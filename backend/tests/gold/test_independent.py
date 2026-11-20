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


def test_auxiliary_cursor_without_scoring_changes_does_not_export(independent, monkeypatch):
    from app.gold import independent_inputs

    plan, cfg = independent
    original = independent_inputs.export(plan, "live", cfg)
    tables, cursor, version = independent_inputs.auxiliary_turn(plan, "live", encoding=cfg.encoding)
    monkeypatch.setattr(
        independent_inputs, "auxiliary_turn", lambda *a, **k: (tables, cursor + 1, version)
    )
    assert independent_inputs.export(plan, "live", cfg).publication_id == original.publication_id

    monkeypatch.setattr(
        independent_inputs,
        "auxiliary_turn",
        lambda *a, **k: (tables, cursor + 2, (version[0], cursor + 2)),
    )
    updated = independent_inputs.export(plan, "live", cfg)
    assert updated.publication_id != original.publication_id
    assert updated.document["silver_read"]["rows"] == 0
    assert "SIP_MODELO" in updated.document["silver_read"]["reused_catalogues"]


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
        lane="live",
        current_slot_seconds=plan.policy.window_seconds,
        environment=cfg.environment,
        plant=cfg.plant,
    )
    result = run(evidence.root, root, **kwargs)
    assert result["status"] == "PUBLISHED"
    assert published_evidence(root).publication_id == evidence.publication_id
    snapshot = Snapshot.open(root, require_gold_contract=True)
    assert snapshot.rows("productions") == []
    assert snapshot.rows("publication_scope_status") == []
    assert run(evidence.root, root, **kwargs)["status"] == "UNCHANGED"


def industrial_evidence(root, lane, *, recent, native, horizon=None):
    from copy import deepcopy
    from datetime import timedelta
    from decimal import Decimal

    from ingesta_delta_lake.contracts import CONTRACTS
    from ingesta_delta_lake.pipeline_rows import business_key, portable, review_hash

    from app.gold.evidence import attribution
    from app.gold.producer import FAMILIES, auxiliary

    from .test_pipeline import CUTOFF, START, inputs

    horizon = horizon or CUTOFF
    data = deepcopy(inputs())
    for row in data["CATALOGO_PRODUCCIONES"]:
        row["id_modelo_fenix"] = 10
    data["SIP_DEFECTO_AR"][0]["descripcion"] = "INSPECTION ZF"
    for family in ("LISTA_RECH", "LISTA_DEFECTOS"):
        for row in data[family]:
            if not recent:
                row["FECHA"] -= timedelta(minutes=10)
                row["INSPECC"] = 500
    tables, lineages = {}, {}
    for family in FAMILIES:
        schema = CONTRACTS[family].schema
        rows = []
        for original in data.get(family, []):
            row = dict(original)
            for field in schema:
                if field.name not in row:
                    row[field.name] = (
                        None
                        if field.nullable
                        else 0
                        if pa.types.is_integer(field.type)
                        else Decimal("0")
                        if pa.types.is_decimal(field.type)
                        else START
                        if pa.types.is_timestamp(field.type)
                        else ""
                    )
            rows.append(row)
        table = pa.Table.from_pylist(rows, schema=schema)
        tables[family] = table
        lineages[family] = {
            business_key(family, r): [
                {
                    "source_table": "owner-" + family,
                    "source_version": 2 if recent else 1,
                    "row_hash": review_hash(family, r),
                }
            ]
            for r in table.to_pylist()
        }
    registry, composition = {}, {}
    for n, row in enumerate(data["CATALOGO_PRODUCCIONES"]):
        identity = row["id_produccion"]
        spans = [
            portable(r) for r in data["MP9_CAMBIO_MODELO"] if r["ID"] in {n * 2 + 1, n * 2 + 2}
        ]
        composition[identity] = {"coverage": "VALID", "root": spans[0], "zf": [spans[1]]}
        for zone, kind in (("ZF", "ZF_ASSOCIATION"), ("ZC", "ZC_PRESENCE")):
            key = f"{zone}-{n}"
            registry[key] = {
                "id": key,
                "kind": kind,
                "origin": "AV23",
                "production": identity,
                "start": spans[0]["FECHA_ON"],
                "end": spans[0]["FECHA_OFF"],
                "revision": 1,
                "actor": "approved fixture",
                ("branch" if zone == "ZF" else "machine"): 1 if zone == "ZF" else 23,
            }
    document = {
        "lane": lane,
        "environment": "test",
        "plant": "AV",
        "row_budget": 100000,
        "confirmed_at": CUTOFF.isoformat(),
        "lineages": lineages,
        "rejections": {},
        "registry": registry,
        "lines": {"AV23": "AV23"},
        "association_cases": [],
        "bundle": {
            "plant": "AV",
            "topology_enabled": False,
            "line_codes": {},
            "catalog_composition": composition,
            "cases": {f: [] for f in FAMILIES},
        },
        "coverage": [
            {
                "source": f,
                "origin": "AV23",
                "estado": "READY",
                "covered_from": START.isoformat(),
                "covered_until": horizon.isoformat(),
                "live_until": horizon.isoformat(),
            }
            for f in FAMILIES
        ],
    }
    tables.update(attribution(document, tables))
    tables.update(
        auxiliary(
            root,
            (),
            captures=(),
            initial={
                "production_parameters": data["production_parameters"],
                "gobs_dictionary": data["gobs_dictionary"],
                "machine_gobs": data["machine_gobs"],
            },
        )
    )
    return save(root, document, tables)


def test_cross_boundary_facts_recalculate_the_same_as_one_complete_input(tmp_path, native):
    from app.gold.common import read_json
    from app.gold.evidence import combine
    from app.gold.pipeline import build

    from .test_pipeline import CUTOFF, bundle

    live = industrial_evidence(tmp_path / "live", "live", recent=True, native=native)
    history = industrial_evidence(tmp_path / "history", "history", recent=False, native=native)
    source = combine(live, history, max_rows=100000)
    assert len(source.rows("LISTA_RECH")) == 8
    assert len(source.rows("SIP_MODELO")) == 1
    scratch = tmp_path / "gold" / "checkpoint" / "facts-work"
    tables, _ = build(
        source,
        bundle=bundle(),
        config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
        cutoff=CUTOFF,
        calculation_time=CUTOFF,
        spill_root=scratch,
        spill_db_bytes=32 * 1024**2,
    )
    try:
        assert tables.store.path.parent.parent == scratch
        exposures = [r for r in tables["mia_window_exposures"] if r["tipo_ventana"] == "PRODUCTION"]
        assert len(exposures) == 4
        assert {r["inspeccionadas"] for r in exposures} == {1500}
        assert len(tables["mia_inspections"]) == 8
        # 40 + 40 rejections over 500 + 1000 inspected: amounts are combined before division.
        amounts = [
            r
            for r in tables["mia_window_bases"]
            if r["tipo_ventana"] == "PRODUCTION" and r["id_produccion"] == "production-3"
        ]
        assert sum(r["rechazos"] for r in amounts) == 80
    finally:
        tables.close()


def test_gold_context_uses_the_confirmed_historical_mp9_closure(tmp_path, native):
    from ingesta_delta_lake.mp9 import key
    from ingesta_delta_lake.pipeline_rows import portable

    from app.gold.context import catalog_mp9

    evidence = industrial_evidence(tmp_path, "live", recent=True, native=native)
    rows = evidence.rows("MP9_CAMBIO_MODELO")
    old = next(r for r in rows if r["STATUS"] == 2 and r["FECHA_OFF"] is not None)
    opened = [{**r, "FECHA_OFF": None} if r == old else r for r in rows]
    accepted = {key(row): portable(row) for row in rows}
    bundle = {
        **evidence.document["bundle"],
        "catalog_composition": {
            identity: {
                **entry,
                "root": accepted[key(entry["root"])],
                "zf": [accepted[key(row)] for row in entry["zf"]],
            }
            for identity, entry in evidence.document["bundle"]["catalog_composition"].items()
        },
        "historical_mp9_closures": [{"publication_id": "history"}],
    }
    assert catalog_mp9(opened, bundle) == portable(rows)


def test_combined_gold_never_counts_a_capture_twice(tmp_path, native):
    from app.gold.evidence import combine

    first = industrial_evidence(tmp_path / "live", "live", recent=True, native=native)
    again = industrial_evidence(tmp_path / "history", "history", recent=True, native=native)
    combined = combine(first, again, max_rows=100000)
    assert len(combined.rows("LISTA_RECH")) == 4
    with pytest.raises(IntegrityError, match="budget"):
        combine(first, again, max_rows=1)


def test_combined_query_pins_both_publications_and_recalculates(tmp_path, native):
    from app.gold.combined_query import CombinedQuery
    from app.gold.common import read_json
    from app.gold.pipeline import run

    from .test_pipeline import CUTOFF, bundle

    roots = {}
    for lane, recent in (("live", True), ("history", False)):
        source = industrial_evidence(tmp_path / lane / "inputs", lane, recent=recent, native=native)
        roots[lane] = tmp_path / lane / "published"
        result = run(
            source.root,
            roots[lane],
            confirmed_input=source,
            bundle=bundle(),
            config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
            cutoff=CUTOFF,
            environment="test",
            plant="AV",
        )
        assert result["status"] == "PUBLISHED"
    query = CombinedQuery(roots["live"], roots["history"], max_rows=100000, total_rows=100000)
    # A later broken pointer cannot mix versions into the already pinned query.
    (roots["history"] / "current.json").write_text("corrupt")
    exposures = query.rows("mia_window_exposures")
    assert {r["inspeccionadas"] for r in exposures if r["tipo_ventana"] == "PRODUCTION"} == {1500}
    assert len(query.rows("mia_inspections")) == 8
    assert query.fork(None) is query


def test_live_gold_keeps_facts_until_historical_gold_confirms_them(independent, native):
    from app.gold.common import read_json
    from app.gold.independent_inputs import OPERATIONS, retain_live
    from app.gold.pipeline import run

    from .test_pipeline import CUTOFF, bundle

    plan, _cfg = independent
    previous = industrial_evidence(plan.live.gold / "inputs", "live", recent=True, native=native)
    empty = {f: previous.table(f, max_rows=100000).slice(0, 0) for f in OPERATIONS}
    document = {"lineages": {}, "rejections": {f: [] for f in OPERATIONS}}
    retain_live(plan, empty, document)
    assert empty["LISTA_RECH"].num_rows == 4
    assert not plan.history.gold.exists()
    copy = industrial_evidence(plan.history.gold / "inputs", "history", recent=True, native=native)
    run(
        copy.root,
        plan.history.gold / "published",
        confirmed_input=copy,
        bundle=bundle(),
        config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
        cutoff=CUTOFF,
        environment="test",
        plant="AV",
    )
    empty = {f: previous.table(f, max_rows=100000).slice(0, 0) for f in OPERATIONS}
    document = {"lineages": {}, "rejections": {f: [] for f in OPERATIONS}}
    retain_live(plan, empty, document)
    assert all(t.num_rows == 0 for t in empty.values())


def revised_evidence(source, root, family, mutate):
    from ingesta_delta_lake.pipeline_rows import business_key, review_hash

    tables = {
        r["name"]: source.table(r["name"], max_rows=100000)
        for r in source.document["tables"]
        if r["name"] != "__lineage"
    }
    rows = tables[family].to_pylist()
    mutate(rows)
    tables[family] = pa.Table.from_pylist(rows, schema=tables[family].schema)
    proofs = source.proofs(max_rows=100000)
    proofs[family] = {
        business_key(family, row): [
            {
                "source_table": "owner-" + family,
                "source_version": 3,
                "row_hash": review_hash(family, row),
            }
        ]
        for row in rows
    }
    return save(root, {**source.document, "lineages": proofs}, tables)


def test_cross_store_contract_conflicts_are_not_hidden(tmp_path, native):
    from app.gold.evidence import combine

    history = industrial_evidence(tmp_path / "history", "history", recent=True, native=native)
    live = revised_evidence(
        history, tmp_path / "live", "SIP_MODELO", lambda rows: rows[0].update(id_modelo_fenix=20)
    )
    with pytest.raises(IntegrityError, match="CROSS_LANE_CONTRACT_CONFLICT"):
        combine(live, history, max_rows=100000)


def test_mia_rekey_requires_verified_exclusion_of_the_old_group(tmp_path, native):
    from ingesta_delta_lake.pipeline_rows import review_hash

    from app.gold.evidence import combine

    history = industrial_evidence(tmp_path / "history", "history", recent=True, native=native)
    live = revised_evidence(
        history, tmp_path / "live", "LISTA_RECH", lambda rows: rows[0].update(INSPECC=500)
    )
    with pytest.raises(IntegrityError, match="CROSS_LANE_CONTRACT_CONFLICT"):
        combine(live, history, max_rows=100000)
    before = history.table("LISTA_RECH", max_rows=100000).to_pylist()[0]
    live.document["rejections"] = {"LISTA_RECH": [review_hash("LISTA_RECH", before)]}
    accepted = combine(live, history, max_rows=100000)
    assert len(accepted.rows("LISTA_RECH")) == 4


def test_coverage_merges_adjacent_periods_without_inventing_gap_coverage():
    from app.gold.evidence import combined_coverage

    def interval(start, end):
        return {
            "source": "LISTA_RECH",
            "origin": "AV23",
            "estado": "READY",
            "covered_from": start,
            "covered_until": end,
        }

    first = interval("2026-11-17T10:00:00", "2026-11-17T11:00:00")
    second = interval("2026-11-17T11:00:00", "2026-11-17T12:00:00")
    values = combined_coverage([first, second])
    assert len(values) == 1 and values[0]["covered_until"] == second["covered_until"]
    assert values[0]["covered_from"] == first["covered_from"]
    second["covered_from"] = "2026-11-17T11:10:00"
    assert len(combined_coverage([first, second])) == 2
    blocked = {
        "source": "LISTA_RECH",
        "origin": "AV23",
        "estado": "WAITING_DEPENDENCY",
        "reason_code": "MANUAL_REVIEW",
    }
    assert blocked in combined_coverage([first, blocked])


def test_independent_gold_worker_has_a_private_identity_and_durable_failure_status(
    independent, monkeypatch
):
    from app.gold.common import read_json
    from app.gold.independent_runtime import Worker

    plan, cfg = independent
    # This fixture already created bridge inputs. Use the still-empty historical Gold store.
    worker = Worker(plan, "history", cfg, score={}, catalogs={})
    identity = worker.prepare()
    assert worker.prepare() == identity
    monkeypatch.setattr(worker, "cycle", lambda: (_ for _ in ()).throw(ValueError("input pending")))
    with pytest.raises(IntegrityError, match="input pending"):
        worker.run(once=True)
    health = read_json(plan.history.gold / "checkpoint/health.json")
    assert health["status"] == "FAILED" and health["lane"] == "history"
    assert worker.historical_reference() is None


def test_gold_health_observes_its_owner_lock_without_waiting_on_history(independent):
    import portalocker

    from app.gold.independent_runtime import Worker

    plan, cfg = independent
    worker = Worker(plan, "live", cfg, score={}, catalogs={})
    worker.prepare()
    with pytest.raises(IntegrityError, match="not running"):
        worker.health()
    with portalocker.Lock(worker.root / "checkpoint/worker.lock", timeout=0):
        assert worker.health() == {"status": "STARTING", "lane": "live"}


def test_worker_spill_is_on_its_own_volume_and_recovers_only_abandoned_work(
    independent, monkeypatch
):
    from app.gold.independent_runtime import Worker

    plan, cfg = independent
    worker = Worker(plan, "live", cfg, score={}, catalogs={})
    worker.prepare()
    abandoned = worker.spill_root / "gold-facts-abandoned"
    abandoned.mkdir(parents=True)
    (abandoned / "facts.sqlite").touch()
    kept = worker.spill_root / "other"
    kept.mkdir()
    history = plan.history.gold / "checkpoint" / "facts-work" / "gold-facts-history"
    history.mkdir(parents=True)

    def check_paths():
        assert not abandoned.exists()
        assert kept.exists() and history.exists()
        return {"status": "UNCHANGED"}

    monkeypatch.setattr(worker, "cycle", check_paths)
    worker.run(once=True)


@pytest.mark.parametrize("phase", ["commit:publication_scope_status", "promoted", "legacy"])
def test_worker_recovers_original_input_before_exporting_new_silver(
    tmp_path, native, monkeypatch, phase
):
    from datetime import timedelta
    from time import monotonic
    from types import SimpleNamespace

    from ingesta_delta_lake.pipeline_rows import portable

    from app.gold import independent_runtime as runtime
    from app.gold.attribution import source_id
    from app.gold.common import atomic_json, read_json
    from app.gold.independent_inputs import cutoff
    from app.gold.pipeline import run
    from app.gold.publications import Snapshot

    from .test_pipeline import CUTOFF, bundle

    latest = CUTOFF - timedelta(hours=1)
    root = tmp_path / "gold"
    output = root / "published"
    old = industrial_evidence(root / "inputs", "live", recent=False, native=native)
    new = industrial_evidence(root / "inputs", "live", recent=True, native=native, horizon=latest)
    config = read_json(Path(__file__).parents[3] / "config/customer_score.json")
    catalogs = bundle()

    def interrupt(point):
        if point == ("commit:publication_scope_status" if phase == "legacy" else phase):
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        run(
            old.root,
            output,
            confirmed_input=old,
            bundle=catalogs,
            config=config,
            cutoff=cutoff(old),
            environment="test",
            plant="AV",
            failpoint=interrupt,
        )
    journal = next((output / "operations").glob("*.json"))
    pending = read_json(journal)["operation_id"]
    if phase == "legacy":
        path = next((output / "runs").glob("*.json"))
        state = read_json(path)
        for key in ("operation_id", "cutoff", "base_publication_id", "reference_publication_id"):
            del state[key]
        atomic_json(path, state)

    plan = SimpleNamespace(
        lane=lambda _: SimpleNamespace(gold=root),
        policy=SimpleNamespace(read_max_rows=100000, window_seconds=600),
    )
    source = SimpleNamespace(environment="test", plant="AV")
    worker = runtime.Worker(plan, "live", source, score=config, catalogs=catalogs)
    monkeypatch.setattr(worker, "historical_reference", lambda: None)

    def next_input(*args):
        assert read_json(journal)["state"] == "ACKNOWLEDGED"
        original = Snapshot.open(output)
        assert original.publication_id == pending
        assert original.table("publication_scope_status").version() == 0
        return new

    monkeypatch.setattr(runtime, "export", next_input)
    started = monotonic()
    result = worker.cycle()
    assert monotonic() - started < 600
    assert result["recovered_publication_id"] == pending
    current = Snapshot.open(output)
    assert current.manifest["input_publication_id"] == new.publication_id
    assert result["incremental"]["chunk_productions"] == ["production-3"]
    original = Snapshot.open(output, publication_id=pending)
    for family, fact in (("LISTA_RECH", "mia_inspections"), ("LISTA_DEFECTOS", "mia_rejections")):
        current_ids = {row["id_observacion"] for row in current.rows(fact)}
        expected = {
            source_id(family, portable(row)) for row in new.rows(family) if row["FECHA"] == latest
        }
        older = {
            source_id(family, portable(row)) for row in new.rows(family) if row["FECHA"] < latest
        }
        assert expected and expected <= current_ids
        assert not older & current_ids
        assert all(
            row["id_observacion"] in current_ids
            for row in original.rows(fact)
            if row["id_produccion"] != "production-3"
        )
    latest_counter = next(row for row in new.rows("CONT_ZC") if row["FECHA"] == latest)
    counter_id = source_id("CONT_ZC", portable(latest_counter))
    assert counter_id in {row["id_observacion"] for row in current.rows("zc_observations")}


def test_gold_live_publishes_first_mia_reference_and_score_in_delta(tmp_path, native):
    from datetime import timedelta

    from app.gold.common import read_json
    from app.gold.mia_statistics import LIVE_SAMPLE
    from app.gold.pipeline import run
    from app.gold.publications import Snapshot

    from .test_pipeline import CUTOFF, bundle

    target = CUTOFF - timedelta(hours=1)
    evidence = industrial_evidence(
        tmp_path / "inputs", "live", recent=True, native=native, horizon=target
    )
    output = tmp_path / "published"
    result = run(
        evidence.root,
        output,
        confirmed_input=evidence,
        bundle=bundle(),
        config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
        cutoff=target,
        environment="test",
        plant="AV",
        lane="live",
        current_slot_seconds=600,
        mia_reference_mode="live",
    )
    assert result["status"] == "PUBLISHED"
    published = Snapshot.open(output, require_gold_contract=True)
    samples = {
        r["reference_id"]
        for r in published.rows("mia_reference_sets")
        if r["reference_rule_id"] == LIVE_SAMPLE
    }
    values = [r for r in published.rows("mia_reference_values") if r["reference_id"] in samples]
    assert values and all(r["muestras"] == 1 and r["sigma_percent"] == 0 for r in values)
    scores = [r for r in published.rows("quality_scores") if r["corte"] == target]
    assert scores
    parts = published.rows("quality_score_components")
    assert {
        r["score"]
        for r in parts
        if r["componente"] == "MIA" and r["id_resultado"] in {s["id_resultado"] for s in scores}
    } == {0.0}


def test_historical_gold_publishes_model_and_segment_statistics(tmp_path, native):
    from app.gold.common import read_json
    from app.gold.mia_statistics import HISTORICAL
    from app.gold.pipeline import run
    from app.gold.publications import Snapshot

    from .test_pipeline import CUTOFF, bundle

    evidence = industrial_evidence(tmp_path / "inputs", "history", recent=True, native=native)
    output = tmp_path / "published"
    result = run(
        evidence.root,
        output,
        confirmed_input=evidence,
        bundle=bundle(),
        config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
        cutoff=CUTOFF,
        environment="test",
        plant="AV",
        mia_reference_mode="history",
    )
    assert result["status"] == "PUBLISHED"
    published = Snapshot.open(output, require_gold_contract=True)
    groups = [
        r for r in published.rows("mia_reference_sets") if r["reference_rule_id"] == HISTORICAL
    ]
    assert {r["nivel"] for r in groups} == {"MODEL", "SEGMENT"}
    refs = {r["reference_id"] for r in groups}
    values = [r for r in published.rows("mia_reference_values") if r["reference_id"] in refs]
    assert values and all(r["muestras"] == 4 and r["estado"] == "READY" for r in values)

    from datetime import timedelta

    target = CUTOFF - timedelta(hours=1)
    live_evidence = industrial_evidence(
        tmp_path / "live-inputs",
        "live",
        recent=True,
        native=native,
        horizon=target,
    )
    live_root = tmp_path / "live-gold"
    live_result = run(
        live_evidence.root,
        live_root,
        confirmed_input=live_evidence,
        bundle=bundle(),
        config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
        cutoff=target,
        environment="test",
        plant="AV",
        lane="live",
        current_slot_seconds=600,
        mia_reference_mode="live",
        reference_snapshot=published,
    )
    assert live_result["status"] == "PUBLISHED"
    details = Snapshot.open(live_root).rows("mia_score_details")
    assert details and {r["muestras"] for r in details} == {4}
