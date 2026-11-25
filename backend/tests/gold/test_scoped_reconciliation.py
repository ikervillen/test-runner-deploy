import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from app.gold import scoped_reconciliation
from app.gold.evidence import Evidence, save


def test_pinned_delta_without_cdf_resumes_after_completed_scopes(tmp_path, monkeypatch):
    from ingesta_delta_lake.contracts import CONTRACTS
    from ingesta_delta_lake.pipeline_rows import business_key, review_hash

    family = "LISTA_RECH"
    schema = CONTRACTS[family].schema
    base = datetime(2026, 11, 23)
    old = [
        dict(
            FECHA=base + timedelta(minutes=10 * slot),
            origen_fenix="AV21",
            MOLDE=1,
            LINEA=21,
            COD_MAQ_ZF=4,
            INSPECC=100,
            RECH=2,
            RECH_AUTO=1,
            CAVIDAD=str(cavity),
            FECHA_R=None,
        )
        for slot in range(24)
        for cavity in range(2)
    ]
    delta = tmp_path / "silver"
    original = pa.Table.from_pylist(old, schema=schema)
    write_deltalake(delta, original)
    table_id = DeltaTable(str(delta)).metadata().id
    payload = {
        "row_budget": 100,
        "fragment_lineage": 600,
        "silver_refs": {family: dict(table_id=table_id, version=0, rejected_hashes=[])},
        "lineages": {
            family: {
                business_key(family, row): [
                    dict(source_table=table_id, source_version=0, row_hash=review_hash(family, row))
                ]
                for row in old
            }
        },
    }
    previous = save(tmp_path / "inputs", payload, {family: original}, window_seconds=600)
    corrected = [dict(row, RECH=5) if i == 0 else row for i, row in enumerate(old) if i != 1]
    write_deltalake(delta, pa.Table.from_pylist(corrected, schema=schema), mode="overwrite")
    latest = dict(table_id=table_id, version=1, rejected_hashes=[])
    own = SimpleNamespace(
        table=lambda _: DeltaTable(str(delta), version=1),
        manifest=dict(publication_id="pinned-silver", tables={family: latest}),
    )
    checkpoint = tmp_path / "checkpoint"
    parameters = dict(
        origins=("AV21",),
        seconds=600,
        budget=8,
        batch_size=2,
        certified=lambda family, key, identity, sources: bool(sources),
    )
    write_checkpoint = scoped_reconciliation.atomic_json
    persisted = []

    def interrupted(path, value):
        write_checkpoint(path, value)
        persisted.append(value["examined"])
        if value["examined"] == 5:
            raise OSError("lost worker after checkpoint")

    with monkeypatch.context() as patch:
        patch.setattr(scoped_reconciliation, "atomic_json", interrupted)
        with pytest.raises(OSError, match="lost worker"):
            scoped_reconciliation.run(checkpoint, previous, own, family, schema, **parameters)
    assert persisted == [0, 1, 2, 3, 4, 5]
    assert Evidence.open(previous.root).publication_id == previous.publication_id
    assert json.loads(next(checkpoint.glob("*.json")).read_text())["examined"] == 5

    resumed = []

    def record(path, value):
        resumed.append(value["examined"])
        write_checkpoint(path, value)

    monkeypatch.setattr(scoped_reconciliation, "atomic_json", record)
    fact, lineage, state = scoped_reconciliation.run(
        checkpoint, Evidence.open(previous.root), own, family, schema, **parameters
    )
    assert resumed == list(range(6, 25))
    assert state["peak_rows"] <= 8 < fact["rows"] == 47
    saved = save(
        previous.root,
        {"row_budget": 100, "fragment_lineage": 600, "lineages": {}},
        {},
        window_seconds=600,
        fact_refs=[fact],
        lineage_refs=lineage,
    )
    assert saved.rows(family) == corrected
    assert len(saved.proofs(max_rows=100)[family]) == len(corrected)
    assert Evidence.open(previous.root, previous.publication_id).rows(family) == old
