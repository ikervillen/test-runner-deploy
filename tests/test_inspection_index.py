from contextlib import closing
from dataclasses import replace

import pytest
from deltalake import DeltaTable
from test_mia_batches import csv_row

from ingesta_delta_lake.defects_control import DefectsControl
from ingesta_delta_lake.defects_storage import DefectsStore
from ingesta_delta_lake.inspection_index import rebuild
from ingesta_delta_lake.rejections_storage import RejectionsControl, RejectionsStore
from ingesta_delta_lake.service import run


@pytest.mark.parametrize("family", ["LISTA_RECH", "LISTA_DEFECTOS"])
def test_index_is_minimal_and_rebuildable_without_writing_delta(config, family):
    config = replace(
        config, family=family, control_format=3, timestamp_policy="local_naive_test_v1"
    )
    path = config.input_root / "AV21" / (family + ".csv")
    path.write_text(csv_row(family, 0) + csv_row(family, 10), encoding="utf-8")
    result = run(config)
    klass, store_klass = (
        (RejectionsControl, RejectionsStore)
        if family == "LISTA_RECH"
        else (DefectsControl, DefectsStore)
    )
    with closing(klass(config)) as control:
        original = control.inspection_summary()
        assert (
            len(
                control.db.execute(
                    "SELECT payload_json FROM inspection_current LIMIT 1"
                ).fetchone()[0]
            )
            < 60
        )
        with control.db:
            control.db.execute("DELETE FROM inspection_current")
        proof = rebuild(control, store_klass(config, control.control_id))
        assert proof["rows"] == 2 and control.inspection_summary() == original
    assert DeltaTable(str(config.table_path)).version() == result["delta_version"]


def test_rebuild_keeps_pending_review_holds(config):
    config = replace(
        config, family="LISTA_RECH", control_format=3, timestamp_policy="local_naive_test_v1"
    )
    path = config.input_root / "AV21" / "LISTA_RECH.csv"
    path.write_text(csv_row(config.family, 0), encoding="utf-8")
    run(config)
    path.write_text(csv_row(config.family, 0, rejected=99), encoding="utf-8")
    run(config)
    with closing(RejectionsControl(config)) as control:
        original = control.inspection_summary()
        assert original["unresolved_groups"] == 1
        rebuild(control, RejectionsStore(config, control.control_id))
        assert control.inspection_summary() == original
