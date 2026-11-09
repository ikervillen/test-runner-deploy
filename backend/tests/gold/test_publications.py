from pathlib import Path

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
