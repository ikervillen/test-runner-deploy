"""Report which Docker resources a completed deployment no longer needs."""

import argparse
import json
import os
from collections import defaultdict
from itertools import batched
from pathlib import Path
from subprocess import SubprocessError

from deployment.manager import Deployment, Docker, load_site, locked

LABEL = "es.fenix.ingesta."


def _inspect(docker, kind, ids):
    for group in batched(sorted(set(ids.splitlines())), 64):
        yield from docker.json(kind, "inspect", *group)


def inventory(controller):
    """Take a read-only inventory under the same lock used by deploy and rollback."""
    with locked(controller.root / "deployment.lock"):
        active = controller.active()
        journal = json.loads(controller.journal_path.read_text(encoding="utf-8"))
        if not active or journal["phase"] != "COMPLETE":
            return {"status": "DEFERRED", "reason": "Hay una activación pendiente de reconciliar."}
        docker = controller.docker
        if docker.json("info", "--format", "{{json .}}")["ID"] != active["engine_id"]:
            raise RuntimeError("El inventario se está ejecutando en otro motor Docker.")
        return _inventory(controller, active, journal.get("previous"))


def _inventory(controller, active, previous):
    docker = controller.docker
    channel = controller.channel
    site = controller.site
    kept = {"active": active}
    if previous and previous["container"] != active["container"]:
        kept["rollback"] = previous
    protected_names = {record["container"] for record in kept.values()}
    protected_refs = {record["image"] for record in kept.values()}
    ownership = {
        LABEL + "namespace": site["namespace"],
        LABEL + "channel": channel,
        LABEL + "role": "worker",
    }

    containers = list(_inspect(docker, "container", docker.call("ps", "-aq", "--no-trunc")))
    retire = {}
    image_users = defaultdict(set)
    for container in containers:
        name = container["Name"].lstrip("/")
        image_users[container["Image"]].add(name)
        labels = container["Config"].get("Labels") or {}
        if (
            name not in protected_names
            and all(labels.get(key) == value for key, value in ownership.items())
            and container["State"]["Status"] == "exited"
            and container["HostConfig"]["RestartPolicy"]["Name"] == "no"
        ):
            retire[name] = {
                "name": name,
                "id": container["Id"],
                "image": container["Config"]["Image"],
            }

    repo = "ghcr.io/" + site["repository"].lower()
    image_ids = docker.call(
        "image", "ls", "-aq", "--no-trunc", "--filter", f"label={LABEL}branch={channel}"
    )
    images = []
    for image in _inspect(docker, "image", image_ids):
        identity = image["Id"]
        refs = sorted({*(image.get("RepoTags") or []), *(image.get("RepoDigests") or [])})
        labels = image["Config"].get("Labels") or {}
        users = image_users[identity]
        if (
            labels.get(LABEL + "branch") == channel
            and labels.get("org.opencontainers.image.source")
            == "https://github.com/" + site["repository"]
            and all(ref.startswith((repo + "@", repo + ":")) for ref in refs)
            and not protected_refs.intersection([identity, *refs])
            and users <= retire.keys()
        ):
            images.append(
                {
                    "id": identity,
                    "refs": refs,
                    "commit": labels.get("org.opencontainers.image.revision"),
                    "bytes": image["Size"],
                    "after_removing_containers": sorted(users),
                }
            )

    return {
        "status": "INVENTORIED",
        "channel": channel,
        "keep": {
            role: {key: record[key] for key in ("container", "image", "commit")}
            for role, record in kept.items()
        },
        "retire_containers": [retire[name] for name in sorted(retire)],
        "retire_images": images,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--branch", choices=["main", "develop"], required=True)
    args = parser.parse_args()
    try:
        site = load_site(args.site)
        result = inventory(Deployment(site, args.branch, Docker(site["docker_context"])))
    except (OSError, ValueError, RuntimeError, KeyError, SubprocessError) as exc:
        result = {"status": "INVENTORY_FAILED", "error": str(exc)}
    output = json.dumps(result, ensure_ascii=True, indent=2)
    print(output)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(f"## Inventario Docker de {args.branch}\n\n```json\n{output}\n```\n")
    return 1 if result["status"] == "INVENTORY_FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
