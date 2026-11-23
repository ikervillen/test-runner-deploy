from datetime import datetime
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from app.gold.evidence import Evidence, save
from app.gold.evidence_storage import consolidate, publish_reference
from app.gold.silver_incremental import FullReadRequired, materialize


@pytest.fixture(autouse=True)
def native():
    pytest.importorskip("ingesta_delta_lake")


@pytest.mark.parametrize("family", ["CONT_ZC", "LISTA_AR"])
def test_window_prunes_verified_fragments_before_opening_parquet(tmp_path, monkeypatch, family):
    from app.gold import evidence

    def stamp(hour, minute=0):
        return datetime(2026, 11, 20, hour, minute)

    if family == "LISTA_AR":
        values = {
            "FECHA_ON": [stamp(5), stamp(6), stamp(7), stamp(8), stamp(10)],
            "FECHA_OFF": [stamp(5, 5), None, stamp(8), stamp(8, 5), None],
        }
        selected = [1, 3]
    else:
        values = {"FECHA": [stamp(5), stamp(8), stamp(8, 5), stamp(8, 10), stamp(10)]}
        selected = [2, 3]
    table = pa.table({**values, "origen_fenix": ["AV21"] * 5, "value": list(range(5))})
    root = tmp_path / "inputs"
    item = save(root, {}, {family: table}, window_seconds=600)
    window = (stamp(8), stamp(8, 10))
    expression = evidence.operational_expression(family, window)
    expected = item.rows(family, max_rows=10, expression=expression)
    assert [row["value"] for row in expected] == selected
    opens = []
    verified = evidence.checked_file

    def record(path, ref):
        opens.append(ref["uri"])
        return verified(path, ref)

    monkeypatch.setattr(evidence, "checked_file", record)
    fresh = Evidence.open(root)
    assert fresh.rows(family, max_rows=10, expression=expression, window=window) == expected
    assert len(opens) == 2
    assert fresh.rows(family, max_rows=10, expression=expression, window=window) == expected
    assert len(opens) == 2  # The same immutable fragments were already verified.
    assert fresh.read_stats[family]["fragments_opened"] == 2
    assert fresh.read_stats[family]["fragments_reused"] == 2


def test_fragments_reuse_unchanged_block_and_publish_verified_links(tmp_path):
    root = tmp_path / "inputs"
    target = tmp_path / "published" / "evidence"
    table = pa.table(
        {
            "origen_fenix": ["AV21", "AV21"],
            "FECHA": [datetime(2026, 11, 20, 8), datetime(2026, 11, 20, 8, 10)],
            "value": [10, 20],
        }
    )
    legacy = save(root, {}, {"CONT_ZC": table})
    assert legacy.table("CONT_ZC", max_rows=2).equals(table)
    original = save(root, {}, {"CONT_ZC": table}, window_seconds=600)
    assert original.table("CONT_ZC", max_rows=2).equals(table)
    changed = table.set_column(2, "value", pa.array([10, 30]))
    latest = save(root, {}, {"CONT_ZC": changed}, window_seconds=600, reuse=original)
    old, new = (item.document["tables"][0]["parts"] for item in (original, latest))
    assert old[0] == new[0]
    assert old[1]["hash"] != new[1]["hash"]
    assert Evidence.open(root).table("CONT_ZC", max_rows=2).equals(changed)

    publish_reference(root, target, new[0])
    assert (root / new[0]["uri"]).samefile(target / new[0]["uri"])
    copy = target / old[1]["uri"]
    initial_bytes = (root / old[1]["uri"]).read_bytes()
    copy.write_bytes(initial_bytes)
    assert consolidate(tmp_path)[0] == 1
    assert not copy.samefile(root / old[1]["uri"])
    assert consolidate(tmp_path, apply=True)[0] == 1
    assert copy.samefile(root / old[1]["uri"])
    assert copy.read_bytes() == initial_bytes
    assert consolidate(tmp_path) == (0, 0)


def test_silver_cdf_applies_correction_and_new_row(tmp_path):
    from ingesta_delta_lake.contracts import CONTRACTS
    from ingesta_delta_lake.pipeline_rows import business_key

    family = "LISTA_RECH"
    schema = CONTRACTS[family].schema
    first = dict(
        origen_fenix="AV21",
        FECHA=datetime(2026, 11, 20, 8),
        MOLDE=3,
        LINEA=21,
        COD_MAQ_ZF=4,
        INSPECC=100,
        RECH=6,
        RECH_AUTO=2,
        CAVIDAD="1",
        FECHA_R=None,
    )
    later = {**first, "FECHA": datetime(2026, 11, 20, 8, 10)}
    path = tmp_path / "silver"
    source = pa.Table.from_pylist([first], schema=schema)
    write_deltalake(path, source, configuration={"delta.enableChangeDataFeed": "true"})
    table = DeltaTable(str(path))

    def ref():
        return {
            "table_id": table.metadata().id,
            "version": table.version(),
            "rejected_hashes": [],
        }

    previous = save(tmp_path / "evidence", {"silver_refs": {family: ref()}}, {family: source})
    table.update(updates={"RECH": "7"})
    write_deltalake(path, pa.Table.from_pylist([later], schema=schema), mode="append")
    table.update_incremental()
    snapshot = SimpleNamespace(table=lambda name: table if name == family else None)
    rows, proofs, scanned = materialize(
        snapshot, previous, {}, family, ref(), schema.names, budget=10
    )
    assert rows == [{**first, "RECH": 7}, later]
    assert scanned == 3
    assert proofs[business_key(family, first)][0]["source_version"] == 1
    assert proofs[business_key(family, later)][0]["source_version"] == 2

    table.delete()
    with pytest.raises(FullReadRequired):
        materialize(snapshot, previous, {}, family, ref(), schema.names, budget=10)


def test_large_manifest_reopens_and_republishes_without_moving_an_invalid_pointer(
    tmp_path, monkeypatch
):
    from app.gold import evidence
    from app.gold.common import IntegrityError, read_json
    from app.gold.publications import independent_dependencies

    root = tmp_path / "inputs"
    published = tmp_path / "published"
    document = {"lane": "live", "metadata": "x" * 4_001_060}
    snapshot = save(root, document, {})
    path = root / f"{snapshot.publication_id}.json"

    with pytest.raises(IntegrityError, match="JSON metadata budget exceeded"):
        read_json(path)
    assert Evidence.open(root).publication_id == snapshot.publication_id
    assert save(root, document, {}).publication_id == snapshot.publication_id
    for _ in range(2):
        dependencies = independent_dependencies(published, snapshot)
        assert dependencies[0]["evidence_id"] == snapshot.publication_id

    monkeypatch.setattr(evidence, "MAX_MANIFEST_BYTES", path.stat().st_size)
    with pytest.raises(IntegrityError, match="Gold input manifest exceeds"):
        save(root, {**document, "extra": True}, {})
    assert Evidence.open(root).publication_id == snapshot.publication_id


def test_cdf_reuses_fact_and_lineage_partitions_after_a_late_move(tmp_path, monkeypatch):
    from datetime import timedelta

    from ingesta_delta_lake.contracts import CONTRACTS
    from ingesta_delta_lake.pipeline_rows import business_key, review_hash

    from app.gold import evidence, fragment_delta

    family = "LISTA_RECH"
    schema = CONTRACTS[family].schema
    start = datetime(2026, 11, 20, 8)
    rows = [
        dict(
            origen_fenix="AV21",
            FECHA=start + timedelta(minutes=i * 10),
            MOLDE=3,
            LINEA=21,
            COD_MAQ_ZF=4,
            INSPECC=100,
            RECH=6,
            RECH_AUTO=2,
            CAVIDAD=str(i),
            FECHA_R=None,
        )
        for i in range(32)
    ]
    source = pa.Table.from_pylist(rows, schema=schema)
    path = tmp_path / "silver"
    root = tmp_path / "evidence"
    write_deltalake(path, source, configuration={"delta.enableChangeDataFeed": "true"})
    table = DeltaTable(str(path))
    table_id = table.metadata().id

    def ref(version):
        return {"table_id": table_id, "version": version, "rejected_hashes": []}

    def document(version):
        return {"fragment_lineage": 600, "silver_refs": {family: ref(version)}}

    previous = save(
        root,
        {
            **document(0),
            "lineages": {
                family: {
                    business_key(family, row): [
                        {
                            "source_table": table_id,
                            "source_version": 0,
                            "row_hash": review_hash(family, row),
                        }
                    ]
                    for row in rows
                }
            },
        },
        {family: source},
        window_seconds=600,
    )
    table.update(
        predicate="CAVIDAD = '1'",
        updates={
            "FECHA": "CAST('2026-11-20 07:40:00' AS TIMESTAMP)",
            "origen_fenix": "'AV22'",
            "RECH": "7",
        },
    )
    added = {**rows[-1], "FECHA": start + timedelta(hours=6), "CAVIDAD": "new"}
    write_deltalake(path, pa.Table.from_pylist([added], schema=schema), mode="append")
    silver = DeltaTable(str(path))
    snapshot = SimpleNamespace(table=lambda name: silver)
    legacy = Evidence.open(root)
    expected_rows, expected_proofs, expected_count = materialize(
        snapshot, legacy, legacy.proofs(max_rows=100), family, ref(2), schema.names, budget=100
    )
    candidate = Evidence.open(root)
    fact, lineage, count = fragment_delta.update(
        snapshot, candidate, family, ref(2), schema, budget=100, seconds=600
    )
    assert count == expected_count == 3
    assert candidate.read_stats[family]["rows_decoded"] == 1
    assert candidate.read_stats["__lineage"]["rows_decoded"] == 1
    assert legacy.read_stats[family]["rows_decoded"] == 32
    assert legacy.read_stats["__lineage"]["rows_decoded"] == 32

    def persist():
        return save(
            root,
            {**document(2), "lineages": {}},
            {},
            window_seconds=600,
            fact_refs=[fact],
            lineage_refs=lineage,
        )

    original_pointer = (root / "current.json").read_bytes()
    atomic = evidence.atomic_json

    def crash(path, value):
        if path.name == "current.json":
            raise OSError("simulated interruption before pointer")
        atomic(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(evidence, "atomic_json", crash)
        with pytest.raises(OSError, match="before pointer"):
            persist()
    assert (root / "current.json").read_bytes() == original_pointer
    result = persist()
    assert persist().publication_id == result.publication_id
    assert {business_key(family, row): row for row in result.rows(family, max_rows=100)} == {
        business_key(family, row): row for row in expected_rows
    }
    assert result.proofs(max_rows=100)[family] == expected_proofs
    assert previous.rows(family, max_rows=100) == rows
    assert (
        len(
            set(p["uri"] for p in fact["parts"])
            & set(p["uri"] for p in previous.document["tables"][0]["parts"])
        )
        == 31
    )

    silver.delete(predicate="CAVIDAD = 'new'")
    with pytest.raises(FullReadRequired):
        fragment_delta.update(snapshot, result, family, ref(3), schema, budget=100, seconds=600)
    assert Evidence.open(root).publication_id == result.publication_id
