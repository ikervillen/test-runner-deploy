"""Render the independent stack; this module never starts or replaces containers."""

import argparse
import json
import re
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from ingesta_delta_lake.pipeline_policy import LANES, STORES, IndependentPipelines, PipelinePolicy

SERVICES = ("ingesta-live", "ingesta-history", "gold-live", "gold-history", "backend")
HISTORY_SERVICES = ("ingesta-history", "gold-history")
LIVE_SERVICES = tuple(name for name in SERVICES if name not in HISTORY_SERVICES)


class ContainerPipelines:
    """Validate container paths on any host; runtime also checks real filesystem aliases."""

    def __init__(self, value):
        if (
            set(value) != {"schema_id", "policy", *LANES}
            or value["schema_id"] != "fenix-independent-pipelines-v1"
        ):
            raise ValueError("Unknown independent pipeline configuration")
        if set(value["policy"]) != set(PipelinePolicy.__dataclass_fields__):
            raise ValueError("Every pipeline operating policy must be explicit")
        self.policy = PipelinePolicy(**value["policy"])
        paths = []
        for lane in LANES:
            if set(value[lane]) != set(STORES):
                raise ValueError("Every pipeline store must be explicit")
            roots = {}
            for store, raw in value[lane].items():
                path = PurePosixPath(raw)
                if (
                    not path.is_absolute()
                    or str(path) != raw
                    or ".." in path.parts
                    or path == PurePosixPath("/")
                ):
                    raise ValueError("Compose requires canonical absolute Linux container paths")
                roots[store] = path
                paths.append(path)
            setattr(self, lane, SimpleNamespace(**roots))
        for index, path in enumerate(paths):
            if any(
                path.is_relative_to(other) or other.is_relative_to(path)
                for other in paths[index + 1 :]
            ):
                raise ValueError("Container store paths must be physically independent")

    def lane(self, name):
        if name not in LANES:
            raise ValueError("Unknown pipeline lane")
        return getattr(self, name)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def cpu_ids(value):
    result = set()
    for part in value.split(","):
        ends = part.split("-")
        start, stop = int(ends[0]), int(ends[-1])
        if len(ends) > 2 or start < 0 or stop < start:
            raise ValueError("Invalid explicit CPU set")
        result.update(range(start, stop + 1))
    if not result:
        raise ValueError("An explicit CPU set is required")
    return result


def validate(site, pipelines):
    expected = {
        "health_retries",
        "host_reserve_memory",
        "schema_id",
        "project",
        "image",
        "uid",
        "gid",
        "input_bind",
        "input_target",
        "configuration_bind",
        "configuration_target",
        "source_file",
        "pipelines_file",
        "score_file",
        "catalogs_file",
        "api_env_file",
        "api_host",
        "api_port",
        "volumes",
        "forbidden_volumes",
        "resources",
    }
    if set(site) != expected or site["schema_id"] != "fenix-independent-deployment-v1":
        raise ValueError("Unknown independent deployment configuration")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", site["project"]):
        raise ValueError("Invalid Compose project")
    if not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", site["image"]):
        raise ValueError("An immutable image digest is required")
    if any(
        type(site[k]) is not int or site[k] <= 0
        for k in ("uid", "gid", "api_port", "health_retries")
    ):
        raise ValueError("Explicit non-root user and listening port are required")
    if site["api_port"] > 65535 or not site["api_host"]:
        raise ValueError("Invalid API listener")
    for key in ("source_file", "pipelines_file", "score_file", "catalogs_file", "api_env_file"):
        if (
            not isinstance(site[key], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", site[key])
            or not site[key]
        ):
            raise ValueError("Configuration files must have portable basenames")
    binds = []
    for field in ("configuration_target", "input_target"):
        path = PurePosixPath(site[field])
        if (
            not path.is_absolute()
            or str(path) != site[field]
            or ".." in path.parts
            or str(path) == "/"
        ):
            raise ValueError("Invalid container bind mount target")
        others = [getattr(pipelines.lane(lane), store) for lane in LANES for store in STORES]
        if any(path.is_relative_to(other) or other.is_relative_to(path) for other in others):
            raise ValueError("Configuration and Fenix mounts cannot hide pipeline stores")
        binds.append(path)
    if binds[0].is_relative_to(binds[1]) or binds[1].is_relative_to(binds[0]):
        raise ValueError("Configuration and Fenix mounts cannot hide each other")
    names = []
    if set(site["volumes"]) != set(LANES):
        raise ValueError("Both lane volumes must be explicitly configured")
    for lane in LANES:
        if set(site["volumes"][lane]) != set(STORES):
            raise ValueError("Every independent store needs an explicit volume")
        names.extend(site["volumes"][lane].values())
    if len(names) != len(set(names)) or set(names) & set(site["forbidden_volumes"]):
        raise ValueError("New stores cannot share or adopt existing deployment volumes")
    if any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", n) for n in names):
        raise ValueError("Invalid named volume")
    if set(site["resources"]) != set(SERVICES):
        raise ValueError("Every process needs explicit resource limits")
    live_cpus = cpu_ids(site["resources"]["ingesta-live"]["cpuset"])
    for name, limits in site["resources"].items():
        if set(limits) != {"cpus", "cpuset", "memory", "pids", "tmpfs", "score_workers"}:
            raise ValueError("Unknown or missing process resource limits")
        if float(limits["cpus"]) <= 0 or any(
            type(limits[k]) is not int or limits[k] < 1 for k in ("pids", "score_workers")
        ):
            raise ValueError("Process limits must be positive")
        if name != "ingesta-live" and cpu_ids(limits["cpuset"]) & live_cpus:
            raise ValueError("Other processes cannot use the CPUs reserved for live ingestion")
        if any(
            not re.fullmatch(r"[1-9][0-9]*[kmg]", limits[k], re.IGNORECASE)
            for k in ("memory", "tmpfs")
        ):
            raise ValueError("Memory and scratch budgets need explicit units")
    # Loading the plan already verifies all twelve paths, including symlink aliases.
    if not isinstance(pipelines, (IndependentPipelines, ContainerPipelines)):
        raise ValueError("A validated independent pipeline plan is required")


def memory_bytes(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*[kmg]", value, re.IGNORECASE):
        raise ValueError("Memory budgets need explicit k/m/g units")
    return int(value[:-1]) * {"k": 1024, "m": 1024**2, "g": 1024**3}[value[-1].lower()]


def validate_host(site, info, *, with_history=False):
    """Read-only preflight against docker info; never starts a workload."""
    cpus = int(info["NCPU"])
    names = SERVICES if with_history else LIVE_SERVICES
    limits_by_service = [site["resources"][name] for name in names]
    for limits in limits_by_service:
        assigned = cpu_ids(limits["cpuset"])
        if max(assigned) >= cpus or float(limits["cpus"]) > len(assigned):
            raise ValueError("Configured CPU reservations exceed this Docker host")
    memory = sum(memory_bytes(limits["memory"]) for limits in limits_by_service)
    if memory + memory_bytes(site["host_reserve_memory"]) > int(info["MemTotal"]):
        raise ValueError("Process memory budgets leave insufficient Docker host reserve")


def configure_live_only(compose, site):
    services = compose["services"]
    for name in HISTORY_SERVICES:
        services[name]["profiles"] = ["history"]
    for name in ("ingesta-live", "gold-live", "backend"):
        limits = site["resources"][name]
        services[name].update(
            cpus=limits["cpus"],
            cpuset=limits["cpuset"],
            mem_limit=limits["memory"],
            memswap_limit=limits["memory"],
        )
    services["ingesta-live"]["environment"]["FENIX_HISTORY_ENABLED"] = "0"
    services["backend"]["environment"]["APP_GOLD_HISTORY_ENABLED"] = "false"
    return compose


def configure_fresh_live(compose, revision, start):
    import re

    from ingesta_delta_lake.live_epoch import _parse

    _parse(start)
    if not re.fullmatch("[0-9a-f]{40}", revision):
        raise ValueError("Fresh Live requires a full revision")
    services = compose["services"]
    for name in ("ingesta-live", "gold-live", "backend", "initialize"):
        service = services[name]
        service["volumes"] = [
            m for m in service["volumes"] if not m.get("source", "").startswith("history-")
        ]
        if name != "initialize":
            service.setdefault("environment", {})["FENIX_LIVE_T0"] = start
    # Keep the historical service definitions intact, under their disabled profile.
    for name, volume in compose["volumes"].items():
        if name.startswith("live-"):
            volume["name"] = f"{compose['name']}-{revision[:12]}-{name}"
    initializer = services["initialize"]
    initializer["command"] = [
        *initializer["command"][:4],
        *[m["target"] for m in initializer["volumes"]],
    ]
    return compose


def render(site, pipelines, *, with_history=False):
    validate(site, pipelines)
    config_root = site["configuration_target"]

    def mount(lane, store, *, read_only):
        return {
            "type": "volume",
            "source": f"{lane}-{store}",
            "target": str(getattr(pipelines.lane(lane), store)),
            "read_only": read_only,
        }

    def arguments(lane):
        return [
            "--pipelines",
            f"{config_root}/{site['pipelines_file']}",
            "--source",
            f"{config_root}/{site['source_file']}",
            "--lane",
            lane,
        ]

    def base(name):
        limits = site["resources"][name]
        return {
            "image": site["image"],
            "user": f"{site['uid']}:{site['gid']}",
            "init": True,
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "cpus": limits["cpus"],
            "cpuset": limits["cpuset"],
            "mem_limit": limits["memory"],
            "memswap_limit": limits["memory"],
            "pids_limit": limits["pids"],
            "tmpfs": [
                f"/tmp:rw,noexec,nosuid,size={limits['tmpfs']},uid={site['uid']},gid={site['gid']}"
            ],
            "environment": {
                "GOLD_SCORE_WORKERS": str(limits["score_workers"]),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            "restart": "unless-stopped",
            "depends_on": {"initialize": {"condition": "service_completed_successfully"}},
            "healthcheck": {"disable": True},
            "volumes": [
                {
                    "type": "bind",
                    "source": site["configuration_bind"],
                    "target": config_root,
                    "read_only": True,
                }
            ],
        }

    services = {}
    for lane in LANES:
        other = "history" if lane == "live" else "live"
        name = f"ingesta-{lane}"
        service = base(name)
        service.update(
            entrypoint=["python", "-m", "ingesta_delta_lake.pipeline_runtime"],
            command=["worker", *arguments(lane)],
        )
        service["environment"]["INGESTA_REVIEW_ROOT"] = str(
            pipelines.lane(lane).exchange / "reviews"
        )
        service["volumes"] += [
            mount(lane, store, read_only=False) for store in STORES if store != "gold"
        ]
        service["volumes"] += [
            mount(other, store, read_only=True)
            for store in (
                ("silver", "exchange") if lane == "live" else ("bronze", "silver", "exchange")
            )
        ]
        service["volumes"].append(
            {
                "type": "bind",
                "source": site["input_bind"],
                "target": site["input_target"],
                "read_only": True,
            }
        )
        service["healthcheck"] = {
            "test": [
                "CMD",
                "python",
                "-m",
                "ingesta_delta_lake.pipeline_runtime",
                "health",
                *arguments(lane),
            ],
            "interval": f"{pipelines.policy.poll_seconds}s",
            "timeout": f"{pipelines.policy.poll_seconds}s",
            "start_period": f"{pipelines.policy.latency_seconds}s",
            "retries": site["health_retries"],
        }
        services[name] = service
        gold = base(f"gold-{lane}")
        gold.update(
            entrypoint=["python", "-m", "app.gold.independent_runtime"],
            command=[
                *arguments(lane),
                "--score",
                f"{config_root}/{site['score_file']}",
                "--catalogs",
                f"{config_root}/{site['catalogs_file']}",
            ],
        )
        gold["volumes"] += [mount(lane, "gold", read_only=False)]
        gold["volumes"] += [
            mount(lane, store, read_only=True) for store in ("bronze", "silver", "exchange")
        ]
        gold["volumes"] += [
            mount(other, store, read_only=True) for store in ("silver", "exchange", "gold")
        ]
        gold["healthcheck"] = {
            "test": [
                "CMD",
                "python",
                "-m",
                "app.gold.independent_runtime",
                *gold["command"],
                "--health",
            ],
            "interval": f"{pipelines.policy.poll_seconds}s",
            "timeout": f"{pipelines.policy.poll_seconds}s",
            "start_period": f"{pipelines.policy.latency_seconds}s",
            "retries": site["health_retries"],
        }
        services[f"gold-{lane}"] = gold
    api = base("backend")
    api.update(
        entrypoint=["python", "-m", "uvicorn"],
        command=["main:app", "--host", "0.0.0.0", "--port", str(site["api_port"])],
        env_file=[str(Path(site["configuration_bind"]) / site["api_env_file"])],
        ports=[f"{site['api_host']}:{site['api_port']}:{site['api_port']}"],
    )
    live_gold, history_gold = (str(pipelines.lane(lane).gold / "published") for lane in LANES)
    api["environment"].update(
        APP_DATA_BACKEND="gold",
        APP_DATA_ROOT=live_gold,
        APP_GOLD_ROOT=live_gold,
        APP_GOLD_HISTORY_ROOT=history_gold,
    )
    api["volumes"] += [mount(lane, "gold", read_only=True) for lane in LANES]
    services["backend"] = api
    # Initialization touches only roots of the new stores and does not adopt data.
    services["initialize"] = {
        "image": site["image"],
        "user": "0:0",
        "read_only": True,
        "cap_drop": ["ALL"],
        "cap_add": ["CHOWN", "DAC_OVERRIDE", "FOWNER"],
        "entrypoint": ["python", "-m", "deployment.independent_permissions"],
        "command": [
            "--uid",
            str(site["uid"]),
            "--gid",
            str(site["gid"]),
            *[str(getattr(pipelines.lane(lane), s)) for lane in LANES for s in STORES],
        ],
        "volumes": [mount(lane, s, read_only=False) for lane in LANES for s in STORES],
        "healthcheck": {"disable": True},
        "restart": "no",
    }
    compose = {
        "name": site["project"],
        "services": services,
        "volumes": {
            f"{lane}-{store}": {"name": site["volumes"][lane][store]}
            for lane in LANES
            for store in STORES
        },
    }
    return compose if with_history else configure_live_only(compose, site)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--pipelines", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host-info", type=Path)
    args = parser.parse_args(argv)
    site = read(args.site)
    result = render(site, ContainerPipelines(read(args.pipelines)))
    if args.host_info:
        validate_host(site, read(args.host_info))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
