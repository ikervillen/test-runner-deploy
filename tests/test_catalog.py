import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake import catalog
from ingesta_delta_lake.catalog_control import CatalogControl
from ingesta_delta_lake.catalog_service import search
from ingesta_delta_lake.catalog_storage import CatalogStore, verify_manifest
from ingesta_delta_lake.cli import main
from ingesta_delta_lake.common import ConfigurationError, IngestaError, IntegrityError
from ingesta_delta_lake.mp9_storage import Mp9Store
from ingesta_delta_lake.pipeline_publications import publish_family
from ingesta_delta_lake.service import run


@pytest.fixture
def config(config):
    return replace(
        config,
        family="CATALOGO_PRODUCCIONES",
        timestamp_policy="local_naive_test_v1",
        line_by_origin=(("AV21", "AV21"),),
    )


def line(identifier=1, model=754, on="01/10/2026 6:00:00", off="", zone=1, rel="", ok=1, machine=7):
    return f"{identifier};{model};{on};{off};{machine};1;{zone};{rel};1;{ok};\n"


@pytest.fixture
def source(config):
    directory = config.input_root / "AV21"
    (directory / "SIP_MODELO.csv").write_text(
        "754;3081/360;1;\n800;OTHER/001;1;\n", encoding="utf-8"
    )
    run(replace(config, family="SIP_MODELO"))
    path = directory / "MP9_CAMBIO_MODELO.csv"
    path.write_text(line() + line(2, zone=2, rel=1, on="01/10/2026 7:00:00"), encoding="utf-8")
    run(replace(config, family="MP9_CAMBIO_MODELO"))
    return path


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def rows(config):
    identifier = query(config, "SELECT value FROM settings WHERE key='control_id'")[0][0]
    return CatalogStore(config, identifier).read()[1]


def files(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def close_source(config, source):
    source.write_text(
        line(off="14/10/2026 6:23:00")
        + line(2, zone=2, rel=1, on="01/10/2026 7:00:00", off="14/10/2026 7:23:14"),
        encoding="utf-8",
    )
    return run(replace(config, family="MP9_CAMBIO_MODELO"))


def test_schema_identity_relationship_and_no_change_replay(config, source, monkeypatch):
    first = run(config)
    assert first["status"] == "APPLIED" and first["complete"]
    assert first["published_productions"] == 1 and first["without_recorded_end"] == 1
    assert DeltaTable(str(config.table_path)).to_pyarrow_table().schema == catalog.SCHEMA
    assert catalog.SCHEMA.names[-1] == "ID_ST1_REL"
    identity = catalog.production_id("AIALA", "AV21", 1)
    assert list(rows(config)) == [identity]
    assert rows(config)[identity]["ID_ST1_REL"] == 1
    before = files(config.table_path)
    monkeypatch.setattr(CatalogStore, "apply_changes", lambda *_: pytest.fail("No debe escribir"))
    assert run(config)["status"] == "NO_CHANGE"
    assert run(config)["status"] == "NO_CHANGE"
    assert files(config.table_path) == before
    assert query(
        config, "SELECT kind,state,COUNT(*) FROM production_intents GROUP BY kind,state"
    ) == [("INSERT", "CONFIRMED", 1)]


def test_confirmed_historical_closures_advance_live_catalog(config, source, tmp_path):
    history = replace(
        config,
        bronze_root=tmp_path / "historical-bronze",
        silver_root=tmp_path / "historical-silver",
        control_root=tmp_path / "historical-control",
        pipeline_lane="history",
    )
    exchange = tmp_path / "historical-exchange"
    storage = SimpleNamespace(exchange=exchange, silver=history.silver_root)
    live = replace(
        config, pipeline_lane="live", history_exchange=exchange, history_silver=history.silver_root
    )
    identity = catalog.production_id("AIALA", "AV21", 1)
    assert run(live)["status"] == "APPLIED"
    assert rows(live)[identity]["fecha_fin_zf"] is None

    run(replace(history, family="SIP_MODELO"))
    close_source(history, source)
    publish_family(history, storage, lane="history", identity="fixture", family="MP9_CAMBIO_MODELO")

    result = run(live)
    assert result["status"] == "APPLIED"
    assert rows(live)[identity]["fecha_fin_zf"] == "2026-10-14T07:23:14"
    manifest = result["source_manifest"]
    assert manifest["MP9_CAMBIO_MODELO"]["historical_closures"]["publication_id"]
    pinned = verify_manifest(live, manifest)
    assert pinned["MP9_CAMBIO_MODELO"]["AV21", 2]["FECHA_OFF"] == "2026-10-14T07:23:14"
    assert run(live)["status"] == "NO_CHANGE"


def test_model_search_without_dates_is_read_only_and_keeps_model_dictionary_authoritative(
    config, source
):
    run(config)
    before_catalog = files(config.table_path)
    before_control = query(config, "SELECT COUNT(*) FROM attempts")
    result = search(config, "3081/360")
    assert result["status"] == "OK" and result["complete"] and result["fresh"]
    assert len(result["productions"]) == 1
    assert result["productions"][0]["codigo_modelo_botella"] == "3081/360"
    assert result["productions"][0]["component_ids"] == [1, 2]
    assert "codigo_modelo_botella" not in catalog.SCHEMA.names
    assert search(config, "MISSING/000")["productions"] == []
    assert files(config.table_path) == before_catalog
    assert query(config, "SELECT COUNT(*) FROM attempts") == before_control


def test_close_uses_final_zf_and_does_not_change_identity_or_first_evidence(config, source):
    run(config)
    identity = next(iter(rows(config)))
    original = query(config, "SELECT after_json FROM production_intents WHERE kind='INSERT'")
    close_source(config, source)
    result = run(config)
    assert result["updated_this_attempt"] == 1 and result["delta_version"] == 1
    assert result["closed_productions"] == 1
    assert rows(config)[identity]["fecha_fin_zf"] == "2026-10-14T07:23:14"
    assert (
        query(config, "SELECT after_json FROM production_intents WHERE kind='INSERT'") == original
    )
    assert (
        DeltaTable(str(config.table_path), version=0)
        .to_pyarrow_table()
        .to_pylist()[0]["fecha_fin_zf"]
        is None
    )
    source.write_text(line() + line(2, zone=2, rel=1, on="01/10/2026 7:00:00"), encoding="utf-8")
    run(replace(config, family="MP9_CAMBIO_MODELO"))
    assert run(config)["updated_this_attempt"] == 0
    assert rows(config)[identity]["fecha_fin_zf"] == "2026-10-14T07:23:14"


def test_stale_query_retains_last_confirmed_snapshot_until_explicit_build(config, source):
    run(config)
    close_source(config, source)
    before = files(config.table_path)
    result = search(config, "3081/360")
    assert result["status"] == "STALE" and not result["fresh"] and not result["complete"]
    assert result["productions"][0]["fecha_fin_zf"] is None
    assert files(config.table_path) == before
    run(config)
    assert search(config, "3081/360")["status"] == "OK"


def test_same_model_in_different_roots_or_origins_never_merges(config, source):
    source.write_text(
        source.read_text(encoding="utf-8")
        + line(3, on="10/10/2026 6:00:00")
        + line(4, zone=2, rel=3, on="10/10/2026 7:00:00"),
        encoding="utf-8",
    )
    other = config.input_root / "AV22"
    other.mkdir()
    (other / "MP9_CAMBIO_MODELO.csv").write_text(line() + line(2, zone=2, rel=1), encoding="utf-8")
    selected = replace(
        config,
        sources=(*config.sources, ("AV22", other)),
        line_by_origin=(*config.line_by_origin, ("AV22", "AV22")),
    )
    run(replace(selected, family="MP9_CAMBIO_MODELO"))
    result = run(selected)
    assert result["published_productions"] == 3
    found = search(selected, "3081/360")["productions"]
    assert len(found) == len({r["id_produccion"] for r in found}) == 3
    assert found[0]["id_zc_raiz"] == 3  # Orden de inicio, no orden del ID.


def test_missing_zf_is_coverage_pending_not_a_fabricated_end(config, source):
    # Un origen nuevo con solo ZC muestra cobertura pendiente; ZF_OK=1 no lo cierra.
    source.write_text(line(3, model=800, off="02/10/2026 6:00:00"), encoding="utf-8")
    run(replace(config, family="MP9_CAMBIO_MODELO"))
    result = run(config)
    assert result["status"] == "PARTIAL" and result["coverage_pending"] == 1
    found = search(config, "OTHER/001")["productions"][0]
    assert found["fecha_fin_zf"] is None and found["ID_ST1_REL"] is None
    assert found["coverage"] == "PENDING_ZF"
    source.write_text(
        line(4, model=800, zone=2, rel=3, on="01/10/2026 7:00:00", off="02/10/2026 7:00:00"),
        encoding="utf-8",
    )
    run(replace(config, family="MP9_CAMBIO_MODELO"))
    assert run(config)["complete"]
    found = search(config, "OTHER/001")["productions"][0]
    assert found["ID_ST1_REL"] == 3 and found["fecha_fin_zf"] == "2026-10-02T07:00:00"


def test_maximum_over_distinct_zf_only_when_all_are_closed(config, source):
    source.write_text(
        line(off="13/10/2026 6:00:00")
        + line(2, zone=2, rel=1, on="01/10/2026 7:00:00", off="13/10/2026 7:00:00")
        + line(3, zone=2, rel=1),
        encoding="utf-8",
    )
    run(replace(config, family="MP9_CAMBIO_MODELO"))
    run(config)
    assert search(config, "3081/360")["productions"][0]["fecha_fin_zf"] is None
    source.write_text(line(3, zone=2, rel=1, off="14/10/2026 8:00:00"), encoding="utf-8")
    run(replace(config, family="MP9_CAMBIO_MODELO"))
    assert run(config)["closed_productions"] == 1
    assert search(config, "3081/360")["productions"][0]["fecha_fin_zf"] == "2026-10-14T08:00:00"


def test_source_conflicts_remain_visible_and_unaccepted_root_is_not_published(config, source):
    source.write_text(
        source.read_text(encoding="utf-8")
        + line(3, model=800, machine=7)
        + line(3, model=800, machine=8)
        + line(4, model=800, zone=2, rel=3),
        encoding="utf-8",
    )
    assert run(replace(config, family="MP9_CAMBIO_MODELO"))["status"] == "PARTIAL"
    result = run(config)
    assert result["candidate_productions"] == 2 and result["published_productions"] == 1
    assert not result["complete"] and result["source_issues"] == 2
    assert result["pending_candidates"][0]["id_zc_raiz"] == 3
    assert result["pending_candidates"][0]["published"] is False
    assert search(config, "OTHER/001")["productions"] == []


def test_new_source_conflict_prevents_closing_accepted_production(config, source):
    run(config)
    close_source(config, source)
    source.write_text(source.read_text(encoding="utf-8") + line(machine=8), encoding="utf-8")
    run(replace(config, family="MP9_CAMBIO_MODELO"))
    result = run(config)
    assert not result["complete"] and result["updated_this_attempt"] == 0
    found = search(config, "3081/360")["productions"][0]
    assert found["fecha_fin_zf"] is None and found["coverage"] == "REVIEW"


def test_late_component_of_closed_production_requires_review_not_automatic_reopening(
    config, source
):
    close_source(config, source)
    run(config)
    old = files(config.table_path)
    source.write_text(line(3, zone=2, rel=1), encoding="utf-8")
    run(replace(config, family="MP9_CAMBIO_MODELO"))
    result = run(config)
    assert result["status"] == "PARTIAL" and result["updated_this_attempt"] == 0
    found = search(config, "3081/360")["productions"][0]
    assert found["fecha_fin_zf"] == "2026-10-14T07:23:14"
    assert found["component_ids"] == [1, 2] and found["coverage"] == "REVIEW"
    assert files(config.table_path) == old
    assert not run(config)["complete"]


def test_line_mapping_is_explicit_and_changes_do_not_mutate_catalog(config, source):
    with pytest.raises(ConfigurationError):
        run(replace(config, line_by_origin=()))
    with pytest.raises(ConfigurationError, match="correspondencia"):
        run(replace(config, line_by_origin=(("AV99", "AV99"),)))
    run(config)
    changed = replace(config, line_by_origin=(("AV21", "LINE_OTHER"),))
    assert search(changed, "3081/360")["status"] == "STALE"
    assert not run(changed)["complete"]
    assert next(iter(rows(config).values()))["linea"] == "AV21"
    # Volver a la configuración anterior no decide ni borra el conflicto.
    assert not run(config)["complete"]


@pytest.mark.parametrize("phase", ["before", "after", "ack"])
@pytest.mark.parametrize("operation", ["insert", "close"])
def test_recovery_does_not_duplicate_productions_or_updates(
    config, source, monkeypatch, phase, operation
):
    if operation == "close":
        run(config)
        close_source(config, source)
    original = CatalogStore.apply_changes

    def fail(self, *args):
        if phase == "after":
            original(self, *args)
        raise OSError("Simulated failure")

    with monkeypatch.context() as patch:
        if phase == "ack":
            patch.setattr(
                CatalogControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack"))
            )
        else:
            patch.setattr(CatalogStore, "apply_changes", fail)
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["delta_version"] == (1 if operation == "close" else 0)
    assert result["published_productions"] == 1
    assert result["recovered_operations"][0]["wrote_this_attempt"] == (phase == "before")
    assert query(config, "SELECT COUNT(*) FROM production_intents WHERE state!='CONFIRMED'") == [
        (0,)
    ]


def test_source_may_advance_before_catalog_retry_without_mixing_versions(
    config, source, monkeypatch
):
    with monkeypatch.context() as patch:
        patch.setattr(
            CatalogStore, "apply_changes", lambda *_: (_ for _ in ()).throw(OSError("before"))
        )
        with pytest.raises(IngestaError):
            run(config)
    close_source(config, source)
    result = run(config)
    assert result["delta_version"] == 1 and result["inserted_this_attempt"] == 1
    assert result["updated_this_attempt"] == 1 and result["published_productions"] == 1
    publications = query(
        config, "SELECT source_manifest_json FROM publications ORDER BY publication_no"
    )
    assert [json.loads(row[0])["MP9_CAMBIO_MODELO"]["version"] for row in publications] == [0, 1]


def test_unconfirmed_catalog_commit_is_not_exposed_as_confirmed_query(config, source, monkeypatch):
    run(config)
    close_source(config, source)
    with monkeypatch.context() as patch:
        patch.setattr(CatalogControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack")))
        with pytest.raises(IngestaError):
            run(config)
    result = search(config, "3081/360")
    assert result["status"] == "STALE" and result["delta_version"] == 0
    assert result["productions"][0]["fecha_fin_zf"] is None
    run(config)
    assert search(config, "3081/360")["delta_version"] == 1


def test_search_never_creates_missing_catalog_or_sources(config):
    assert search(config, "3081/360")["status"] == "NO_CATALOG"
    assert not config.control_root.exists()
    assert run(config)["status"] == "NO_INPUTS" and not config.table_path.exists()
    assert search(config, "3081/360")["status"] == "NO_CATALOG"


def test_catalog_source_reads_do_not_modify_raw_csv_or_source_control(config, source):
    mp9_config = replace(config, family="MP9_CAMBIO_MODELO")
    source_files = files(mp9_config.table_path)
    with sqlite3.connect(mp9_config.control_path) as db:
        before_attempts = db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    raw = source.read_bytes()
    run(config)
    search(config, "3081/360")
    assert source.read_bytes() == raw and files(mp9_config.table_path) == source_files
    with sqlite3.connect(mp9_config.control_path) as db:
        assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == before_attempts


def test_query_rejects_different_plant_and_unconfirmed_source_blocks_build(
    config, source, monkeypatch
):
    run(config)
    with pytest.raises(IntegrityError):
        search(replace(config, plant="OTHER"), "3081/360")
    source.write_text(line(3, model=800), encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(
            Mp9Store, "apply_changes", lambda *_: (_ for _ in ()).throw(OSError("before"))
        )
        with pytest.raises(IngestaError):
            run(replace(config, family="MP9_CAMBIO_MODELO"))
    with pytest.raises(IntegrityError, match="Recuperar MP9"):
        run(config)
    assert search(config, "3081/360")["status"] == "STALE"


def test_catalog_cli_build_and_search_require_correct_family(config, source, tmp_path, capsys):
    values = {
        "plant": config.plant,
        "family": config.family,
        "timestamp_policy": config.timestamp_policy,
    }
    values.update(
        {
            k: str(getattr(config, k))
            for k in ("input_root", "bronze_root", "silver_root", "control_root")
        }
    )
    file = tmp_path / "catalog.toml"
    file.write_text(
        "\n".join(f"{k} = {json.dumps(v)}" for k, v in values.items())
        + '\n[sources]\nAV21 = "AV21"\n[line_by_origin]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    assert main(["catalog", "build", "--config", str(file)]) == 0
    assert json.loads(capsys.readouterr().out)["published_productions"] == 1
    assert main(["catalog", "search", "--config", str(file), "--model", "3081/360"]) == 0
    assert len(json.loads(capsys.readouterr().out)["productions"]) == 1
    assert main(["ingest", "--config", str(file)]) == 3
    assert json.loads(capsys.readouterr().err)["status"] == "FAILED"


def test_source_quality_change_without_delta_commit_invalidates_publication(config, source):
    run(config)
    before = files(config.table_path)
    source.write_text(line(machine=8), encoding="utf-8")
    mp9_result = run(replace(config, family="MP9_CAMBIO_MODELO"))
    assert mp9_result["delta_version"] == 0
    assert search(config, "3081/360")["status"] == "STALE"
    result = run(config)
    assert result["status"] == "PARTIAL" and result["delta_version"] == 0
    assert files(config.table_path) == before
    assert search(config, "3081/360")["productions"][0]["coverage"] == "REVIEW"


def test_invalid_global_limits_do_not_invent_a_production_end(config, source):
    source.write_text(
        line(3, model=800, on="20/10/2026 6:00:00")
        + line(4, model=800, zone=2, rel=3, off="02/10/2026 7:00:00"),
        encoding="utf-8",
    )
    run(replace(config, family="MP9_CAMBIO_MODELO"))
    assert not run(config)["complete"]
    found = search(config, "OTHER/001")["productions"][0]
    assert found["fecha_fin_zf"] is None and "INVALID_LIMITS" in found["review_reasons"]


def test_prepared_source_hash_mismatch_blocks_recovery(config, source, monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            CatalogStore, "apply_changes", lambda *_: (_ for _ in ()).throw(OSError("before"))
        )
        with pytest.raises(IngestaError):
            run(config)
    with sqlite3.connect(config.control_path) as db:
        prepared = json.loads(db.execute("SELECT prepared_json FROM batches").fetchone()[0])
        prepared["source_manifest"]["MP9_CAMBIO_MODELO"]["hash"] = "altered"
        db.execute("UPDATE batches SET prepared_json=?", (json.dumps(prepared),))
    with pytest.raises(IntegrityError, match="snapshot"):
        run(config)
    assert not config.table_path.exists()
