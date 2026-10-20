import json
import threading
from dataclasses import asdict, replace

import portalocker
import pytest

from ingesta_delta_lake import workers
from ingesta_delta_lake.common import ConfigurationError, IngestaError
from ingesta_delta_lake.service import run


def test_process_roundtrip_and_real_independent_delta_writers(config, source):
    (config.input_root / "AV21/SIP_DEFECTO_STATUS.csv").write_text(
        "11;2;Muestreo IFA;0;MF;1;1;\n", encoding=config.encoding
    )
    reconstructed = workers.decode_config(json.dumps(asdict(config), default=str))
    assert reconstructed == config
    events, observed = [], []
    stop = config.control_root.parent / "runtime" / "test.stop"
    workers.run_phase(
        config,
        ("SIP_MODELO", "SIP_DEFECTO_STATUS"),
        threading.Event(),
        stop,
        lambda *event: events.append(event),
        observed.append,
    )
    assert {f for f, _, _ in events} == {"SIP_MODELO", "SIP_DEFECTO_STATUS"}
    assert all(r["status"] != "FAILED" for _, r, _ in events)
    assert any(len(active) == 2 for active in observed)
    assert config.table_path.joinpath("_delta_log").is_dir()
    assert replace(config, family="SIP_DEFECTO_STATUS").table_path.joinpath("_delta_log").is_dir()
    assert not list(stop.parent.glob("unit-*.json"))
    # Child processes release memory/handles, replay neither duplicates nor rewrites.
    assert run(config)["status"] == "NO_CHANGE"


def test_table_lock_excludes_same_family_but_allows_another(config, source):
    config.silver_root.mkdir()
    path = config.silver_root / f".writer-{config.plant}-{config.family}.lock"
    with portalocker.Lock(str(path), mode="a", timeout=0):
        with pytest.raises(IngestaError, match="otro escritor"):
            run(config, runtime_owner=True)
        other = replace(config, family="SIP_DEFECTO_STATUS")
        assert run(other, runtime_owner=True)["status"] != "FAILED"


def simulated(monkeypatch, stop, *, payload=None, exit_code=0):
    live, launches = [], []

    class Process:
        def __init__(self, command, env):
            cfg = json.loads(command[command.index("--config-json") + 1])
            self.family = cfg["family"]
            self.path = workers.Path(command[command.index("--result") + 1])
            self.pid, self.polls = len(launches) + 1, 0
            live.append(self)
            launches.append(self.family)
            assert len(live) <= 2
            assert sum(p.family in workers.HEAVY for p in live) <= 1
            assert env["RAYON_NUM_THREADS"] == "2"

        def poll(self):
            self.polls += 1
            if self.polls < 2:
                return None
            if self in live:
                live.remove(self)
                event = {"family": self.family, "result": {"status": "NO_CHANGE"}, "seconds": 1}
                value = event if payload is None else payload
                if value != "absent":
                    self.path.write_text(json.dumps(value), encoding="utf-8")
            return exit_code

        def wait(self):
            assert stop.exists()
            if self in live:
                live.remove(self)

    monkeypatch.setattr(workers.subprocess, "Popen", Process)
    monkeypatch.setattr(workers.time, "sleep", lambda *_: None)
    return launches, live


def test_scheduler_overlaps_light_work_but_never_two_heavy_tables(config, monkeypatch):
    stop = config.control_root.parent / "runtime/test.stop"
    launches, live = simulated(monkeypatch, stop)
    events = []
    workers.run_phase(
        config,
        ("LISTA_RECH", "LISTA_DEFECTOS", "CONT_ZC", "LISTA_AR"),
        threading.Event(),
        stop,
        lambda *e: events.append(e),
    )
    assert launches[:2] == ["LISTA_RECH", "CONT_ZC"]
    assert set(launches) == {"LISTA_RECH", "LISTA_DEFECTOS", "CONT_ZC", "LISTA_AR"}
    assert len(events) == 4 and not live


@pytest.mark.parametrize("payload", ["absent", [], {"family": "OTHER"}, {"result": []}])
def test_missing_or_corrupt_result_is_audited_as_failure(config, monkeypatch, payload):
    stop = config.control_root.parent / "runtime/test.stop"
    simulated(monkeypatch, stop, payload=payload)
    events = []
    workers.run_phase(config, ("SIP_MODELO",), threading.Event(), stop, lambda *e: events.append(e))
    assert events[0][1]["status"] == "FAILED"
    assert "valid durable result" in events[0][1]["error"]


def test_abnormal_exit_cannot_report_success(config, monkeypatch):
    stop = config.control_root.parent / "runtime/test.stop"
    simulated(monkeypatch, stop, exit_code=137)
    events = []
    workers.run_phase(config, ("SIP_MODELO",), threading.Event(), stop, lambda *e: events.append(e))
    assert events[0][1]["status"] == "FAILED" and events[0][1]["exit_code"] == 137


def test_stop_does_not_launch_new_units_and_waits_for_active_ones(config, monkeypatch):
    stop_path = config.control_root.parent / "runtime/test.stop"
    launches, live = simulated(monkeypatch, stop_path)
    stop = threading.Event()
    events = []
    workers.run_phase(
        config,
        ("SIP_MODELO", "SIP_DEFECTO", "SIP_DEFECTO_AR"),
        stop,
        stop_path,
        lambda *e: events.append(e),
        lambda active: stop.set() if len(active) == 2 else None,
    )
    assert launches == ["SIP_MODELO", "SIP_DEFECTO"]
    assert len(events) == 2 and not live and stop_path.exists()


def test_callback_failure_stops_children_without_forcing_them(config, monkeypatch):
    stop = config.control_root.parent / "runtime/test.stop"
    _, live = simulated(monkeypatch, stop)

    def fail(_):
        raise OSError("audit disk unavailable")

    with pytest.raises(OSError, match="audit disk"):
        workers.run_phase(config, ("SIP_MODELO",), threading.Event(), stop, lambda *_: None, fail)
    assert stop.exists() and not live


@pytest.mark.parametrize("limit,families", [(0, ("SIP_MODELO",)), (2, ("SIP_MODELO",) * 2)])
def test_invalid_process_budget_or_duplicate_table_rejected(config, limit, families):
    stop = config.control_root.parent / "runtime/test.stop"
    with pytest.raises(ConfigurationError):
        workers.run_phase(config, families, threading.Event(), stop, lambda *_: None, limit=limit)


def test_uncontrolled_stop_or_result_path_rejected(config, tmp_path):
    stop = tmp_path / "outside.stop"
    with pytest.raises(ConfigurationError, match="Stop path"):
        workers.run_phase(config, ("SIP_MODELO",), threading.Event(), stop, lambda *_: None)
    with pytest.raises(ConfigurationError, match="result/stop"):
        workers.execute(config, tmp_path / "outside.json", stop)


def test_child_exception_returns_durable_failure(config, monkeypatch):
    from ingesta_delta_lake import service

    def fail(*_, **__):
        raise OSError("synthetic failure")

    monkeypatch.setattr(service, "run", fail)
    root = config.control_root.parent / "runtime"
    root.mkdir()
    path = root / "result.json"
    assert workers.execute(config, path, root / "test.stop") == 3
    event = json.loads(path.read_text(encoding="utf-8"))
    assert event["result"]["error_type"] == "OSError"
    assert event["family"] == config.family
