import json
from copy import deepcopy
from pathlib import Path

import pytest

from deployment.manager import Deployment, common_args, load_site, resource_budget, save

SHA1, SHA2 = "a" * 40, "b" * 40
IMAGE1 = "ghcr.io/company/ingesta@sha256:" + "1" * 64
IMAGE2 = "ghcr.io/company/ingesta@sha256:" + "2" * 64


@pytest.fixture
def site(tmp_path):
    value = json.loads(Path("deployment/site.example.json").read_text())
    for config in value["environments"].values():
        config.pop("review_volumes", None)
    value.update(
        repository="company/ingesta",
        state_root=str(tmp_path / "deploy"),
        input_bind="/mnt/d/Fenix/Aiala",
    )
    return value


class FakeDocker:
    """Modelo de efectos del motor: verifica handoff y aislamiento sin un daemon local."""

    def __init__(self):
        self.events, self.containers, self.volumes = [], {}, {}
        self.failed, self.incompatible, self.pending = None, False, False
        self.latest = SHA1
        self.capacity = {"NCPU": 4, "MemTotal": 8 * 1024**3}

    def call(self, *args, **kwargs):
        self.events.append(tuple(args))
        if args[0] == "pull":
            return "pulled"
        if args[:2] == ("volume", "ls"):
            return "\n".join(self.volumes)
        if args[:2] == ("volume", "create"):
            labels = dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "--label")
            self.volumes[args[-1]] = labels
            return args[-1]
        if args[0] == "ps":
            channel = next(
                a.split("=", 2)[2] for a in args if "label=es.fenix.ingesta.channel=" in a
            )
            return "\n".join(
                n
                for n, r in self.containers.items()
                if r["running"] and r["record"]["channel"] == channel
            )
        if args[0] == "run" and args[-1] == "describe":
            return json.dumps({"contracts": "different" if self.incompatible else "v2"})
        raise AssertionError(args)

    def json(self, *args):
        if args[0] == "info":
            return {"OSType": "linux", "Architecture": "x86_64", "ID": "engine-1", **self.capacity}
        if args[:2] == ("volume", "inspect"):
            return [{"Labels": self.volumes[args[-1]]}]
        if args[:2] == ("image", "inspect"):
            image = args[-1]
            return [
                {
                    "RepoDigests": [image],
                    "Config": {
                        "Labels": {
                            "org.opencontainers.image.revision": SHA1 if image == IMAGE1 else SHA2,
                            "org.opencontainers.image.source": "https://github.com/company/ingesta",
                            "es.fenix.ingesta.branch": self.branch,
                        }
                    },
                }
            ]
        raise AssertionError(args)

    def helper(self, record, command, **kwargs):
        self.events.append(("helper", record["channel"], tuple(command), kwargs))
        if command == ["check"] and self.pending:
            raise RuntimeError("pending operations")
        return {"versions": {"SIP_MODELO": None}}

    def drain(self, record, timeout):
        self.events.append(("drain", record["container"]))
        if self.failed == "drain" and record["commit"] == SHA1:
            raise RuntimeError("no safe boundary")
        if record["container"] in self.containers:
            self.containers[record["container"]]["running"] = False

    def launch(self, record):
        self.events.append(("launch", record["container"]))
        if self.failed == "launch":
            raise RuntimeError("cannot launch")
        self.containers[record["container"]] = {"record": record, "running": True, "enabled": False}

    def ready(self, record):
        if self.failed == "ready" and record["commit"] == SHA2:
            raise RuntimeError("not ready")
        assert self.containers[record["container"]]["running"]
        return {"phase": "READY"}

    def activate(self, record):
        self.events.append(("activate", record["container"]))
        if self.failed == "activate" and record["commit"] == SHA2:
            raise RuntimeError("activation failure")
        self.containers[record["container"]]["enabled"] = True

    def resume(self, record):
        self.events.append(("resume", record["container"]))
        self.containers[record["container"]]["running"] = True
        self.activate(record)


@pytest.fixture
def motor():
    return FakeDocker()


def deployer(site, motor, branch="main"):
    motor.branch = branch
    return Deployment(site, branch, motor, head=lambda *_: motor.latest)


@pytest.mark.parametrize("branch", ["develop", "main"])
def test_gold_image_activates_export_only_in_develop(site, motor, monkeypatch, branch):
    controller = deployer(site, motor, branch)
    original = motor.json

    def with_gold_label(*args):
        result = original(*args)
        if args[:2] == ("image", "inspect"):
            result[0]["Config"]["Labels"]["es.fenix.ingesta.gold.revision"] = "c" * 40
        return result

    monkeypatch.setattr(motor, "json", with_gold_label)
    if branch == "main":
        with pytest.raises(RuntimeError, match="exige develop"):
            controller.deploy(IMAGE1, SHA1, "run")
        assert not motor.containers
        return

    release = controller.deploy(IMAGE1, SHA1, "run")["release"]
    assert release["gold_revision"] == "c" * 40
    assert "INGESTA_GOLD_EXPORT=1" in common_args(release)
    assert not any(event[-1] == "self-test" for event in motor.events if event)


def test_two_branches_have_independent_worker_volumes_and_descriptors(site, motor):
    for branch in ("main", "develop"):
        result = deployer(site, motor, branch).deploy(IMAGE1, SHA1, "https://github.com/run/1")
        assert result["status"] == "DEPLOYED"
    assert len(motor.volumes) == 2
    assert len(motor.containers) == 2
    assert all(r["enabled"] for r in motor.containers.values())
    for branch in ("main", "develop"):
        helper_calls = [e for e in motor.events if e[0] == "helper" and e[1] == branch]
        assert [e[2] for e in helper_calls] == [("bootstrap",), ("check",)]
        assert helper_calls[-1][3] == {}  # check usa montaje readonly por defecto.


def test_unicode_contract_metadata_remains_compatible_across_updates(site, motor):
    original = motor.call

    def call(*args, **kwargs):
        if args[0] == "run" and args[-1] == "describe":
            return json.dumps({"schema": "ID_AgrupaciÃ³n"}, ensure_ascii=False)
        return original(*args, **kwargs)

    motor.call = call
    dep = deployer(site, motor)
    dep.deploy(IMAGE1, SHA1, "first")
    motor.latest = SHA2
    assert dep.deploy(IMAGE2, SHA2, "update")["status"] == "DEPLOYED"
    assert dep.active()["manifest"] == {"schema": "ID_AgrupaciÃ³n"}


def test_association_storage_downgrade_is_rejected_before_writer_handoff(site, motor):
    dep = deployer(site, motor)
    record = dep.deploy(IMAGE1, SHA1, "first")["release"]
    record["manifest"]["association_artifact_format"] = 2
    save(dep.active_path, record)
    motor.events.clear()
    motor.latest = SHA2
    with pytest.raises(RuntimeError, match="Association artifact downgrade"):
        dep.deploy(IMAGE2, SHA2, "old-reader")
    assert not any(event[0] == "drain" for event in motor.events)


def test_rotated_daemon_id_is_adopted_when_the_same_volume_remains(site, motor):
    dep = deployer(site, motor)
    release = dep.deploy(IMAGE1, SHA1, "run")["release"]
    release["engine_id"] = "previous-daemon"
    save(dep.active_path, release)
    assert dep.adopt_engine_id("engine-1")["engine_id"] == "engine-1"
    assert dep.active()["engine_id"] == "engine-1"


def test_rotated_daemon_id_without_the_instance_volume_is_rejected(site, motor):
    dep = deployer(site, motor)
    release = dep.deploy(IMAGE1, SHA1, "run")["release"]
    release["engine_id"] = "previous-daemon"
    save(dep.active_path, release)
    motor.volumes.clear()
    with pytest.raises(RuntimeError, match="no coincide"):
        dep.adopt_engine_id("engine-1")


def test_code_update_adopts_a_rotated_daemon_id(site, motor):
    dep = deployer(site, motor)
    dep.deploy(IMAGE1, SHA1, "run1")
    active = dep.active()
    active["engine_id"] = "previous-daemon"
    save(dep.active_path, active)
    motor.latest = SHA2
    assert dep.deploy(IMAGE2, SHA2, "run2")["status"] == "DEPLOYED"
    assert dep.active()["engine_id"] == "engine-1"


def test_same_digest_configuration_replay_does_not_restart(site, motor):
    dep = deployer(site, motor)
    dep.deploy(IMAGE1, SHA1, "run1")
    motor.events.clear()
    assert dep.deploy(IMAGE1, SHA1, "run2")["status"] == "ALREADY_ACTIVE"
    assert not any(e[0] in {"drain", "launch", "activate"} for e in motor.events)


def test_update_waits_for_old_and_confirms_pointer_before_activation(site, motor):
    dep = deployer(site, motor)
    old = dep.deploy(IMAGE1, SHA1, "run1")["release"]
    motor.latest = SHA2
    original = motor.activate

    def activate(record):
        assert dep.active()["token"] == record["token"]
        assert not motor.containers[old["container"]]["running"]
        original(record)

    motor.activate = activate
    new = dep.deploy(IMAGE2, SHA2, "run2")["release"]
    assert dep.active() == new
    assert motor.events.index(("drain", old["container"])) < motor.events.index(
        ("launch", new["container"])
    )
    assert len(list((dep.root / "receipts").glob("*.json"))) == 2


@pytest.mark.parametrize("failure", ["drain", "launch", "ready", "activate"])
def test_failure_keeps_or_restores_previous_without_second_writer(site, motor, failure):
    dep = deployer(site, motor)
    old = dep.deploy(IMAGE1, SHA1, "run1")["release"]
    motor.latest, motor.failed = SHA2, failure
    with pytest.raises(RuntimeError):
        dep.deploy(IMAGE2, SHA2, "run2")
    assert dep.active() == old
    assert motor.containers[old["container"]]["running"]
    assert sum(r["running"] for r in motor.containers.values()) == 1


def test_pending_lot_blocks_replacement_and_resumes_original_image(site, motor):
    dep = deployer(site, motor)
    old = dep.deploy(IMAGE1, SHA1, "run1")["release"]
    motor.latest, motor.pending = SHA2, True
    with pytest.raises(RuntimeError, match="pending"):
        dep.deploy(IMAGE2, SHA2, "run2")
    assert dep.active() == old
    assert not any(e[0] == "launch" and e[1] != old["container"] for e in motor.events)


def test_incompatible_contract_fails_before_draining(site, motor):
    dep = deployer(site, motor)
    dep.deploy(IMAGE1, SHA1, "run1")
    motor.events.clear()
    motor.latest, motor.incompatible = SHA2, True
    with pytest.raises(RuntimeError, match="incompatibles"):
        dep.deploy(IMAGE2, SHA2, "run2")
    assert not any(e[0] == "drain" for e in motor.events)


def test_obsolete_push_does_not_activate(site, motor):
    dep = deployer(site, motor)
    motor.latest = SHA2
    with pytest.raises(RuntimeError, match="obsoleto"):
        dep.deploy(IMAGE1, SHA1, "run1")
    assert not motor.containers


def test_recover_interruption_after_pointer_switch_completes_same_activation(site, motor):
    dep = deployer(site, motor)
    old = dep.deploy(IMAGE1, SHA1, "run1")["release"]
    candidate = {**old, "token": "c" * 32, "container": "candidate"}
    motor.drain(old, 10)
    motor.launch(candidate)
    save(dep.active_path, candidate)
    save(dep.journal_path, {"phase": "STARTED", "candidate": candidate, "previous": old})
    dep.recover()
    assert dep.active() == candidate
    assert motor.containers[candidate["container"]]["enabled"]
    assert json.loads(dep.journal_path.read_text())["outcome"] == "RECOVERED_ACTIVATION"


def test_recover_uncommitted_candidate_stops_it_before_resuming_previous(site, motor):
    dep = deployer(site, motor)
    old = dep.deploy(IMAGE1, SHA1, "run1")["release"]
    candidate = {**old, "token": "d" * 32, "container": "candidate"}
    motor.drain(old, 10)
    motor.launch(candidate)
    save(dep.journal_path, {"phase": "STARTED", "candidate": candidate, "previous": old})
    dep.recover()
    assert dep.active() == old
    assert not motor.containers[candidate["container"]]["running"]
    assert motor.containers[old["container"]]["running"]


@pytest.mark.parametrize(
    "change",
    [
        {
            "environments": {
                "main": {"volume": "same", "instance": "1" * 32},
                "develop": {"volume": "same", "instance": "2" * 32},
            }
        },
        {"input_bind": "relative/Fenix"},
        {"interval_seconds": 0},
        {"docker_context": "bad,context"},
        {"namespace": "../other"},
        {"repository": "repo"},
        {"state_root": "."},
    ],
)
def test_site_rejects_ambiguous_or_shared_targets(site, tmp_path, change):
    path = tmp_path / "site.json"
    path.write_text(json.dumps({**deepcopy(site), **change}))
    with pytest.raises(ValueError):
        load_site(path)


def test_settings_pass_as_arguments_never_shell_or_other_environment_mount(site, motor):
    record = deployer(site, motor, "develop").deploy(IMAGE1, SHA1, "run1")["release"]
    args = common_args(record)
    assert "none" in args and "--read-only" in args
    assert "type=bind,src=/mnt/d/Fenix/Aiala,dst=/input,readonly" in args
    assert not any("main" in a for a in args)
    assert not any("docker.sock" in a for a in args)


@pytest.mark.parametrize(
    "image,sha",
    [
        ("ghcr.io/company/ingesta:latest", SHA1),
        (IMAGE1, "abc"),
        (IMAGE1.replace("company", "other"), SHA1),
    ],
)
def test_mutable_or_foreign_release_rejected(site, motor, image, sha):
    with pytest.raises(ValueError):
        deployer(site, motor).deploy(image, sha, "run1")
    assert not motor.events


def test_missing_persistent_volume_is_not_recreated_on_update(site, motor):
    dep = deployer(site, motor)
    original = dep.deploy(IMAGE1, SHA1, "run1")["release"]
    del motor.volumes[original["volume"]]
    motor.latest = SHA2
    motor.events.clear()
    with pytest.raises(RuntimeError, match="Falta el volumen"):
        dep.deploy(IMAGE2, SHA2, "run2")
    assert not any(e[:2] == ("volume", "create") for e in motor.events)
    assert dep.active() == original


def test_explicit_rollback_keeps_data_volume_and_separate_receipts(site, motor):
    dep = deployer(site, motor)
    first = dep.deploy(IMAGE1, SHA1, "run1")["release"]
    motor.latest = SHA2
    second = dep.deploy(IMAGE2, SHA2, "run2")["release"]
    assert dep.rollback("rollback-run")["status"] == "ROLLED_BACK"
    assert dep.active() == first
    assert not motor.containers[second["container"]]["running"]
    assert len(list((dep.root / "receipts").glob("*.json"))) == 3
    assert len(motor.volumes) == 1


def test_pending_candidate_is_not_recovered_by_previous_release(site, motor):
    dep = deployer(site, motor)
    old = dep.deploy(IMAGE1, SHA1, "run1")["release"]
    motor.latest, motor.failed = SHA2, "activate"
    original_helper = motor.helper

    def helper(record, command, **kwargs):
        if command == ["check"] and dep.active()["commit"] == SHA2:
            raise RuntimeError("pending candidate operation")
        return original_helper(record, command, **kwargs)

    motor.helper = helper
    with pytest.raises(RuntimeError, match="pending candidate"):
        dep.deploy(IMAGE2, SHA2, "run2")
    assert dep.active()["commit"] == SHA2
    assert not motor.containers[old["container"]]["running"]
    assert json.loads(dep.journal_path.read_text())["phase"] == "SWITCHED"


def test_rollback_drain_timeout_restores_current_without_reading_inflight_control(site, motor):
    dep = deployer(site, motor)
    dep.deploy(IMAGE1, SHA1, "run1")
    motor.latest = SHA2
    current = dep.deploy(IMAGE2, SHA2, "run2")["release"]
    original = motor.drain

    def drain(record, timeout):
        if record["token"] == current["token"]:
            raise RuntimeError("no safe boundary")
        original(record, timeout)

    motor.drain = drain
    motor.pending = True
    with pytest.raises(RuntimeError, match="no safe boundary"):
        dep.rollback("rollback-run")
    assert dep.active() == current
    assert motor.containers[current["container"]]["running"]


@pytest.mark.parametrize(
    "cpus,memory_gib,expected",
    [(4, 7.76, (4, 6, 2)), (16, 32, (4, 6, 2)), (1, 4, (1, 2.5, 1)), (2, 5, (2, 3.5, 1))],
)
def test_resource_budget_leaves_engine_headroom(cpus, memory_gib, expected):
    budget = resource_budget({"NCPU": cpus, "MemTotal": int(memory_gib * 1024**3)})
    assert (budget["cpus"], budget["memory_bytes"] / 1024**3, budget["workers"]) == expected


@pytest.mark.parametrize(
    "info", [{}, {"NCPU": True, "MemTotal": 8 * 1024**3}, {"NCPU": 4, "MemTotal": 3 * 1024**3}]
)
def test_invalid_or_insufficient_engine_capacity_fails_before_handoff(site, motor, info):
    motor.capacity = info
    with pytest.raises(ValueError):
        deployer(site, motor).deploy(IMAGE1, SHA1, "run1")
    assert not motor.containers and not any(e[0] == "drain" for e in motor.events)


def test_resources_are_recorded_and_legacy_descriptor_keeps_original_limits(site, motor):
    dep = deployer(site, motor)
    record = dep.deploy(IMAGE1, SHA1, "run1")["release"]
    args = common_args(record)
    assert args[args.index("--memory") + 1] == str(6 * 1024**3)
    assert args[args.index("--memory-swap") + 1] == str(6 * 1024**3)
    assert args[args.index("--cpus") + 1] == "4"
    assert "INGESTA_WORKERS=2" in args
    legacy = {k: v for k, v in record.items() if k != "resources"}
    args = common_args(legacy)
    assert args[args.index("--memory") + 1] == str(4 * 1024**3)
    assert "INGESTA_WORKERS=1" in args
    save(dep.active_path, legacy)
    # Same image + different operational limits is an explicit controlled handoff.
    motor.events.clear()
    assert dep.deploy(IMAGE1, SHA1, "budget-update")["status"] == "DEPLOYED"
    assert any(e[0] == "drain" for e in motor.events)


def test_review_channels_have_opposite_mount_permissions_and_persistent_identity(site, motor):
    from deployment.manager import Docker

    record = deployer(site, motor).deploy(IMAGE1, SHA1, "run1")["release"]
    record["review_volumes"] = {
        "commands": "reviews-develop-commands",
        "results": "reviews-develop-results",
    }
    args = common_args(record)
    assert "type=volume,src=reviews-develop-commands,dst=/reviews/commands,readonly" in args
    assert "type=volume,src=reviews-develop-results,dst=/reviews/results" in args
    assert "INGESTA_REVIEW_ROOT=/reviews" in args
    docker = Docker("default")

    def call(*args):
        if args[0] == "run":
            assert "--user" in args and "0:0" in args
            assert all("dst=/lake" not in arg for arg in args)
            return ""
        return motor.call(*args)

    docker.call, docker.json = call, motor.json
    docker.prepare_reviews(record)
    docker.prepare_reviews(record, allow_create=False)
    labels = motor.volumes["reviews-develop-results"]
    assert labels["es.fenix.review.role"] == "results"
    assert labels["es.fenix.review.instance"] == record["instance"]
    del motor.volumes["reviews-develop-results"]
    with pytest.raises(RuntimeError, match="no se recrea"):
        docker.prepare_reviews(record, allow_create=False)


def test_review_channels_cannot_be_shared_between_environments(site, tmp_path):
    value = {"commands": "shared-commands", "results": "shared-results"}
    for config in site["environments"].values():
        config["review_volumes"] = value
    path = tmp_path / "site.json"
    path.write_text(json.dumps(site))
    with pytest.raises(ValueError, match="compartir canales"):
        load_site(path)
