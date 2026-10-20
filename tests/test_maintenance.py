import json
from types import SimpleNamespace

import pytest

from deployment.maintenance import execute
from deployment.manager import save


@pytest.fixture
def maintenance(tmp_path):
    record = {
        "channel": "develop",
        "volume": "test-develop",
        "instance": "1" * 32,
        "namespace": "test-fenix",
        "docker_context": "desktop-linux",
        "engine_id": "engine",
        "input_bind": "C:/DataSharing/Fenix",
        "settings": {},
        "token": "a" * 32,
        "image": "ghcr.io/company/ingesta@sha256:" + "a" * 64,
        "commit": "b" * 40,
    }
    site = {
        **record,
        "environments": {"develop": {"volume": record["volume"], "instance": record["instance"]}},
        "state_root": str(tmp_path),
    }
    folder = tmp_path / "develop"
    folder.mkdir()
    save(folder / "active.json", record)
    events = []

    def metadata(*args):
        if args[0] == "info":
            return {"ID": "engine"}
        if args[0] == "volume":
            return [
                {
                    "Labels": {
                        "es.fenix.ingesta.namespace": record["namespace"],
                        "es.fenix.ingesta.channel": "develop",
                        "es.fenix.ingesta.instance": record["instance"],
                    }
                }
            ]
        return [
            {
                "RepoDigests": [record["image"]],
                "Config": {
                    "Labels": {
                        "org.opencontainers.image.revision": record["commit"],
                    }
                },
            }
        ]

    def call(*args, **kwargs):
        events.append((args, kwargs))
        return "" if args[0] == "ps" else json.dumps({"status": "REQUEUED", "events": []})

    docker = SimpleNamespace(json=metadata, call=call, helper=lambda *_: {"status": "COMPATIBLE"})
    return site, docker, record, events


def test_owner_recovery_is_explicit_offline_and_writes_a_receipt(maintenance):
    site, docker, record, events = maintenance
    result = execute(site, "develop", "LISTA_RECH", "run", docker)
    assert result["owner_image"] == record["image"]
    assert result["result"]["status"] == "REQUEUED"
    args = events[-1][0]
    assert "--pull" in args and "never" in args and "none" in args
    assert "--memory-swap" in args and args[args.index("--memory-swap") + 1] == "4g"
    assert "type=volume,src=test-develop,dst=/lake" in args
    assert args[-3:] == ("/repair.py", "--family", "LISTA_RECH")


@pytest.mark.parametrize("reason", ["main", "family", "identity", "volume", "running", "image"])
def test_missing_operational_proof_blocks_before_mutation(maintenance, reason):
    site, docker, record, events = maintenance
    channel, family = "develop", "LISTA_RECH"
    if reason == "main":
        channel = "main"
    elif reason == "family":
        family = "CONT_ZC"
    elif reason == "identity":
        site["input_bind"] = "/different"
    elif reason == "running":
        docker.call = lambda *_: "active-container"
    else:
        original = docker.json

        def metadata(*args):
            if args[0] == reason:
                return [{"Labels": {}, "RepoDigests": [], "Config": {}}]
            return original(*args)

        docker.json = metadata
    with pytest.raises((ValueError, RuntimeError)):
        execute(site, channel, family, "run", docker)
    assert not any(args[0] == "run" for args, _ in events)
