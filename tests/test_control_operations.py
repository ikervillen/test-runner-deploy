"""Maintenance uses the active descriptor and always restores its writer."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deployment import operations
from deployment.manager import Docker, save


@pytest.fixture
def active_site(tmp_path, monkeypatch):
    site = {"state_root": str(tmp_path), "docker_context": "test", "drain_seconds": 30}
    record = {
        "channel": "develop",
        "commit": "tested-sha",
        "container": "active-develop",
        "volume": "persistent-develop",
        "image": "tested-digest",
        "manifest": {"control_payload_format": 2},
    }
    (tmp_path / "develop").mkdir()
    save(tmp_path / "develop" / "active.json", record)
    calls = []

    class Motor:
        failure = False

        def call(self, *args, **kwargs):
            assert args[:3] == ("exec", record["container"], "python")
            return json.dumps({"controls": [{"name": "control.sqlite", "bytes": 200}]})

        def helper(self, release, command, **kwargs):
            assert release == record
            calls.append((tuple(command), kwargs))
            if command[0] == "compact-control":
                if self.failure:
                    raise RuntimeError("verification failure")
                return {"status": "COMPACTED", "bytes_before": 200, "bytes_after": 50}
            return {"versions": {"SIP_MODELO": 0, "LISTA_AR": None}}

        def drain(self, release, timeout):
            assert release == record and timeout == 30
            calls.append(("drain", {}))

        def resume(self, release):
            assert release == record
            calls.append(("resume", {}))

    motor = Motor()
    monkeypatch.setattr(operations, "Docker", lambda _: motor)
    return SimpleNamespace(
        site=site, record=record, motor=motor, calls=calls, report=tmp_path / "report.json"
    )


def test_inspection_does_not_drain_or_write_controls(active_site):
    a = active_site
    result = operations.execute(a.site, "inspect", a.report)
    assert result["status"] == "INSPECTED" and not a.calls
    assert json.loads(a.report.read_text()) == result


@pytest.mark.parametrize("remove", [False, True])
def test_compaction_skips_missing_controls_and_resumes_same_image(active_site, remove):
    a = active_site
    result = operations.execute(a.site, "compact", a.report, remove_backup=remove)
    assert result["status"] == "COMPACTED"
    assert result["controls"][0]["bytes_after"] == 50
    commands = [c for c, _ in a.calls if isinstance(c, tuple) and c[0] == "compact-control"]
    expected = ("compact-control", "--family", "SIP_MODELO", "--apply")
    assert commands == [expected + (("--remove-backup",) if remove else ())]
    assert a.calls[0][0] == "drain" and a.calls[-1][0] == "resume"
    assert next(kw for cmd, kw in a.calls if cmd == commands[0]) == {
        "readonly": False,
        "timeout": 3600,
    }
    assert result["verification"]["versions"]["SIP_MODELO"] == 0


def test_failure_is_recorded_and_resumes_without_reset(active_site):
    a = active_site
    a.motor.failure = True
    with pytest.raises(RuntimeError, match="verification failure"):
        operations.execute(a.site, "compact", a.report)
    report = json.loads(a.report.read_text())
    assert report["status"] == "FAILED" and "after" in report
    assert a.calls[-1][0] == "resume"
    assert (
        json.loads((Path(a.site["state_root"]) / "develop" / "active.json").read_text()) == a.record
    )


def test_old_image_or_missing_active_is_rejected_before_drain(active_site):
    a = active_site
    a.record["manifest"] = {}
    path = Path(a.site["state_root"]) / "develop" / "active.json"
    save(path, a.record)
    with pytest.raises(RuntimeError, match="format-2"):
        operations.execute(a.site, "compact", a.report)
    path.unlink()
    with pytest.raises(RuntimeError, match="No active"):
        operations.execute(a.site, "inspect", a.report)
    assert not a.calls


def test_unconfirmed_helper_exit_cannot_launch_a_writer(active_site, monkeypatch):
    from deployment.manager import MaintenanceStillRunning

    a = active_site
    original = a.motor.helper

    def helper(release, command, **kwargs):
        if command[0] == "compact-control":
            raise MaintenanceStillRunning("helper termination unconfirmed")
        return original(release, command, **kwargs)

    monkeypatch.setattr(a.motor, "helper", helper)
    with pytest.raises(MaintenanceStillRunning):
        operations.execute(a.site, "compact", a.report)
    assert all(command != "resume" for command, _ in a.calls)
    report = json.loads(a.report.read_text())
    assert report["status"] == "FAILED" and "recovery_required" in report


def test_helper_forwards_maintenance_deadline_and_readwrite_mount():
    docker = Docker("test")
    record = {
        "channel": "develop",
        "instance": "a" * 32,
        "token": "b" * 32,
        "settings": {},
        "volume": "data",
        "image": "tested",
        "input_bind": "/input-data",
    }
    captured = []

    def call(*args, **kwargs):
        captured.append((args, kwargs))
        return '{"status":"COMPACTED"}'

    docker.call = call
    assert (
        docker.helper(record, ["compact-control"], readonly=False, timeout=3600)["status"]
        == "COMPACTED"
    )
    assert captured[0][1] == {"timeout": 3600}
    assert "type=volume,src=data,dst=/lake" in captured[0][0]
