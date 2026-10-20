import json
from types import SimpleNamespace

import pytest

from deployment.manager import save
from deployment.reset_develop import reset


@pytest.fixture
def target(tmp_path):
    site = {
        "state_root": str(tmp_path),
        "namespace": "test-fenix",
        "docker_context": "desktop-linux",
        "input_bind": "C:/DataSharing/Fenix",
        "environments": {
            "develop": {"volume": "test-develop", "instance": "1" * 32},
            "main": {"volume": "test-main", "instance": "2" * 32},
        },
    }
    record = {
        **site["environments"]["develop"],
        "channel": "develop",
        "engine_id": "engine",
        "docker_context": "desktop-linux",
    }
    root = tmp_path / "develop"
    root.mkdir()
    save(root / "active.json", record)
    save(root / "transaction.json", {"phase": "COMPLETE"})
    labels = {
        "es.fenix.ingesta.namespace": "test-fenix",
        "es.fenix.ingesta.channel": "develop",
        "es.fenix.ingesta.instance": "1" * 32,
    }
    volumes = {"test-develop": labels, "test-main": {"preserve": "main"}}
    events = []

    def metadata(*args):
        if args[0] == "info":
            return {"ID": "engine", "OSType": "linux"}
        return [{"Labels": volumes[args[-1]]}]

    def call(*args):
        events.append(args)
        if args[0] == "ps":
            return ""
        if args[:2] == ("volume", "ls"):
            return "\n".join(volumes)
        if args[:2] == ("volume", "rm"):
            del volumes[args[-1]]
            return "removed"
        if args[:2] == ("volume", "create"):
            volumes[args[-1]] = dict(
                args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "--label"
            )
            return args[-1]
        raise AssertionError(args)

    docker = SimpleNamespace(json=metadata, call=call)
    return site, record, docker, events, volumes


@pytest.mark.parametrize("already_deleted", [False, True])
def test_explicit_reset_recreates_only_develop_and_archives_host_descriptors(
    target, already_deleted
):
    site, record, docker, events, volumes = target
    if already_deleted:
        del volumes["test-develop"]
    result = reset(site, "test-develop", "run", docker)
    assert result["status"] == "RESET"
    assert volumes["test-main"] == {"preserve": "main"}
    assert volumes["test-develop"]["es.fenix.ingesta.instance"] == record["instance"]
    from pathlib import Path

    receipt = Path(result["receipt"])
    assert json.loads(receipt.read_text())["phase"] == "EMPTY_VOLUME_RECREATED"
    assert json.loads((receipt.parent / "active.json").read_text()) == record
    assert not (Path(site["state_root"]) / "develop" / "active.json").exists()
    assert not any("test-main" in args for args in events)
    assert not any(site["input_bind"] in args for args in events)


@pytest.mark.parametrize("reason", ["confirmation", "main", "engine", "labels", "attachments"])
def test_ambiguous_or_unconfirmed_reset_never_deletes_anything(target, reason):
    site, record, docker, events, volumes = target
    confirmed = "test-develop"
    if reason == "confirmation":
        confirmed = "test-main"
    elif reason == "main":
        site["environments"]["main"]["volume"] = confirmed
    elif reason == "engine":
        record["engine_id"] = "other-engine"
        from pathlib import Path

        save(Path(site["state_root"]) / "develop" / "active.json", record)
    elif reason == "labels":
        volumes[confirmed] = {}
    else:
        docker.call = lambda *_: "still-attached"
    with pytest.raises((ValueError, RuntimeError)):
        reset(site, confirmed, "run", docker)
    assert not any(args[:2] in {("volume", "rm"), ("volume", "create")} for args in events)


def test_reset_can_be_retried_if_volume_creation_fails_after_descriptor_archive(target):
    site, _, docker, _, volumes = target
    original = docker.call

    def fail(*args):
        if args[:2] == ("volume", "create"):
            raise OSError("Simulated engine interruption")
        return original(*args)

    docker.call = fail
    with pytest.raises(OSError):
        reset(site, "test-develop", "first", docker)
    assert "test-develop" not in volumes
    docker.call = original
    assert reset(site, "test-develop", "retry", docker)["status"] == "RESET"
