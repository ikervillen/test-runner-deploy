import json

import pytest
from test_maintenance_boundaries import helper_record

from deployment.manager import Docker
from ingesta_delta_lake.maintenance import replace_control
from ingesta_delta_lake.service import run


def test_trial_helper_mounts_industrial_volume_readonly_and_no_review_output():
    record = helper_record()
    record["review_volumes"] = {"commands": "commands", "results": "results"}
    motor = Docker("test")
    calls = []
    motor.call = lambda *args, **kwargs: calls.append(args) or '{"status":"TRIAL_VERIFIED"}'
    motor.helper(record, ["trial-control"], trial_volume="data-trial-" + "a" * 12)
    arguments = calls[0]
    assert "type=volume,src=data,dst=/lake,readonly" in arguments
    assert "type=volume,src=data-trial-" + "a" * 12 + ",dst=/trial" in arguments
    assert not any("/reviews/" in value for value in arguments)
    with pytest.raises(ValueError):
        motor.helper(
            record, ["trial-control"], readonly=False, trial_volume="data-trial-" + "a" * 12
        )
    with pytest.raises(ValueError):
        motor.helper(record, ["trial-control"], trial_volume="data")


def test_trial_volume_initialization_has_no_industrial_write_mount():
    motor = Docker("test")
    events = []
    motor.call = lambda *args, **kwargs: events.append(args) or ""
    volume = motor.create_control_trial(helper_record())
    assert volume.startswith("data-trial-")
    assert events[0][:2] == ("volume", "create")
    assert all("dst=/lake" not in value for value in events[1])


def test_format3_migration_is_repeatable_and_backup_release_is_verified(config, source):
    run(config)
    first = replace_control(config, apply=True, reformulate=True)
    assert not first["backup_removed"]
    second = replace_control(config, apply=True, reformulate=True)
    assert second["status"] == "ALREADY_FORMAT_3"
    third = replace_control(config, apply=True, reformulate=True, remove_backup=True)
    assert third["backup_removed"]
    assert not list(config.control_root.glob("*.before-compaction"))
    assert run(config)["status"] == "NO_CHANGE"


def test_unverified_backup_cannot_be_released(config, source):
    from ingesta_delta_lake.common import IntegrityError

    run(config)
    replace_control(config, apply=True, reformulate=True)
    proof = config.control_path.with_name(config.control_path.name + ".compaction-report.json")
    data = json.loads(proof.read_text())
    data["control_id"] = "different"
    proof.write_text(json.dumps(data))
    with pytest.raises(IntegrityError, match="verified migration"):
        replace_control(config, apply=True, reformulate=True, remove_backup=True)
