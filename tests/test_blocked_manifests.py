import json
from contextlib import closing
from dataclasses import replace

from ingesta_delta_lake.control import Control
from ingesta_delta_lake.maintenance import replace_control
from ingesta_delta_lake.service import run


def test_blocked_batch_retains_evidence_without_replicating_valid_rows(config, source):
    config = replace(config, control_format=3)
    run(config)
    source.write_text(
        "754;9999/111;1;\n" + "".join(f"{i};{i}/242;1;\n" for i in range(1000, 2000)),
        encoding="utf-8",
    )
    result = run(config)
    assert result["status"] == "BLOCKED"
    with closing(Control(config)) as control:
        batch = control.batch(result["batch_id"])
        document = json.loads(batch["prepared_json"])
        assert document["$control_blocked"] == 3 and "$control_receipt" not in document
        assert "inserts" not in document and len(batch["prepared_json"]) < 2000
        assert control.db.execute("SELECT count(*) FROM issues").fetchone()[0] > 0
        assert control.db.execute("SELECT count(*) FROM issue_evidence").fetchone()[0] > 0
        assert not batch["confirmed_at"]


def test_migration_keeps_blocked_state_and_archives_legacy_document(config, source):
    run(config)
    source.write_text("754;9999/111;1;\n", encoding="utf-8")
    result = run(config)
    replace_control(config, apply=True, reformulate=True)
    with closing(Control(config)) as control:
        batch = control.batch(result["batch_id"])
        assert batch["state"] == "BLOCKED" and batch["confirmed_at"] is None
        assert control.prepared(batch)["$control_blocked"] == 3
