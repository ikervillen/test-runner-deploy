import subprocess

import pytest

from deployment.manager import Docker, MaintenanceStillRunning
from ingesta_delta_lake import maintenance
from ingesta_delta_lake.service import run


def test_source_check_is_linear_but_rebuilt_indexes_are_fully_checked(config, source, monkeypatch):
    run(config)
    statements = []
    original = maintenance.sqlite3.connect

    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(maintenance.sqlite3, "connect", connect)
    report = maintenance.replace_control(config, apply=True, remove_backup=True)
    assert statements.count("PRAGMA quick_check") == 1
    assert statements.count("PRAGMA integrity_check") == 2
    assert statements.count("PRAGMA cache_size=-131072") == 4
    assert "logical-hashes" in report["validation"]


def helper_record():
    return {
        "channel": "develop",
        "instance": "a" * 32,
        "token": "b" * 32,
        "settings": {},
        "volume": "data",
        "image": "tested",
        "input_bind": "/inputs",
    }


def test_copy_timeout_stops_only_its_named_helper_before_returning(monkeypatch):
    docker = Docker("test")
    events = []

    def call(*args, **kwargs):
        events.append((args, kwargs))
        if args[0] == "run":
            assert "--init" in args and "--no-healthcheck" in args
            name = args[args.index("--name") + 1]
            assert name.startswith("fenix-control-")
            raise subprocess.TimeoutExpired("isolated copy", 1)
        return ""

    monkeypatch.setattr(docker, "call", call)
    monkeypatch.setattr(docker, "running", lambda _: False)
    with pytest.raises(subprocess.TimeoutExpired):
        docker.helper(helper_record(), ["compact-control"], readonly=False, timeout=1)
    name = events[0][0][events[0][0].index("--name") + 1]
    assert events[1][0] == ("kill", "--signal=SIGTERM", name)
    assert events[2][0] == ("wait", name)


def test_uncertain_helper_termination_is_an_explicit_recovery_boundary(monkeypatch):
    docker = Docker("test")

    def call(*args, **kwargs):
        if args[0] == "run":
            raise subprocess.TimeoutExpired("copy", 1)
        return ""

    monkeypatch.setattr(docker, "call", call)
    monkeypatch.setattr(docker, "running", lambda _: True)
    with pytest.raises(MaintenanceStillRunning, match="Confirm helper termination"):
        docker.helper(helper_record(), ["compact-control"], readonly=False, timeout=1)


def test_optional_call_timeout_does_not_abort_the_caller(monkeypatch):
    docker = Docker("desktop-linux")

    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout", 15))

    monkeypatch.setattr("deployment.manager.subprocess.run", run)
    assert docker.call("exec", "worker", "health", optional=True, timeout=15) is None
    with pytest.raises(subprocess.TimeoutExpired):
        docker.call("exec", "worker", "health", timeout=15)


def test_non_maintenance_timeout_does_not_signal_any_container(monkeypatch):
    docker = Docker("test")
    commands = []

    def call(*args, **kwargs):
        commands.append(args[0])
        raise subprocess.TimeoutExpired("check", 1)

    monkeypatch.setattr(docker, "call", call)
    with pytest.raises(subprocess.TimeoutExpired):
        docker.helper(helper_record(), ["check"])
    assert commands == ["run"]
