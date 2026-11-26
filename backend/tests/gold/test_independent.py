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
        timestamp_policy="local_naive_test_v1",
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


def test_gold_bridge_accepts_multiple_confirmed_families(independent, native):
    from ingesta_delta_lake.pipeline_intake import Intake
    from ingesta_delta_lake.pipeline_runtime import PipelineWorker

    from app.gold.independent_inputs import export

    plan, cfg = independent
    folder = cfg.sources[0][1]
    values = ["15/10/2026 12:00:00", 0, 1, 13, 15, 96, 2, 2, *([0] * 9), ""]
    (folder / "15102026_120000_LISTA_DEFECTOS.csv").write_text(";".join(map(str, values)) + ";\n")
    worker = PipelineWorker(plan, "live", cfg)
    worker.prepare()
    with closing(
        Intake(worker.source.intake_path, lane="live", identity=worker.identity)
    ) as intake:
        worker.cycle(intake, phases=(("LISTA_DEFECTOS",),))
    result = export(plan, "live", cfg)
    assert len(result.rows("LISTA_DEFECTOS", max_rows=10)) == 1
    assert len(result.rows("SIP_MODELO", max_rows=10)) == 1


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
        lambda *a, **k: (tables, cursor + 1, (version[0], version[1], cursor + 1)),
    )
    slot_changed = independent_inputs.export(plan, "live", cfg)
    assert slot_changed.publication_id != original.publication_id
    assert (
        independent_inputs.export(plan, "live", cfg).publication_id == slot_changed.publication_id
    )

    monkeypatch.setattr(
        independent_inputs,
        "auxiliary_turn",
        lambda *a, **k: (tables, cursor + 2, (version[0], cursor + 2, cursor + 1)),
    )
    updated = independent_inputs.export(plan, "live", cfg)
    assert updated.publication_id != original.publication_id
    assert updated.document["silver_read"]["rows"] == 0
    assert "SIP_MODELO" in updated.document["silver_read"]["reused_catalogues"]
    update = independent_inputs.fragment_delta.update

    def one_family_fallback(snapshot, previous, family, *args, **kwargs):
        if family == "CONT_ZC":
            raise independent_inputs.FullReadRequired
        return update(snapshot, previous, family, *args, **kwargs)

    monkeypatch.setattr(independent_inputs.fragment_delta, "update", one_family_fallback)
    monkeypatch.setattr(
        independent_inputs,
        "auxiliary_turn",
        lambda *a, **k: (tables, cursor + 3, (version[0], cursor + 3, cursor + 1)),
    )
    revised = independent_inputs.export(plan, "live", cfg)
    stats = revised.document["silver_read"]
    assert stats["full_read_families"] == []
    assert set(stats["reconciliation"]) == {"CONT_ZC"}
    assert stats["reconciliation"]["CONT_ZC"]["examined"] == 0
    assert revised.document["silver_read"]["incremental"] is True


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
    stored = result["write_telemetry"]["evidence"]
    assert stored["new_links"] > 0 and stored["new_physical_bytes"] == 0
    assert result["write_telemetry"]["tables"]["publication_scope_status"]["commits"] == 1
    assert published_evidence(root).publication_id == evidence.publication_id
    snapshot = Snapshot.open(root, require_gold_contract=True)
    assert snapshot.rows("productions") == []
    assert snapshot.rows("publication_scope_status") == []
    assert run(evidence.root, root, **kwargs)["status"] == "UNCHANGED"


def industrial_evidence(root, lane, *, recent, native, horizon=None, window_seconds=None):
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
    return save(root, document, tables, window_seconds=window_seconds)


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
    from app.config import Settings
    from app.gold.combined_query import CombinedQuery
    from app.gold.common import read_json
    from app.gold.pipeline import run
    from app.gold.service import GoldService

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
    settings = Settings(
        data_root=roots["live"],
        data_backend="gold",
        gold_root=roots["live"],
        gold_history_root=roots["history"],
        gold_history_enabled=False,
    )
    direct = GoldService(settings).query()
    assert not isinstance(direct, CombinedQuery)
    assert direct.snapshot.publication_id == query.live.snapshot.publication_id


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

    def next_input(*args, **kwargs):
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


def test_gold_live_publishes_first_mia_reference_and_score_in_delta(tmp_path, native, monkeypatch):
    from datetime import timedelta

    from app.gold.common import read_json
    from app.gold.mia_statistics import LIVE_SAMPLE
    from app.gold.pipeline import run
    from app.gold.publications import Snapshot

    from .test_pipeline import CUTOFF, bundle

    monkeypatch.setattr("app.gold.pipeline.utc_now", lambda: "2026-11-20T12:00:00+00:00")
    target = CUTOFF - timedelta(hours=1)
    evidence = industrial_evidence(
        tmp_path / "inputs", "live", recent=True, native=native, horizon=target, window_seconds=600
    )
    output = tmp_path / "published"
    options = dict(
        bundle=bundle(),
        config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
        cutoff=target,
        environment="test",
        plant="AV",
        lane="live",
        current_slot_seconds=600,
        mia_reference_mode="live",
    )
    result = run(evidence.root, output, confirmed_input=evidence, **options)
    assert result["status"] == "PUBLISHED"
    assert result["write_telemetry"]["validation_ms"] >= 0
    timings = result["incremental"]["score_timing"]
    assert result["incremental"]["stage_seconds"]["reference_samples"] >= 0
    assert timings["productions"] > 0
    assert timings["worker_calls"]["sqlite_load"] == timings["productions"]
    for stage in ("mia_bases", "ar_bases", "zc_bases", "calculate"):
        assert timings["worker_calls"][stage] > 0
        assert timings["worker_seconds"][stage] >= 0
    assert timings["slow_productions"][0]["windows"][0]["cutoff"] == target.isoformat()
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

    batches = Evidence.batches

    def baseline_batches(self, name, *, window=None, **kwargs):
        return batches(self, name, **kwargs)

    monkeypatch.setattr(Evidence, "batches", baseline_batches)
    baseline_root = tmp_path / "baseline"
    baseline = run(
        evidence.root, baseline_root, confirmed_input=Evidence.open(evidence.root), **options
    )
    assert baseline["status"] == "PUBLISHED"
    reference = Snapshot.open(baseline_root, require_gold_contract=True)
    from app.gold.common import canonical

    def values(snapshot, name):
        return sorted(
            canonical({k: v for k, v in row.items() if k != "fecha_calculo"})
            for row in snapshot.rows(name)
        )

    for name in ("quality_scores", "quality_score_components", "publication_scope_status"):
        assert values(published, name) == values(reference, name)


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


def test_live_shared_history_matches_previous_route_with_same_pinned_cut(
    tmp_path, native, monkeypatch
):
    from copy import deepcopy
    from datetime import timedelta
    from shutil import copytree

    from app.gold import pipeline
    from app.gold.common import canonical, read_json
    from app.gold.publications import Snapshot

    from .test_pipeline import CUTOFF, bundle

    monkeypatch.setattr(pipeline, "utc_now", lambda: "2026-11-20T12:00:00+00:00")
    target = CUTOFF - timedelta(hours=1)
    evidence = industrial_evidence(
        tmp_path / "inputs", "live", recent=True, native=native, horizon=target, window_seconds=600
    )
    config = read_json(Path(__file__).parents[3] / "config/customer_score.json")
    options = dict(
        confirmed_input=evidence,
        bundle=bundle(),
        cutoff=target,
        environment="test",
        plant="AV",
        lane="live",
        current_slot_seconds=600,
        mia_reference_mode="live",
    )
    baseline = tmp_path / "baseline"
    alternate = tmp_path / "candidate"
    initial = pipeline.run(evidence.root, baseline, config=config, **options)
    assert initial["status"] == "PUBLISHED"
    copytree(baseline, alternate)
    scoring = deepcopy(config)
    scoring["weights"]["zc"] += 1

    def force_budget_fallback(*args, **kwargs):
        raise pipeline._SharedHistoryBudget

    with monkeypatch.context() as patch:
        patch.setattr(pipeline, "_shared_history", force_budget_fallback)
        old = pipeline.run(evidence.root, baseline, config=scoring, **options)
    new = pipeline.run(evidence.root, alternate, config=scoring, **options)
    assert old["status"] == new["status"] == "PUBLISHED"
    before = old["incremental"]["score_timing"]
    after = new["incremental"]["score_timing"]
    assert before["history_shared_budget_fallback"]
    assert not after["history_shared_budget_fallback"]
    assert "quality_scores" in before["history_table_calls"]
    assert "quality_scores" not in after["history_table_calls"]
    assert after["history_table_calls"]["mia_window_exposures"] == 1

    left, right = Snapshot.open(baseline), Snapshot.open(alternate)
    assert left.rows("quality_scores")
    assert left.publication_id == right.publication_id
    for ref in left.manifest["tables"]:
        name = ref["name"]
        assert sorted(map(canonical, left.rows(name))) == sorted(map(canonical, right.rows(name)))
    assert (
        pipeline.run(evidence.root, alternate, config=scoring, **options)["status"] == "UNCHANGED"
    )


def test_fragment_cdf_gold_matches_reference_for_same_silver_cut(tmp_path, native, monkeypatch):
    from datetime import timedelta
    from time import perf_counter

    from deltalake import DeltaTable, write_deltalake
    from ingesta_delta_lake.contracts import CONTRACTS
    from ingesta_delta_lake.pipeline_rows import business_key, review_hash

    from app.gold import fragment_delta
    from app.gold.common import canonical, read_json
    from app.gold.evidence import attribution
    from app.gold.lanes import hour_holding
    from app.gold.pipeline import run
    from app.gold.publications import Snapshot
    from app.gold.silver_incremental import materialize

    from .test_pipeline import CUTOFF, bundle

    monkeypatch.setattr("app.gold.pipeline.utc_now", lambda: "2026-11-20T12:00:00+00:00")
    family = "LISTA_RECH"
    target = CUTOFF - timedelta(hours=1)
    schema = CONTRACTS[family].schema
    initial = industrial_evidence(
        tmp_path / "initial", "live", recent=True, native=native, horizon=target, window_seconds=600
    )
    tables = {
        r["name"]: initial.table(r["name"], max_rows=100000)
        for r in initial.document["tables"]
        if r["name"] != "__lineage"
    }
    rech = tables[family].to_pylist()
    earlier = [
        {**rech[0], "FECHA": target - timedelta(days=2, minutes=n * 10), "CAVIDAD": f"old-{n}"}
        for n in range(240)
    ]
    tables[family] = pa.Table.from_pylist([*rech, *earlier], schema=schema)
    silver_root = tmp_path / "silver-delta"
    write_deltalake(
        silver_root, tables[family], configuration={"delta.enableChangeDataFeed": "true"}
    )
    delta = DeltaTable(str(silver_root))
    table_id = delta.metadata().id

    def ref(version):
        return {"table_id": table_id, "version": version, "rejected_hashes": []}

    original_proofs = initial.proofs(max_rows=100000)
    original_proofs[family] = {
        business_key(family, row): [
            {"source_table": table_id, "source_version": 0, "row_hash": review_hash(family, row)}
        ]
        for row in [*rech, *earlier]
    }
    input_root = tmp_path / "input"
    doc = {
        **initial.document,
        "fragment_lineage": 600,
        "silver_refs": {family: ref(0)},
        "lineages": original_proofs,
    }
    save(input_root, doc, tables, window_seconds=600)
    delta.update(
        predicate=f"CAVIDAD = '{rech[0]['CAVIDAD']}'",
        updates={"RECH": "RECH + 1"},
    )
    previous = Evidence.open(input_root)
    native_snapshot = type("Source", (), {"table": lambda self, _: delta})()
    native_snapshot.manifest = {"publication_id": "scope-proof", "tables": {family: ref(1)}}
    started = perf_counter()
    reference_rows, proofs, n = materialize(
        native_snapshot, previous, original_proofs, family, ref(1), schema.names, budget=1000
    )
    baseline_s = perf_counter() - started
    baseline_reads = dict(previous.read_stats)
    previous = Evidence.open(input_root)
    budget = 64
    assert len(reference_rows) > budget
    with pytest.raises(IntegrityError, match="row budget"):
        previous.rows(family, max_rows=budget)
    started = perf_counter()
    fact, lineage, n2 = fragment_delta.update(
        native_snapshot, previous, family, ref(1), schema, budget=budget, seconds=600
    )
    assert fact["rows"] > budget
    assert sum(c["rows_decoded"] for c in previous.read_stats.values()) + n2 < budget
    candidate_s = perf_counter() - started
    print("EXPORT_AB", baseline_s, candidate_s, baseline_reads, dict(previous.read_stats))
    assert n == n2 > 0
    window = (hour_holding(target)[0], target)
    patched = Evidence(input_root, previous.publication_id, {"tables": [fact]})
    candidate_tables = {
        **tables,
        family: pa.Table.from_batches(
            patched.batches(family, max_rows=1000, window=window), schema=schema
        ),
    }
    reference_tables = {**tables, family: pa.Table.from_pylist(reference_rows, schema=schema)}
    decisions = attribution(doc, candidate_tables, window=window)
    baseline_decisions = attribution(doc, reference_tables, window=window)
    assert {k: t.to_pylist() for k, t in decisions.items()} == {
        k: t.to_pylist() for k, t in baseline_decisions.items()
    }
    candidate_tables.update(decisions)
    reference_tables.update(baseline_decisions)
    del candidate_tables[family]
    published_doc = {**doc, "silver_refs": {family: ref(1)}}
    current = save(
        input_root,
        {**published_doc, "lineages": {k: p for k, p in original_proofs.items() if k != family}},
        candidate_tables,
        window_seconds=600,
        fact_refs=[fact],
        lineage_refs=lineage,
    )
    baseline = save(
        input_root,
        {**published_doc, "lineages": {**original_proofs, family: proofs}},
        reference_tables,
        window_seconds=600,
    )
    assert current.proofs(max_rows=100000) == baseline.proofs(max_rows=100000)
    assert current.rows(family, max_rows=1000) == baseline.rows(family, max_rows=1000)
    assert current.publication_id == baseline.publication_id
    wire = next(r for r in current.wire_document["tables"] if r["name"] == family)
    assert "parts" not in wire and "parts_pages" not in wire
    assert wire["parts_root"]["rows"] == wire["rows"]
    options = dict(
        bundle=bundle(),
        config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
        cutoff=target,
        environment="test",
        plant="AV",
        lane="live",
        current_slot_seconds=600,
        mia_reference_mode="live",
    )

    def output(evidence, root):
        result = run(evidence.root, root, confirmed_input=evidence, **options)
        assert result["status"] == "PUBLISHED"
        return Snapshot.open(root, require_gold_contract=True)

    left = output(baseline, tmp_path / "baseline")
    right = output(current, tmp_path / "candidate")
    assert len(left.manifest["tables"]) == len(right.manifest["tables"]) == 36
    assert left.rows("quality_scores")
    for ref_row in left.manifest["tables"]:
        name = ref_row["name"]
        assert sorted(map(canonical, left.rows(name))) == sorted(map(canonical, right.rows(name)))

    from app.gold import scoped_reconciliation

    rebuilt, rebuilt_lineage, metrics = scoped_reconciliation.run(
        tmp_path / "reconcile",
        previous,
        native_snapshot,
        family,
        schema,
        origins=tuple({r["origen_fenix"] for r in reference_rows}),
        seconds=600,
        budget=64,
        batch_size=8,
    )
    recovered = save(
        input_root,
        {**published_doc, "lineages": {k: p for k, p in original_proofs.items() if k != family}},
        candidate_tables,
        window_seconds=600,
        fact_refs=[rebuilt],
        lineage_refs=rebuilt_lineage,
    )
    assert metrics["peak_rows"] <= 64 < rebuilt["rows"]
    assert recovered.proofs(max_rows=100000) == baseline.proofs(max_rows=100000)
    reconciled = output(recovered, tmp_path / "reconciled")
    assert len(reconciled.manifest["tables"]) == 36
    for ref_row in left.manifest["tables"]:
        name = ref_row["name"]
        expected, observed = left.rows(name), reconciled.rows(name)
        if name == "quality_scores":
            for rows, snapshot in ((expected, left), (observed, reconciled)):
                for row in rows:
                    assert row.pop("basis_publication_id") == snapshot.publication_id
        assert sorted(map(canonical, expected)) == sorted(map(canonical, observed))


def test_live_export_can_carry_more_logical_rows_than_its_work_budget(
    independent, native, monkeypatch
):
    from dataclasses import replace
    from datetime import datetime, timedelta
    from time import perf_counter

    from ingesta_delta_lake.pipeline_publications import publish_family
    from ingesta_delta_lake.pipeline_runtime import PipelineWorker
    from ingesta_delta_lake.service import run

    from app.gold.independent_inputs import export

    plan, cfg = independent
    worker = PipelineWorker(plan, "live", cfg)
    source = cfg.sources[0][1] / "CONT_ZC.csv"
    owner = replace(worker.source, family="CONT_ZC", intake_path=None)

    def inputs(stamps):
        return "".join(
            f"21;{stamp:%d/%m/%Y %H:%M:%S};100;100;100;100;0;180;0;100;100;0;0;80.00;80.00;0;1;\n"
            for stamp in stamps
        )

    def publish():
        result = run(owner)
        assert result["status"] == "APPLIED"
        publish_family(
            owner, worker.storage, lane="live", identity=worker.identity, family=owner.family
        )

    start = datetime(2026, 11, 14, 8)
    source.write_text(inputs(start + timedelta(minutes=10 * i) for i in range(300)))
    publish()
    previous = export(plan, "live", cfg)
    assert previous.document["silver_read"]["full_read_families"]
    # Exercise actual legacy on-disk evidence, including source-version lineage partitions.
    from app.gold.evidence import save

    tables = {
        r["name"]: previous.table(r["name"], max_rows=1000)
        for r in previous.document["tables"]
        if r["name"] != "__lineage"
    }
    metadata = {
        k: v for k, v in previous.document.items() if k not in ("tables", "fragment_lineage")
    }
    metadata["lineages"] = previous.proofs(max_rows=1000)
    legacy = save(previous.root, metadata, tables, window_seconds=600)

    source.write_text(inputs([start + timedelta(minutes=3000)]))
    publish()
    small = replace(plan, policy=replace(plan.policy, read_max_rows=128))
    began = perf_counter()
    updated = export(small, "live", cfg)
    print("LEGACY_BUDGET_EXPORT_SECONDS", perf_counter() - began, updated.document["silver_read"])
    probe = updated.document["silver_read"]
    assert probe["logical_rows"] > small.policy.read_max_rows
    assert probe["materialized_rows"] < small.policy.read_max_rows
    assert probe["lineage_bootstrap"] and "CONT_ZC" in probe["fragment_incremental_families"]
    assert updated.table("CONT_ZC", max_rows=1000).num_rows == 301
    assert len(updated.proofs(max_rows=1000)["CONT_ZC"]) == 301
    assert (
        Evidence.open(legacy.root, legacy.publication_id).table("CONT_ZC", max_rows=1000).num_rows
        == 300
    )
    assert export(small, "live", cfg).publication_id == updated.publication_id

    from app.gold import fragment_delta
    from app.gold.silver_incremental import FullReadRequired

    actual_update = fragment_delta.update

    def missing_cdf(snapshot, previous, family, *args, **kwargs):
        if family == "CONT_ZC":
            raise FullReadRequired
        return actual_update(snapshot, previous, family, *args, **kwargs)

    source.write_text(inputs([start + timedelta(minutes=3010)]))
    publish()
    monkeypatch.setattr(fragment_delta, "update", missing_cdf)
    recovered = export(small, "live", cfg)
    assert recovered.table("CONT_ZC", max_rows=1000).num_rows == 302
    stats = recovered.document["silver_read"]
    assert stats["reconciliation"]["CONT_ZC"]["peak_rows"] <= small.policy.read_max_rows
    assert stats["full_read_families"] == []
    assert len(recovered.proofs(max_rows=1000)["CONT_ZC"]) == 302
    assert export(small, "live", cfg).publication_id == recovered.publication_id


@pytest.mark.parametrize(
    ("delay_hours", "gap_family"), [(0, "CONT_ZC"), (9, "CONT_ZC"), (9, "LISTA_DEFECTOS")]
)
def test_late_coverage_publishes_previous_closed_hour_in_real_delta(
    tmp_path, native, monkeypatch, delay_hours, gap_family
):
    from datetime import UTC, timedelta
    from types import SimpleNamespace

    from app.gold.common import read_json
    from app.gold.live_hours import retry_closures
    from app.gold.pipeline import run
    from app.gold.publications import Snapshot
    from app.gold.scoring import has_official_total

    from .test_pipeline import CUTOFF, bundle

    closing = CUTOFF - timedelta(hours=1)
    later = closing + timedelta(hours=delay_hours, minutes=10)
    source = industrial_evidence(
        tmp_path / "source",
        "live",
        recent=True,
        native=native,
        horizon=later,
        window_seconds=600,
    )
    metadata = {k: v for k, v in source.document.items() if k not in {"tables", "schema_id"}}
    tables = {
        r["name"]: source.table(r["name"], max_rows=100000) for r in source.document["tables"]
    }
    coverage = metadata["coverage"]
    late = [
        {**r, "covered_until": (closing - timedelta(minutes=10)).isoformat()}
        if r["source"] == gap_family
        else r
        for r in coverage
    ]
    initial = save(source.root, {**metadata, "coverage": late}, tables, window_seconds=600)
    published = tmp_path / "output"
    options = dict(
        bundle=bundle(),
        config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
        environment="test",
        plant="AV",
        lane="live",
        current_slot_seconds=600,
        mia_reference_mode="live",
    )
    assert (
        run(initial.root, published, confirmed_input=initial, cutoff=closing, **options)["status"]
        == "PUBLISHED"
    )
    before = Snapshot.open(published)
    prior = [r for r in before.rows("quality_scores") if r["corte"] == closing]
    prior_ids = {r["id_resultado"] for r in prior}
    missing = "ZC" if gap_family == "CONT_ZC" else "MIA"
    assert prior_ids and all(not has_official_total(row["formula_version"]) for row in prior)
    assert not any(
        r["id_resultado"] in prior_ids and r["componente"] == missing
        for r in before.rows("quality_score_components")
    )
    older = (closing - timedelta(hours=1), closing) if delay_hours else None
    if older:
        assert not retry_closures(before, coverage, later, 100000)
    retry = retry_closures(before, coverage, later, 100000, older=older)
    assert retry and all(item[3] == closing.isoformat() for item in retry)
    after = save(source.root, {**metadata, "retry_closures": retry}, tables, window_seconds=600)
    assert (
        run(after.root, published, confirmed_input=after, cutoff=later, **options)["status"]
        == "PUBLISHED"
    )
    visible = Snapshot.open(published)
    assert [r for r in visible.rows("quality_scores") if r["corte"] == closing]
    if prior_ids:
        assert prior_ids & {
            r["id_resultado"]
            for r in visible.rows("quality_score_components")
            if r["componente"] == missing
        }
        assert (
            run(after.root, published, confirmed_input=after, cutoff=later, **options)["status"]
            == "UNCHANGED"
        )
        assert Snapshot.open(published).manifest["tables"] == visible.manifest["tables"]
    assert not retry_closures(visible, coverage, later, 100000, older=older)
    if older:
        from app.gold.live_archive import archive
        from app.infrastructure.cache import LruCache

        t0 = closing - timedelta(hours=2)
        monkeypatch.setattr("app.gold.live_archive.live_epoch", lambda: t0)
        monkeypatch.setattr("app.gold.live_view.live_epoch", lambda: t0)
        settings = SimpleNamespace(
            resolved_gold_root=published,
            gold_live_timezone="UTC",
            gold_query_max_rows=100000,
            gold_query_total_rows=300000,
        )
        moment = later.replace(tzinfo=UTC)
        cache = LruCache(4)
        prior = archive(settings, cache, publication_id=before.publication_id, moment=moment)
        recovered = archive(settings, cache, moment=moment)

        def scores(page):
            return {
                row["result_id"]
                for shift in page["shifts"]
                for hour in shift["hours"]
                for row in hour["results"]
                if row["result_id"]
            }

        if prior_ids:
            assert prior_ids <= scores(recovered) & scores(prior)
        else:
            assert scores(recovered) - scores(prior)
        monkeypatch.undo()
    settled = {
        row["id_resultado"]: row
        for row in visible.rows("quality_scores")
        if row["corte"] == closing
    }
    next_cut = later + timedelta(minutes=10)
    advanced = save(source.root, {**metadata, "retry_closures": []}, tables, window_seconds=600)
    assert (
        run(advanced.root, published, confirmed_input=advanced, cutoff=next_cut, **options)[
            "status"
        ]
        == "PUBLISHED"
    )
    final = Snapshot.open(published)
    assert {
        row["id_resultado"]: row
        for row in final.rows("quality_scores")
        if row["id_resultado"] in settled
    } == settled


def test_ar_category_revision_recovers_pinned_hour_in_real_delta(tmp_path, native):
    from datetime import timedelta

    from app.gold.common import read_json
    from app.gold.live_hours import ar_revisions, retry_closures
    from app.gold.pipeline import run
    from app.gold.publications import Snapshot

    from .test_pipeline import CUTOFF, bundle

    closing = CUTOFF - timedelta(hours=1)
    later = closing + timedelta(minutes=10)
    source = industrial_evidence(
        tmp_path / "inputs", "live", recent=True, native=native, horizon=later, window_seconds=600
    )
    metadata = {k: v for k, v in source.document.items() if k not in {"tables", "schema_id"}}
    tables = {
        r["name"]: source.table(r["name"], max_rows=100000) for r in source.document["tables"]
    }
    before_cats = {**bundle(), "defect_categories": {"53": None}}
    initial = save(
        source.root,
        {**metadata, "ar_categories": before_cats["defect_categories"], "ar_pin": "v1"},
        tables,
        window_seconds=600,
    )
    published = tmp_path / "published"
    options = dict(
        config=read_json(Path(__file__).parents[3] / "config/customer_score.json"),
        environment="test",
        plant="AV",
        lane="live",
        current_slot_seconds=600,
        mia_reference_mode="live",
    )
    run(
        initial.root,
        published,
        confirmed_input=initial,
        cutoff=closing,
        bundle=before_cats,
        **options,
    )
    before = Snapshot.open(published)
    assert any(
        "AR_CATEGORY_UNRESOLVED" in (scope["reason_code"] or "")
        for scope in before.rows("publication_scope_status")
        if scope["source"] == "quality_scores"
    )
    next_doc = {**metadata, "ar_categories": bundle()["defect_categories"], "ar_pin": "v1"}
    changes = ar_revisions(
        tmp_path,
        before,
        next_doc,
        lambda bounds: (
            source.rows("LISTA_AR", window=bounds),
            tables["observation_assignments"].to_pylist(),
        ),
        later,
        100000,
    )
    next_doc["retry_closures"] = retry_closures(
        before,
        metadata["coverage"],
        later,
        100000,
        closed=[],
        ar_changed=changes,
    )
    assert len(next_doc["retry_closures"]) == 1
    revised = save(source.root, next_doc, tables, window_seconds=600)
    run(revised.root, published, confirmed_input=revised, cutoff=later, bundle=bundle(), **options)
    current = Snapshot.open(published)
    assert any(r["corte"] == closing for r in current.rows("quality_scores"))
    score = next(r for r in current.rows("quality_scores") if r["corte"] == closing)
    assert any(
        r["reference_id"] == score["reference_id"] for r in current.rows("mia_reference_sets")
    )
    assert any(
        r["evidence_reference"] == revised.publication_id and r["estado"] == "READY"
        for r in current.rows("publication_scope_status")
        if r["source"] == "quality_scores"
    )
