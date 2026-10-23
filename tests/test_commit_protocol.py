from contextlib import closing

import pytest
from deltalake import DeltaTable

from ingesta_delta_lake.common import IngestaError
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.delta import DeltaStore
from ingesta_delta_lake.service import run


@pytest.mark.parametrize("point", ["before", "after", "ack"])
def test_lost_response_recovers_same_transaction_with_referenced_work(
    config, source, monkeypatch, point
):
    run(config)
    with closing(Control(config)) as control:
        identity = control.control_id
        with control.db:
            control.db.execute("INSERT INTO settings VALUES('control_format','3')")
    source.write_text("754;3081/360;1;\n755;3082/360;1;\n", encoding="utf-8")
    original = DeltaStore.insert

    def interrupted(self, *args):
        if point == "after":
            original(self, *args)
        raise OSError("simulated lost response")

    with monkeypatch.context() as patch:
        if point == "ack":
            patch.setattr(Control, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack")))
        else:
            patch.setattr(DeltaStore, "insert", interrupted)
        with pytest.raises(IngestaError):
            run(config)
    with closing(Control(config)) as control:
        pending = control.pending()
        assert len(pending) == 1 and pending[0]["state"] == "UNKNOWN"
        operation = pending[0]["operation_id"]
        assert len(control.prepared(pending[0])["inserts"]) == 1
    run(config)
    table = DeltaTable(str(config.table_path))
    assert table.version() == 1 and table.to_pyarrow_table().num_rows == 2
    assert table.transaction_version("fenix:" + identity) == 1
    assert len([h for h in table.history() if h["ingesta_operation_id"] == operation]) == 1
    with closing(Control(config)) as control:
        assert not control.pending()
        assert not list(control.work_root.glob("*.json.gz"))
