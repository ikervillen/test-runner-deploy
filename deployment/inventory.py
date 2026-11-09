"""Report and explicitly retire Docker resources from completed deployments."""

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


def inventory(controller, *, remove_retired=False):
    """Use the deploy lock for the inventory and for any explicitly requested removal."""
    if remove_retired and controller.channel != "develop":
        raise ValueError("La retirada solo está habilitada en develop.")
    with locked(controller.root / "deployment.lock"):
        active = controller.active()
        journal = json.loads(controller.journal_path.read_text(encoding="utf-8"))
        if not active or journal["phase"] != "COMPLETE":
            return {"status": "DEFERRED", "reason": "Hay una activación pendiente de reconciliar."}
        docker = controller.docker
        if docker.json("info", "--format", "{{json .}}")["ID"] != active["engine_id"]:
            raise RuntimeError("El inventario se está ejecutando en otro motor Docker.")
        previous = journal.get("previous")
        report = _inventory(controller, active, previous)
        if remove_retired:
            _remove_retired(controller, report, active, previous)
        return report


def _remove_retired(controller, report, active, previous):
    docker = controller.docker
    removed, errors = [], []

    def remove(kind, resource):
        flags = ["--no-prune"] if kind == "image" else []
        try:
            docker.call(kind, "rm", *flags, resource["id"])
        except (RuntimeError, SubprocessError) as exc:
            errors.append({"kind": kind, **resource, "error": str(exc)})
        else:
            removed.append({"kind": kind, **resource})

    for container in report["retire_containers"]:
        remove("container", container)

    # Recheck references after container removal. Docker also refuses removal
    # without --force if another container acquired a reference in the meantime.
    current = _inventory(controller, active, previous)
    candidates = {image["id"] for image in report["retire_images"]}
    for image in current["retire_images"]:
        if image["id"] in candidates and not image["after_removing_containers"]:
            remove("image", image)

    report.update(
        status="CLEANUP_PARTIAL" if errors else "CLEANED",
        removed=removed,
        errors=errors,
    )


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
    parser.add_argument("--remove-retired", action="store_true")
    args = parser.parse_args()
    try:
        site = load_site(args.site)
        result = inventory(
            Deployment(site, args.branch, Docker(site["docker_context"])),
            remove_retired=args.remove_retired,
        )
    except (OSError, ValueError, RuntimeError, KeyError, SubprocessError) as exc:
        result = {"status": "INVENTORY_FAILED", "error": str(exc)}
    output = json.dumps(result, ensure_ascii=True, indent=2)
    print(output)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(f"## Inventario Docker de {args.branch}\n\n```json\n{output}\n```\n")
    return int(
        result["status"] in {"INVENTORY_FAILED", "CLEANUP_PARTIAL"}
        or (args.remove_retired and result["status"] != "CLEANED")
    )


if __name__ == "__main__":
    raise SystemExit(main())
