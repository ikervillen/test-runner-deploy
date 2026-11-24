import pytest

from app.gold.common import IntegrityError, digest
from app.gold.evidence import Evidence
from app.gold.fragment_index import DEPTH, FragmentIndex
from app.gold.publications import independent_dependencies


def _leaves(index, size):
    import pyarrow as pa

    from app.gold.evidence import _write_table

    def part(slot):
        return {
            **_write_table(index.root, pa.table({"value": [slot]})),
            "partition": ["AV21", slot],
        }

    return index.updated(None, {("AV21", slot): part(slot) for slot in range(size)}), part


@pytest.mark.parametrize("size", [16, 160, 1600])
def test_one_edit_visits_one_path_at_every_scale(tmp_path, size):
    old, part = _leaves(FragmentIndex(tmp_path), size)
    index = FragmentIndex(tmp_path)
    assert index.find(old, ("AV21", 1)) == part(1)
    changed = index.updated(old, {("AV21", 1): None})
    assert changed["rows"] == size - 1
    assert index.pages_read == DEPTH + 1
    assert index.pages_written <= DEPTH + 1
    assert index.find(changed, ("AV21", 1)) is None
    assert index.find(old, ("AV21", 1)) is not None


def test_publication_retries_after_interruption_without_certifying_partial_tree(
    tmp_path, monkeypatch
):
    from app.gold import evidence_storage

    source, target = tmp_path / "inputs", tmp_path / "published"
    root, part = _leaves(FragmentIndex(source), 32)

    def snapshot(node):
        payload = {
            "schema_id": "quality-independent-input-v1",
            "lane": "live",
            "tables": [dict(name="CONT_ZC", parts_root=node, rows=node["rows"])],
        }
        return Evidence(source, digest(payload), payload)

    real_copy = evidence_storage.publish_reference
    calls = []

    def interrupted(source, target, ref):
        calls.append(ref["uri"])
        if len(calls) == 4:
            raise OSError("interrupted link")
        return real_copy(source, target, ref)

    monkeypatch.setattr(evidence_storage, "publish_reference", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        independent_dependencies(target, snapshot(root))
    assert not (target / "evidence" / "index" / "ack" / f"{root['hash']}.json").exists()

    copied = []

    def count(source, target, ref):
        copied.append(ref["uri"])
        return real_copy(source, target, ref)

    monkeypatch.setattr(evidence_storage, "publish_reference", count)
    independent_dependencies(target, snapshot(root))
    assert len([x for x in copied if x.endswith(".parquet")]) >= 30

    copied.clear()
    edited = FragmentIndex(source).updated(root, {("AV21", 32): part(32)})
    snap = snapshot(edited)
    assert snap.index.pages_read == 0
    independent_dependencies(target, snap)
    assert len([x for x in copied if x.endswith(".parquet")]) == 1
    assert len(copied) <= DEPTH + 2
    reloaded = Evidence.open(target / "evidence", snap.publication_id)
    assert reloaded.index.pages_read == 0
    assert reloaded.index.find(edited, ("AV21", 32)) == part(32)

    marker = target / "evidence" / "index" / "ack" / f"{edited['hash']}.json"
    marker.write_text("{}")
    with pytest.raises(IntegrityError, match="accreditation"):
        independent_dependencies(target, snap)
