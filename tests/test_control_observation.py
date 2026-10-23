import json
from contextlib import closing
from dataclasses import replace

from ingesta_delta_lake import retry_control, supervision_export
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.service import run


def test_retry_wait_keeps_last_failure_visible_to_existing_prometheus(config, source, monkeypatch):
    config = replace(config, control_format=3)
    monkeypatch.setenv("INGESTA_SUPERVISION_EXPORT", "1")
    monkeypatch.setattr(supervision_export, "INTERVAL", 0)
    run(config)
    with closing(Control(config)) as control:
        retry_control.failed(control, OSError("temporary"))
    assert run(config, runtime_owner=True)["status"] == "RETRY_WAIT"
    path = config.control_root / "supervision" / (config.control_path.name + ".json")
    value = json.loads(path.read_text())
    assert value["stats"]["last_attempt"]["status"] == "FAILED"
    assert value["stats"]["control_storage"]["retry_state"]["failures"] == 1
    run(config)
    assert json.loads(path.read_text())["stats"]["last_attempt"]["status"] != "FAILED"
