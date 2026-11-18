import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deployment.independent import ContainerPipelines, render
from deployment.independent_permissions import initialize


@pytest.fixture
def stack():
    site = json.loads(Path("deployment/independent.example.json").read_text())
    plan = ContainerPipelines(json.loads(Path("deployment/pipelines.example.json").read_text()))
    return site, plan


def test_render_has_no_shared_writers_and_no_private_control_mounts_in_gold(stack):
    site, plan = stack
    result = render(site, plan)
    assert set(result["services"]) == {
        "initialize",
        "ingesta-live",
        "ingesta-history",
        "gold-live",
        "gold-history",
        "backend",
    }
    for lane in ("live", "history"):
        other = "history" if lane == "live" else "live"
        ingesta = result["services"][f"ingesta-{lane}"]
        assert all(
            v.get("read_only")
            for v in ingesta["volumes"]
            if v.get("source", "").startswith(other + "-")
        )
        gold = result["services"][f"gold-{lane}"]
        assert [v["source"] for v in gold["volumes"] if not v.get("read_only")] == [f"{lane}-gold"]
        assert not any(
            v.get("source", "").endswith(("-control", "-checkpoint")) for v in gold["volumes"]
        )
        assert not any(v.get("target") == site["input_target"] for v in gold["volumes"])
    assert all(v["read_only"] for v in result["services"]["backend"]["volumes"])
    assert "fenix-aiala-develop" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["shared-volume", "old-volume", "shared-cpu", "floating-image"])
def test_refuses_unsafe_or_incomplete_stack_configuration(stack, failure):
    site, plan = stack
    if failure == "shared-volume":
        site["volumes"]["history"]["silver"] = site["volumes"]["live"]["silver"]
    elif failure == "old-volume":
        site["volumes"]["live"]["silver"] = "fenix-aiala-develop"
    elif failure == "shared-cpu":
        site["resources"]["gold-history"]["cpuset"] = "0"
    else:
        site["image"] = "repository:latest"
    with pytest.raises(ValueError):
        render(site, plan)


def test_render_is_host_independent_and_rejects_nested_container_paths(stack):
    _site, plan = stack
    assert str(plan.live.silver) == "/pipelines/live/silver"
    value = json.loads(Path("deployment/pipelines.example.json").read_text())
    value["history"]["silver"] = value["live"]["silver"] + "/history"
    with pytest.raises(ValueError, match="independent"):
        ContainerPipelines(value)


def test_permission_initialization_changes_only_an_empty_explicit_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("os.chown", lambda p, u, g: calls.append((p, u, g)), raising=False)
    initialize([str(tmp_path.resolve())], uid=10001, gid=10001)
    assert calls == [(tmp_path.resolve(), 10001, 10001)]
    (tmp_path / "existing").write_text("do not adopt")
    monkeypatch.setattr(Path, "stat", lambda *_a, **_k: SimpleNamespace(st_uid=0, st_gid=0))
    with pytest.raises(ValueError, match="adopt"):
        initialize([str(tmp_path.resolve())], uid=10001, gid=10001)
    assert len(calls) == 1


def test_preflight_checks_real_docker_host_capacity_without_starting_services(stack):
    from deployment.independent import validate_host

    site, _plan = stack
    validate_host(site, {"NCPU": 8, "MemTotal": 16 * 1024**3})
    with pytest.raises(ValueError, match="CPU"):
        validate_host(site, {"NCPU": 2, "MemTotal": 16 * 1024**3})
    with pytest.raises(ValueError, match="memory"):
        validate_host(site, {"NCPU": 8, "MemTotal": 4 * 1024**3})


def test_cli_writes_compose_only_after_read_only_host_preflight(stack, tmp_path):
    from deployment.independent import main

    site, _plan = stack
    site_file, info_file, output = (
        tmp_path / n for n in ("site.json", "host.json", "compose.json")
    )
    site_file.write_text(json.dumps(site))
    info_file.write_text(json.dumps({"NCPU": 8, "MemTotal": 16 * 1024**3}))
    args = [
        "--site",
        str(site_file),
        "--pipelines",
        "deployment/pipelines.example.json",
        "--host-info",
        str(info_file),
        "--output",
        str(output),
    ]
    main(args)
    services = json.loads(output.read_text())["services"]
    live = services["ingesta-live"]
    assert live["pids_limit"] == 512
    assert live["cpus"] == "2"
    assert live["mem_limit"] == "2g"
    assert services["ingesta-history"]["pids_limit"] == 128
    for lane in ("live", "history"):
        assert "--health" in services[f"gold-{lane}"]["healthcheck"]["test"]
        assert services[f"ingesta-{lane}"]["healthcheck"]["test"][4] == "health"
    output.unlink()
    info_file.write_text(json.dumps({"NCPU": 1, "MemTotal": 16 * 1024**3}))
    with pytest.raises(ValueError, match="CPU"):
        main(args)
    assert not output.exists()


def test_refuses_mounts_that_hide_configuration_or_fenix(stack):
    site, plan = stack
    site["configuration_target"] = site["input_target"] + "/config"
    with pytest.raises(ValueError, match="each other"):
        render(site, plan)


def test_initializer_cli_never_accepts_root_identity_or_relative_paths(tmp_path, monkeypatch):
    from deployment.independent_permissions import main

    calls = []
    monkeypatch.setattr("os.chown", lambda *args: calls.append(args), raising=False)
    main(["--uid", "10001", "--gid", "10001", str(tmp_path.resolve())])
    assert calls == [(tmp_path.resolve(), 10001, 10001)]
    with pytest.raises(ValueError, match="non-root"):
        main(["--uid", "0", "--gid", "10001", str(tmp_path)])
    with pytest.raises(ValueError, match="resolved"):
        main(["--uid", "10001", "--gid", "10001", "relative"])
