from pathlib import Path
from shutil import copytree
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import CommitProperties, DeltaTable, write_deltalake

from app.gold.common import IntegrityError, atomic_json, digest
from app.gold.publications import Publisher, Snapshot, envelope, promote, reference


def source(root: Path, version: int = 1) -> Snapshot:
    root.mkdir(parents=True, exist_ok=True)
    table = root / "silver" / "accepted"
    op = f"input-{version}"
    write_deltalake(
        table,
        pa.table({"id": [version]}),
        mode="overwrite",
        commit_properties=CommitProperties(custom_metadata={"accepted_operation": op}),
    )
    ref = reference(
        root,
        "accepted",
        "silver/accepted",
        DeltaTable(table).version(),
        op,
        operation_field="accepted_operation",
    )
    promote(
        root,
        [ref],
        environment="test",
        plant="AV",
        input_id=None,
        operation_id=op,
        generation=version,
        previous=None,
        coverage=[],
    )
    return Snapshot.open(root)


def coverage_row():
    return {
        "scope_id": "one",
        "id_produccion": None,
        "source": "MIA",
        "origin": "AV22",
        "period_start": None,
        "period_end": None,
        "estado": "READY",
        "reason_code": None,
        "evidence_reference": None,
        "last_successful_discovery": None,
        "confirmed_source_version": 0,
        "pending_files": 0,
        "earliest_pending_period": None,
    }


class ScopeSpill(dict):
    def __init__(self, rows):
        super().__init__(publication_scope_status=rows)
        self.spilled = {"publication_scope_status"}
        self.store = SimpleNamespace(iter_rows=lambda _name: iter(rows))


@pytest.mark.parametrize("scoped", [False, True])
def test_spilled_scope_status_reuses_confirmed_version_for_equal_rows(tmp_path, scoped):
    snap = source(tmp_path / "input")
    root = tmp_path / "gold"
    publisher = Publisher(root, environment="test", plant="AV")
    a = coverage_row()
    b = {**a, "scope_id": "two", "origin": "AV21"}
    changed = {**a, "estado": "REVIEW", "reason_code": "LATE_CORRECTION"}

    def write(rows, cutoff, *, partial=False):
        return publisher.publish(
            snap,
            ScopeSpill(rows),
            config_id="config",
            cutoff=cutoff,
            coverage=[],
            fact_bounds=() if partial else None,
        )

    first = write([a, b], "1")
    directory = root / "tables" / "publication_scope_status"
    original = sorted(directory.glob("*.parquet"))
    stats = first["publication_scope_status_write"]
    assert stats["commits"] == 1 and stats["added_parquet"] == len(original) > 0
    second = write([a] if scoped else [b, a], "2", partial=scoped)
    assert second["publication_id"] != first["publication_id"]
    assert second["publication_scope_status_write"] == {
        "delta_version": 0,
        "reused_version": True,
        "commits": 0,
        "added_parquet": 0,
        "added_bytes": 0,
        "added_allocated_bytes": 0,
    }
    assert sorted(directory.glob("*.parquet")) == original
    assert write([a] if scoped else [b, a], "2", partial=scoped)["status"] == "UNCHANGED"
    third = write([changed], "3", partial=scoped)
    assert third["publication_scope_status_write"]["commits"] == 1
    assert {r["scope_id"]: r for r in Snapshot.open(root).rows("publication_scope_status")} == (
        {"one": changed, "two": b} if scoped else {"one": changed}
    )
    assert Snapshot.open(root, publication_id=first["publication_id"]).rows(
        "publication_scope_status"
    ) == [a, b]


def test_full_scope_withdrawal_when_previous_patch_had_the_same_rows(tmp_path):
    snap = source(tmp_path / "input")
    root = tmp_path / "gold"
    publisher = Publisher(root, environment="test", plant="AV")
    a = coverage_row()
    b = {**a, "scope_id": "two"}
    publisher.publish(snap, ScopeSpill([a]), config_id="c", cutoff="1", coverage=[])
    publisher.publish(snap, ScopeSpill([b]), config_id="c", cutoff="2", coverage=[], fact_bounds=())
    publisher.publish(snap, ScopeSpill([b]), config_id="c", cutoff="3", coverage=[])
    assert Snapshot.open(root).rows("publication_scope_status") == [b]


@pytest.mark.parametrize("partial", [False, True])
def test_sparse_scope_update_keeps_unrelated_files_and_recovers(tmp_path, monkeypatch, partial):
    from app.gold import publications

    snap = source(tmp_path / "input")
    root = tmp_path / "candidate"
    legacy = tmp_path / "legacy"
    rows = [{**coverage_row(), "scope_id": f"scope-{i:04}"} for i in range(80)]

    def publish(path, values, cutoff, *, partial, failpoint=None):
        return Publisher(path, environment="test", plant="AV", failpoint=failpoint).publish(
            snap,
            ScopeSpill(values),
            config_id="config",
            cutoff=str(cutoff),
            coverage=[],
            fact_bounds=() if partial else None,
        )

    for i in range(8):
        initial = publish(root, rows[i * 10 : (i + 1) * 10], i, partial=True)
    table_name = "publication_scope_status"
    relative = f"tables/{table_name}"
    original = set(DeltaTable(root / relative).file_uris())
    assert len(original) == 8
    copytree(root, legacy)
    changed = {
        **rows[12],
        "estado": "WAITING_DEPENDENCY",
        "reason_code": "LATE_CORRECTION",
        "evidence_reference": "corrected",
    }
    expected = [*rows[:12], changed, *rows[13:]]
    incoming = expected[10:20] if partial else expected
    with monkeypatch.context() as patch:
        patch.setattr(publications, "_changed_scope_keys", lambda *a, **k: None)
        publish(legacy, incoming, 8, partial=partial)

    def crash(point):
        if point == f"commit:{table_name}":
            raise RuntimeError("commit before receipt")

    with pytest.raises(RuntimeError, match="commit before receipt"):
        publish(root, incoming, 8, partial=partial, failpoint=crash)
    assert Snapshot.open(root).publication_id == initial["publication_id"]
    committed = DeltaTable(root / relative)
    new_files = set(committed.file_uris())
    assert len(original - new_files) == 1
    assert all(Path(f).is_file() for f in original)
    metrics = committed.history(1)[0]["operationMetrics"]
    assert int(metrics["num_source_rows"]) == 1
    assert int(metrics["num_target_files_scanned"]) == 1

    result = publish(root, incoming, 8, partial=partial)
    assert result["publication_scope_status_write"]["commits"] == 1
    assert publish(root, incoming, 8, partial=partial)["status"] == "UNCHANGED"
    assert DeltaTable(root / relative).version() == committed.version()

    def sort_key(row):
        return row["scope_id"]

    assert sorted(Snapshot.open(root).rows(table_name), key=sort_key) == expected
    assert sorted(Snapshot.open(legacy).rows(table_name), key=sort_key) == expected
    old = Snapshot.open(root, publication_id=initial["publication_id"])
    assert sorted(old.rows(table_name), key=sort_key) == rows


@pytest.mark.parametrize(
    "stage", ["prepared", "commit:publication_scope_status", "verified", "promoted"]
)
def test_resume_each_failure_boundary_without_duplicate_commit(tmp_path, stage):
    snap = source(tmp_path / "input")
    output = tmp_path / "gold"

    def crash(point):
        if point == stage:
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError):
        Publisher(output, environment="test", plant="AV", failpoint=crash).publish(
            snap,
            {"publication_scope_status": [coverage_row()]},
            config_id="c",
            cutoff="cut",
            coverage=[],
        )
    publisher = Publisher(output, environment="test", plant="AV")
    publisher.publish(
        snap,
        {"publication_scope_status": [coverage_row()]},
        config_id="c",
        cutoff="cut",
        coverage=[],
    )
    result = Snapshot.open(output)
    assert result.rows("publication_scope_status")[0]["scope_id"] == "one"
    assert DeltaTable(output / "tables/publication_scope_status").version() == 0
    assert (
        publisher.publish(
            snap,
            {"publication_scope_status": [coverage_row()]},
            config_id="c",
            cutoff="cut",
            coverage=[],
        )["status"]
        == "UNCHANGED"
    )


def test_pinned_input_remains_old_after_source_advances(tmp_path):
    root = tmp_path / "input"
    old = source(root)
    source(root, 2)
    assert old.rows("accepted") == [{"id": 1}]
    assert Snapshot.open(root).rows("accepted") == [{"id": 2}]


def test_tampered_manifest_rejected(tmp_path):
    source(tmp_path)
    pointer, manifest = envelope(tmp_path)
    manifest["plant"] = "other"
    atomic_json(tmp_path / pointer["manifest_uri"], manifest)
    with pytest.raises(IntegrityError, match="hash"):
        Snapshot.open(tmp_path)


def test_unconfirmed_commit_rejected(tmp_path):
    source(tmp_path)
    pointer, manifest = envelope(tmp_path)
    manifest["tables"][0]["operation_id"] = "not-accepted"
    manifest.pop("manifest_hash")
    manifest["manifest_hash"] = digest(manifest)
    atomic_json(tmp_path / pointer["manifest_uri"], manifest)
    pointer["manifest_hash"] = manifest["manifest_hash"]
    atomic_json(tmp_path / "current.json", pointer)
    with pytest.raises(IntegrityError, match="operation"):
        Snapshot.open(tmp_path)


def test_missing_delta_data_rejected(tmp_path):
    snap = source(tmp_path)
    Path(snap.table("accepted").file_uris()[0]).unlink()
    with pytest.raises(IntegrityError, match="missing"):
        Snapshot.open(tmp_path)


def test_incompatible_dependency_versions_rejected(tmp_path):
    snap = source(tmp_path)
    promote(
        tmp_path,
        snap.manifest["tables"],
        environment="test",
        plant="AV",
        input_id=None,
        operation_id="bad-deps",
        generation=2,
        previous="input-1",
        coverage=[],
        dependencies=[{"input_versions": {"accepted": 99}}],
    )
    with pytest.raises(IntegrityError, match="incompatible"):
        Snapshot.open(tmp_path)


def test_scoped_replacement_and_empty_withdrawal_preserve_other_origins(tmp_path):
    snap = source(tmp_path / "input")
    publisher = Publisher(tmp_path / "gold", environment="test", plant="AV")
    a = coverage_row()
    b = {**a, "scope_id": "two", "origin": "AV23"}
    publisher.publish(
        snap, {"publication_scope_status": [a, b]}, config_id="c", cutoff="1", coverage=[]
    )
    changed = {**a, "estado": "REVIEW"}
    publisher.publish(
        snap,
        {"publication_scope_status": [changed]},
        config_id="c",
        cutoff="2",
        coverage=[],
        replacement_scopes={"publication_scope_status": {"origin": ["AV22"]}},
    )
    rows = Snapshot.open(tmp_path / "gold").rows("publication_scope_status")
    assert {r["scope_id"]: r["estado"] for r in rows} == {"one": "REVIEW", "two": "READY"}
    publisher.publish(
        snap,
        {"publication_scope_status": []},
        config_id="c",
        cutoff="3",
        coverage=[],
        replacement_scopes={"publication_scope_status": {"origin": ["AV22"]}},
    )
    assert Snapshot.open(tmp_path / "gold").rows("publication_scope_status") == [b]


def test_partial_update_keeps_previous_publication_readable(tmp_path):
    snap = source(tmp_path / "input")
    root = tmp_path / "gold"
    first = Publisher(root, environment="test", plant="AV")
    first.publish(
        snap, {"publication_scope_status": [coverage_row()]}, config_id="c", cutoff="1", coverage=[]
    )
    pinned = Snapshot.open(root)

    def crash(point):
        if point == "commit:publication_scope_status":
            raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError):
        Publisher(root, environment="test", plant="AV", failpoint=crash).publish(
            snap,
            {"publication_scope_status": [{**coverage_row(), "estado": "REVIEW"}]},
            config_id="c",
            cutoff="2",
            coverage=[],
        )
    assert Snapshot.open(root).publication_id == pinned.publication_id
    assert pinned.rows("publication_scope_status")[0]["estado"] == "READY"
    first.publish(
        snap,
        {"publication_scope_status": [{**coverage_row(), "estado": "REVIEW"}]},
        config_id="c",
        cutoff="2",
        coverage=[],
    )
    assert Snapshot.open(root).rows("publication_scope_status")[0]["estado"] == "REVIEW"


def test_replaying_a_superseded_operation_cannot_roll_the_pointer_back(tmp_path):
    snap = source(tmp_path / "input")
    root = tmp_path / "gold"
    publisher = Publisher(root, environment="test", plant="AV")
    first = {"publication_scope_status": [coverage_row()]}
    publisher.publish(snap, first, config_id="c", cutoff="1", coverage=[])
    publisher.publish(
        snap,
        {"publication_scope_status": [{**coverage_row(), "estado": "REVIEW"}]},
        config_id="c",
        cutoff="2",
        coverage=[],
    )
    latest = Snapshot.open(root).publication_id
    with pytest.raises(IntegrityError, match="superseded"):
        publisher.publish(snap, first, config_id="c", cutoff="1", coverage=[])
    assert Snapshot.open(root).publication_id == latest


def test_restored_publication_is_independent_of_original_directory(tmp_path):
    import shutil

    snap = source(tmp_path / "input")
    root = tmp_path / "gold"
    Publisher(root, environment="test", plant="AV").publish(
        snap, {"publication_scope_status": [coverage_row()]}, config_id="c", cutoff="1", coverage=[]
    )
    restored = tmp_path / "restored"
    shutil.copytree(root, restored, ignore=shutil.ignore_patterns("*.lock"))
    assert Snapshot.open(restored).rows("publication_scope_status") == [coverage_row()]


def test_existing_unowned_table_is_not_adopted_or_overwritten(tmp_path):
    from app.gold.schemas import make_table

    snap = source(tmp_path / "input")
    root = tmp_path / "gold"
    table = root / "tables/publication_scope_status"
    write_deltalake(table, make_table("publication_scope_status", [coverage_row()]))
    with pytest.raises(IntegrityError, match="published owner"):
        Publisher(root, environment="test", plant="AV").publish(
            snap, {"publication_scope_status": []}, config_id="c", cutoff="cut", coverage=[]
        )
    assert DeltaTable(table).version() == 0
    assert DeltaTable(table).to_pyarrow_dataset().count_rows() == 1


def test_all_replacement_scopes_are_validated_before_any_table_write(tmp_path):
    snap = source(tmp_path / "input")
    root = tmp_path / "gold"
    with pytest.raises(IntegrityError, match="known columns"):
        Publisher(root, environment="test", plant="AV").publish(
            snap,
            {"dim_plant": [], "publication_scope_status": [coverage_row()]},
            config_id="c",
            cutoff="1",
            coverage=[],
            replacement_scopes={"publication_scope_status": {"unknown": ["x"]}},
        )
    assert not (root / "tables").exists()


def test_parent_only_update_cannot_claim_compatible_derived_data(tmp_path):
    snap = source(tmp_path / "input")
    root = tmp_path / "gold"
    publisher = Publisher(root, environment="test", plant="AV")
    publisher.publish(
        snap,
        {"productions": [], "production_assignments": []},
        config_id="c",
        cutoff="1",
        coverage=[],
    )
    pinned = Snapshot.open(root)
    with pytest.raises(IntegrityError, match="Dependent tables"):
        publisher.publish(snap, {"productions": []}, config_id="c", cutoff="2", coverage=[])
    assert Snapshot.open(root).publication_id == pinned.publication_id


def test_gold_reader_requires_complete_current_contract(tmp_path):
    snap = source(tmp_path / "input")
    with pytest.raises(IntegrityError, match="contract"):
        Snapshot.open(snap.root, require_gold_contract=True)
    root = tmp_path / "gold"
    Publisher(root, environment="test", plant="AV").publish(
        snap, {"publication_scope_status": []}, config_id="c", cutoff="1", coverage=[]
    )
    with pytest.raises(IntegrityError, match="every declared table"):
        Snapshot.open(root, require_gold_contract=True)
