"""Explicit destructive reset of the confirmed develop volume, never main or input."""

import argparse
import json
import os
import time
import uuid
from pathlib import Path

from deployment.manager import Docker, encoded, load_site, locked, save


def reset(site, confirmed_volume, run_url, docker):
    env = site["environments"]["develop"]
    volume = env["volume"]
    if confirmed_volume != volume or volume == site["environments"]["main"]["volume"]:
        raise ValueError("Explicit confirmation of the isolated develop volume is required")
    root = (Path(site["state_root"]) / "develop").resolve()
    root.mkdir(parents=True, exist_ok=True)
    with locked(root / "deployment.lock"):
        active_path = root / "active.json"
        active = (
            json.loads(active_path.read_text(encoding="utf-8")) if active_path.exists() else None
        )
        info = docker.json("info", "--format", "{{json .}}")
        if info["OSType"] != "linux":
            raise RuntimeError("Expected the configured Linux engine")
        if active and any(
            active.get(k) != v
            for k, v in {
                "channel": "develop",
                "volume": volume,
                "instance": env["instance"],
                "engine_id": info["ID"],
                "docker_context": site["docker_context"],
            }.items()
        ):
            raise RuntimeError("Active deployment identity differs from the reset target")
        # No running or stopped attachments; never force removal of another process.
        if docker.call("ps", "-aq", "--filter", "volume=" + volume):
            raise RuntimeError("Remove the develop containers before explicitly resetting storage")
        names = docker.call("volume", "ls", "--format", "{{.Name}}").splitlines()
        labels = {
            "es.fenix.ingesta.namespace": site["namespace"],
            "es.fenix.ingesta.channel": "develop",
            "es.fenix.ingesta.instance": env["instance"],
        }
        if volume in names:
            actual = docker.json("volume", "inspect", volume)[0].get("Labels") or {}
            if any(actual.get(k) != v for k, v in labels.items()):
                raise RuntimeError("Volume identity mismatch; no data was deleted")
        token = uuid.uuid4().hex
        archive = root / "resets" / token
        archive.mkdir(parents=True)
        receipt = {
            "operation": "USER_AUTHORIZED_DEVELOP_RESET",
            "volume": volume,
            "instance": env["instance"],
            "engine_id": info["ID"],
            "run_url": run_url,
            "time": time.time(),
            "previous_release": active,
            "input_bind_preserved": site["input_bind"],
            "phase": "INTENT_RECORDED",
        }
        save(archive / "receipt.json", receipt)
        if volume in names:
            docker.call("volume", "rm", volume)
        receipt["phase"] = "STORAGE_REMOVED"
        save(archive / "receipt.json", receipt)
        # Archive two small host descriptors, never recursively move/delete a directory.
        for name in ("active.json", "transaction.json"):
            source, target = (root / name).resolve(), (archive / name).resolve()
            if source.parent != root or not target.is_relative_to(root / "resets"):
                raise RuntimeError("Descriptor archive is outside the deployment state directory")
            if source.exists():
                os.replace(source, target)
        args = ["volume", "create"]
        for key, value in labels.items():
            args += ["--label", key + "=" + value]
        docker.call(*args, volume)
        receipt["phase"] = "EMPTY_VOLUME_RECREATED"
        save(archive / "receipt.json", receipt)
        return {"status": "RESET", "volume": volume, "receipt": str(archive / "receipt.json")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--confirm-volume", required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    site = load_site(args.site)
    print(encoded(reset(site, args.confirm_volume, args.run_url, Docker(site["docker_context"]))))


if __name__ == "__main__":
    main()
