import json
import sqlite3
from dataclasses import replace

import pytest
from deltalake import DeltaTable
from test_mia_batches import csv_row

from ingesta_delta_lake import defects_service as batching
from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.defects_control import DefectsControl
from ingesta_delta_lake.mia_scope import expression, verify_files
from ingesta_delta_lake.service import run


@pytest.fixture(params=["LISTA_RECH", "LISTA_DEFECTOS"])
def mia(config, request):
    return replace(config, family=request.param, timestamp_policy="local_naive_test_v1")


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def test_new_period_never_materializes_old_rows_and_totals_cover_both(mia):
    source = mia.sources[0][1] / (mia.family + ".csv")
    source.write_text(csv_row(mia.family, 0), encoding="utf-8")
    assert run(mia)["resolved_inspection_total"] == 96
    source.write_text(csv_row(mia.family, 10), encoding="utf-8")
    result = run(mia)
    assert result["context_rows"] == 1
    assert result["published_rows"] == result["resolved_groups"] == 2
    assert result["resolved_inspection_total"] == 192
    assert query(mia, "SELECT count(*) FROM inspection_groups") == [(0,)]
    assert query(mia, "SELECT count(*) FROM inspection_current") == [(2,)]
    assert run(mia)["context_rows"] == 0
    assert DeltaTable(str(mia.table_path)).version() == 1


def test_same_context_revisions_still_find_prior_rows(mia):
    source = mia.sources[0][1] / (mia.family + ".csv")
    source.write_text(csv_row(mia.family, 0), encoding="utf-8")
    run(mia)
    source.write_text(csv_row(mia.family, 0, rejected=99), encoding="utf-8")
    result = run(mia)
    assert result["status"] == "PARTIAL" and result["conflicts"] == 1
    assert result["published_rows"] == result["unresolved_groups"] == 1
    assert result["resolved_inspection_total"] == 0
    assert DeltaTable(str(mia.table_path)).version() == 0


def test_unknown_context_error_marks_all_groups_of_origin_without_full_read(mia):
    source = mia.sources[0][1] / (mia.family + ".csv")
    source.write_text(csv_row(mia.family, 0) + csv_row(mia.family, 10), encoding="utf-8")
    run(mia)
    source.write_bytes(b"\xff\xfeINVALID")
    result = run(mia)
    assert result["context_rows"] == 0 and result["published_rows"] == 2
    assert result["resolved_groups"] == 0 and result["unresolved_groups"] == 2


def test_adopts_legacy_confirmed_groups_and_recovers_legacy_pending(mia, monkeypatch):
    source = mia.sources[0][1] / (mia.family + ".csv")
    source.write_text(csv_row(mia.family, 0), encoding="utf-8")
    prepare = DefectsControl.prepare

    def legacy(self, batch_id, operation, prepared):
        prepared = dict(prepared)
        for name in ("read_scope", "before_files", "inspection_holds"):
            prepared.pop(name)
        prepare(self, batch_id, operation, prepared)

    with monkeypatch.context() as patch:
        patch.setattr(DefectsControl, "prepare", legacy)
        patch.setattr(batching, "_apply", lambda *_: (_ for _ in ()).throw(OSError("loss")))
        with pytest.raises(OSError):
            run(mia)
    source.write_text(csv_row(mia.family, 10), encoding="utf-8")
    result = run(mia)
    assert result["published_rows"] == result["resolved_groups"] == 2
    assert result["resolved_inspection_total"] == 192
    assert result["recovered_operations"]


def test_scoped_intention_records_file_manifest_and_all_context_rows(mia):
    source = mia.sources[0][1] / (mia.family + ".csv")
    source.write_text(csv_row(mia.family, 0), encoding="utf-8")
    run(mia)
    source.write_text(csv_row(mia.family, 10), encoding="utf-8")
    run(mia)
    prepared = json.loads(query(mia, "SELECT prepared_json FROM batches ORDER BY rowid DESC")[0][0])
    assert len(prepared["before_files"]) == 1
    assert prepared["before_files"][0][2] == 1
    table = DeltaTable(str(mia.table_path))
    verify_files(table, prepared, after=True)
    with pytest.raises(IntegrityError, match="manifest"):
        verify_files(table, prepared, after=False)
    with pytest.raises(IntegrityError, match="row count"):
        verify_files(table, {**prepared, "inserts": []}, after=True)
    altered = {**prepared, "before_files": [["missing.parquet", 1, 1]]}
    with pytest.raises(IntegrityError, match="replaced"):
        verify_files(table, altered, after=True)


@pytest.mark.parametrize(
    "scope",
    [
        [["", "2026-10-14T00:00:00", "2026-10-14T00:00:00"]],
        [["AV21", "2026-10-14T00:10:00", "2026-10-14T00:00:00"]],
        [["AV21", "2026-10-14T00:00:00+02:00", "2026-10-14T00:00:00+02:00"]],
        [["AV21", "2026-10-14T00:00:00", "2026-10-14T00:00:00"]] * 2,
    ],
)
def test_scope_rejects_invalid_intervals(scope):
    with pytest.raises(IntegrityError):
        expression(scope)


def test_gap_between_incoming_samples_does_not_materialize_unrelated_context(mia):
    source = mia.sources[0][1] / (mia.family + ".csv")
    source.write_text(csv_row(mia.family, 10), encoding="utf-8")
    run(mia)
    source.write_text(csv_row(mia.family, 0) + csv_row(mia.family, 20), encoding="utf-8")
    result = run(mia)
    assert result["context_rows"] == 2 and result["published_rows"] == 3
    assert result["resolved_inspection_total"] == 288


def test_resolved_review_restores_cached_base_without_rewriting_delta(mia):
    source = mia.sources[0][1] / (mia.family + ".csv")
    source.write_text(csv_row(mia.family, 0), encoding="utf-8")
    run(mia)
    source.write_text(csv_row(mia.family, 0, rejected=99), encoding="utf-8")
    assert run(mia)["unresolved_groups"] == 1
    with sqlite3.connect(mia.control_path) as db:
        db.execute("UPDATE issues SET status='RESOLVED'")
    result = run(mia)
    assert result["resolved_inspection_total"] == 96 and result["unresolved_groups"] == 0
    assert DeltaTable(str(mia.table_path)).version() == 0


def test_compact_rech_recovers_inspection_groups_from_the_verified_commit(config, monkeypatch):
    from ingesta_delta_lake.common import IngestaError
    from ingesta_delta_lake.rejections_storage import RejectionsControl

    config = replace(
        config, family="LISTA_RECH", control_format=3, timestamp_policy="local_naive_test_v1"
    )
    source = config.sources[0][1] / "LISTA_RECH.csv"
    source.write_text(csv_row(config.family, 0), encoding="utf-8")
    run(config)
    source.write_text(csv_row(config.family, 20), encoding="utf-8")

    def fail_confirmation(*args, **kwargs):
        raise OSError("simulated interruption after Delta commit")

    with monkeypatch.context() as patch:
        patch.setattr(RejectionsControl, "confirm", fail_confirmation)
        with pytest.raises(IngestaError, match="simulated interruption"):
            run(config)
    assert DeltaTable(str(config.table_path)).version() == 1
    control = RejectionsControl(config)
    try:
        plan = control.prepared(control.pending()[0])
        assert plan["inspection_groups_from_confirmed_rows"]
        assert "inspection_groups" not in plan and "before_files" not in plan
        assert plan["before_files_hash"]
    finally:
        control.close()

    result = run(config)
    assert result["recovered_operations"]
    assert result["resolved_inspection_total"] == 192
    assert result["published_rows"] == result["resolved_groups"] == 2
    assert DeltaTable(str(config.table_path)).version() == 1


def test_compact_rech_skips_identical_oversized_preparation(config, monkeypatch):
    from ingesta_delta_lake import compact_control, lista_rech

    config = replace(
        config, family="LISTA_RECH", control_format=3, timestamp_policy="local_naive_test_v1"
    )
    source = config.sources[0][1] / "LISTA_RECH.csv"
    source.write_text(csv_row(config.family, 0), encoding="utf-8")
    parse = lista_rech.parse
    attempts = []

    def count_parse(snapshots, encoding):
        attempts.append(len(snapshots))
        return parse(snapshots, encoding)

    monkeypatch.setattr(lista_rech, "parse", count_parse)
    with monkeypatch.context() as patch:
        patch.setattr(compact_control, "MAX_DOCUMENT", 20)
        with pytest.raises(compact_control.PreparedArtifactTooLarge, match="recovery budget"):
            run(config)
        assert attempts == [1]
        with pytest.raises(compact_control.PreparedArtifactTooLarge, match="unchanged input"):
            run(config)
        assert attempts == [1]
    source.write_text(csv_row(config.family, 10), encoding="utf-8")
    assert run(config)["resolved_inspection_total"] == 192
    assert attempts == [1, 2]



def test_original_recovery_budget_rejects_duplicated_preparation(tmp_path):
    from ingesta_delta_lake import compact_control

    # Exercise the real uncompressed budget with highly compressible input.
    # Sharing the test strings keeps the fixture small in memory.
    repeated = ["x" * (1024 * 1024)] * 129
    with pytest.raises(compact_control.PreparedArtifactTooLarge, match="recovery budget"):
        compact_control.publish(tmp_path, {"inserts": repeated, "inspection_groups": repeated})
    assert not list(tmp_path.iterdir())

    reference = compact_control.publish(
        tmp_path, {"inserts": repeated, "inspection_groups_from_confirmed_rows": True}
    )
    assert 128 * 1024 * 1024 < reference["bytes"] < compact_control.MAX_DOCUMENT
