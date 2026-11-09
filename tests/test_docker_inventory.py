from types import SimpleNamespace

from deployment.inventory import LABEL, inventory
from deployment.manager import save


def _image(index, *, branch="develop"):
    return {
        "Id": f"sha256:{index:064x}",
        "RepoDigests": [f"ghcr.io/company/ingesta@sha256:{index + 10:064x}"],
        "Size": 1024,
        "Config": {
            "Labels": {
                LABEL + "branch": branch,
                "org.opencontainers.image.source": "https://github.com/company/ingesta",
                "org.opencontainers.image.revision": f"{index:040x}",
            }
        },
    }


def _container(name, image, *, status="exited", restart="no", channel="develop"):
    return {
        "Name": "/" + name,
        "Id": name + "-id",
        "Image": image["Id"],
        "Config": {
            "Image": image["RepoDigests"][0],
            "Labels": {
                LABEL + "namespace": "plant",
                LABEL + "channel": channel,
                LABEL + "role": "worker",
            },
        },
        "State": {"Status": status},
        "HostConfig": {"RestartPolicy": {"Name": restart}},
    }


class DockerInventory:
    def __init__(self, containers, images):
        self.containers = {c["Id"]: c for c in containers}
        self.images = {i["Id"]: i for i in images}
        self.removals = []

    def call(self, *args):
        if args == ("ps", "-aq", "--no-trunc"):
            return "\n".join(self.containers)
        if args[:2] == ("image", "ls"):
            return "\n".join(self.images)
        if args[:2] == ("container", "rm"):
            self.removals.append(args)
            del self.containers[args[-1]]
            return args[-1]
        if args[:3] == ("image", "rm", "--no-prune"):
            self.removals.append(args)
            assert all(c["Image"] != args[-1] for c in self.containers.values())
            del self.images[args[-1]]
            return args[-1]
        raise AssertionError(args)

    def json(self, kind, *args):
        if kind == "info":
            return {"ID": "engine"}
        assert args[0] == "inspect"
        records = self.containers if kind == "container" else self.images
        return [records[key] for key in args[1:]]


def _controller(tmp_path, docker, phase, active, previous=None):
    journal = tmp_path / "transaction.json"
    save(journal, {"phase": phase, "previous": previous})
    return SimpleNamespace(
        root=tmp_path,
        journal_path=journal,
        active=lambda: active,
        channel="develop",
        site={"namespace": "plant", "repository": "company/ingesta"},
        docker=docker,
    )


def _record(name, image):
    return {
        "container": name,
        "image": image["RepoDigests"][0],
        "commit": image["Config"]["Labels"]["org.opencontainers.image.revision"],
        "engine_id": "engine",
    }


def test_inventory_reports_exact_retired_resources_and_keeps_shared_images(tmp_path):
    active, previous, old, shared, orphan = [_image(n) for n in range(5)]
    unrelated = _image(6, branch="main")
    docker = DockerInventory(
        [
            _container("active", active, status="running"),
            _container("previous", previous),
            _container("old", old),
            _container("old-shared", shared),
            _container("other-channel", shared, channel="main"),
            _container("restartable", previous, restart="unless-stopped"),
            _container("running", active, status="running"),
        ],
        [active, previous, old, shared, orphan, unrelated],
    )
    report = inventory(
        _controller(
            tmp_path, docker, "COMPLETE", _record("active", active), _record("previous", previous)
        )
    )

    assert report["keep"]["rollback"]["container"] == "previous"
    assert [r["name"] for r in report["retire_containers"]] == ["old", "old-shared"]
    assert {r["id"] for r in report["retire_images"]} == {old["Id"], orphan["Id"]}
    retired_image = next(r for r in report["retire_images"] if r["id"] == old["Id"])
    assert retired_image["after_removing_containers"] == ["old"]
    assert retired_image["refs"] == old["RepoDigests"]


def test_inventory_defers_during_unreconciled_handoff(tmp_path):
    controller = _controller(tmp_path, None, "SWITCHED", {"container": "candidate"})
    assert inventory(controller)["status"] == "DEFERRED"
    assert inventory(controller, remove_retired=True)["status"] == "DEFERRED"


def test_cleanup_removes_only_retired_resources_and_preserves_rollback(tmp_path):
    active, previous, retired = [_image(n) for n in range(3)]
    docker = DockerInventory(
        [
            _container("active", active, status="running"),
            _container("previous", previous),
            _container("retired", retired),
        ],
        [active, previous, retired],
    )
    controller = _controller(
        tmp_path, docker, "COMPLETE", _record("active", active), _record("previous", previous)
    )
    result = inventory(controller, remove_retired=True)

    assert result["status"] == "CLEANED"
    assert docker.removals == [
        ("container", "rm", "retired-id"),
        ("image", "rm", "--no-prune", retired["Id"]),
    ]
    assert set(docker.containers) == {"active-id", "previous-id"}
    assert set(docker.images) == {active["Id"], previous["Id"]}
    assert inventory(controller, remove_retired=True)["removed"] == []
