import json
from contextlib import closing

import pytest

from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.compact_control import clean, finalize, load, publish, summary
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.service import run


def enable(config):
    with closing(Control(config)) as control:
        with control.db:
            control.db.execute("INSERT INTO settings VALUES('control_format','3')")


def test_artifacts_are_checksummed_reusable_and_confined(tmp_path):
    root = tmp_path / "work"
    data = {"inserts": [{"value": "x" * 1000}] * 100}
    ref = publish(root, data)
    assert publish(root, data) == ref
    assert load(root, ref) == data
    assert len(list(root.iterdir())) == 1
    with pytest.raises(IntegrityError, match="identity"):
        load(root, {"$control_work": "../../secrets"})
    next(root.iterdir()).write_bytes(b"corrupt")
    with pytest.raises(IntegrityError):
        load(root, ref)


def test_small_receipt_has_no_business_rows():
    receipt = summary(
        {
            "inserts": [{"x": 1}] * 1000,
            "inspection_groups": [{}] * 500,
            "stats": {"rows_read": 1000},
            "unique_pairs": 1,
        }
    )
    assert receipt["insert_count"] == 1000 and receipt["unique_pairs"] == 1
    assert len(json.dumps(receipt)) < 300
    assert "inserts" not in receipt and "inspection_groups" not in receipt


def test_protocol_replay_and_finalization_keep_identity(config, source):
    first = run(config)
    with closing(Control(config)) as control:
        identity = control.control_id
    enable(config)
    source.write_text("754;3081/360;1;\n755;3082/360;1;\n", encoding="utf-8")
    result = run(config)
    assert result["status"] == "APPLIED"
    with closing(Control(config)) as control:
        assert control.control_id == identity
        document = control.prepared(control.batch(result["batch_id"]))
        assert document["$control_receipt"] == 3 and document["insert_count"] == 1
        assert not control.db.execute(
            "SELECT 1 FROM row_intents WHERE operation_id=?",
            (control.batch(result["batch_id"])["operation_id"],),
        ).fetchone()
        assert not list(control.work_root.glob("*.json.gz"))
    assert run(config)["status"] == "NO_CHANGE"
    assert result["delta_version"] == first["delta_version"] + 1


def test_pending_survives_restart_and_never_cleans_early(config, source):
    run(config)
    enable(config)
    data = {"inserts": [{"id_modelo_fenix": 9, "codigo_modelo_botella": "9/242"}]}
    with closing(Control(config)) as control:
        control.prepare("pending", "pending-operation", data)
        control.mark("pending", "UNKNOWN")
        assert not finalize(control, "pending")
        assert clean(control) == 0
    with closing(Control(config)) as control:
        assert control.prepared(control.pending()[0]) == data
        assert len(list(control.work_root.glob("*.json.gz"))) == 1


def test_shared_artifact_is_not_deleted_until_all_references_confirm(config, source):
    run(config)
    enable(config)
    with closing(Control(config)) as control:
        for name in ("one", "two"):
            control.prepare(name, name, {"inserts": []})
        control.confirm("one", 0)
        finalize(control, "one")
        assert clean(control) == 0
        assert control.prepared(control.batch("two")) == {"inserts": []}
        control.confirm("two", 0)
        finalize(control, "two")
        assert clean(control) == 1
