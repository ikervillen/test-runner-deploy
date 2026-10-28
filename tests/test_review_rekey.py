"""A manual replacement moves the observed key without retaining the old row."""

from contextlib import closing
from dataclasses import replace

import pytest
from deltalake import DeltaTable
from test_lista_defectos import line as defect_line
from test_lista_rech import line as rech_line
from test_review_execution import command

from ingesta_delta_lake.defects_control import DefectsControl
from ingesta_delta_lake.rejections_storage import RejectionsControl
from ingesta_delta_lake.review_adapters import KEEP, REPLACE, adapter, offer
from ingesta_delta_lake.reviews import apply_command
from ingesta_delta_lake.service import run


@pytest.mark.parametrize("family", ["LISTA_DEFECTOS", "LISTA_RECH"])
@pytest.mark.parametrize("crash", [False, True])
def test_inspection_count_key_change_is_atomic_and_recovers(config, family, crash, monkeypatch):
    config = replace(config, family=family, timestamp_policy="local_naive_test_v1")
    line = defect_line if family == "LISTA_DEFECTOS" else rech_line
    control_class = DefectsControl if family == "LISTA_DEFECTOS" else RejectionsControl
    path = config.input_root / "AV21" / f"{family}.csv"
    path.write_text(line(inspected=96), encoding="utf-8")
    run(config)
    path.write_text(line(inspected=97), encoding="utf-8")
    assert not run(config)["complete"]
    with closing(control_class(config)) as control:
        decision = command(config, control, REPLACE)
        if crash:
            from ingesta_delta_lake import reviews

            class CrashStore:
                def __init__(self, store):
                    self.store = store

                def __getattr__(self, name):
                    return getattr(self.store, name)

                def read(self, table=None):
                    if self.store.table().version() > 0:
                        raise OSError("Lost after key-changing MERGE")
                    return self.store.read(table)

            original = reviews.adapter

            def instrument(*args):
                item = original(*args)
                item.store = CrashStore(item.store)
                return item

            with monkeypatch.context() as patch:
                patch.setattr(reviews, "adapter", instrument)
                with pytest.raises(OSError):
                    apply_command(config, control, decision)
        receipt = apply_command(config, control, decision)
        assert receipt["before"]["INSPECC"] == 96 and receipt["after"]["INSPECC"] == 97
        assert apply_command(config, control, decision) == receipt
        data = adapter(config, control.control_id).store.read()[1]
        assert len(data) == 1 and next(iter(data.values()))["INSPECC"] == 97
    version = DeltaTable(str(config.table_path)).version()
    assert run(config)["complete"] and DeltaTable(str(config.table_path)).version() == version
    path.write_text(line(inspected=96), encoding="utf-8")
    assert run(config)["complete"] and DeltaTable(str(config.table_path)).version() == version


def test_group_corrections_can_be_applied_successively_without_inventing_a_denominator(config):
    config = replace(config, family="LISTA_DEFECTOS", timestamp_policy="local_naive_test_v1")
    path = config.input_root / "AV21" / "LISTA_DEFECTOS.csv"
    path.write_text(defect_line(defect=15) + defect_line(defect=16), encoding="utf-8")
    run(config)
    path.write_text(
        defect_line(defect=15, inspected=97) + defect_line(defect=16, inspected=97),
        encoding="utf-8",
    )
    run(config)
    with closing(DefectsControl(config)) as control:
        first = command(config, control, REPLACE)
        apply_command(config, control, first)
        assert control.inspection_summary()["unresolved_groups"] == 1
        second = command(config, control, REPLACE)
        apply_command(config, control, second)
        assert control.inspection_summary()["unresolved_groups"] == 0
    assert run(config)["complete"]


def test_multiple_accepted_defects_select_the_homologous_record(config):
    from ingesta_delta_lake.lista_defectos import FIELDS

    config = replace(config, family="LISTA_DEFECTOS", timestamp_policy="local_naive_test_v1")
    values = ["2026-10-14T00:00:00", 0, 1, 13, 15, 96, *([0] * 11), None]
    row = {"origen_fenix": "AV21", **dict(zip(FIELDS, values, strict=True))}
    result = offer(
        config,
        {
            "reason": "INSPECTION_GROUP_CONFLICT",
            "accepted": [row, {**row, "COD_DEF": 16}],
            "candidate": {**row, "INSPECC": 97},
        },
    )
    assert result["actions"] == [KEEP, REPLACE] and result["accepted"] == row
