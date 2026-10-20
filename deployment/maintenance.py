"""Run explicit, offline initial-MIA recovery with the existing owner image."""

import argparse
import hashlib
import json
from pathlib import Path

from deployment.manager import Docker, common_args, encoded, load_site, locked, save


def execute(site, channel, family, run_url, docker):
    if channel != "develop" or family not in {"LISTA_RECH", "LISTA_DEFECTOS"}:
        raise ValueError("Initial recovery is restricted to develop MIA")
    root = Path(site["state_root"]) / channel
    with locked(root / "deployment.lock"):
        record = json.loads((root / "active.json").read_text())
        environment = site["environments"][channel]
        info = docker.json("info", "--format", "{{json .}}")
        if any(record[k] != environment[k] for k in ("volume", "instance")) or any(
            record[k] != value
            for k, value in {
                "channel": channel,
                "namespace": site["namespace"],
                "docker_context": site["docker_context"],
                "engine_id": info["ID"],
                "input_bind": site["input_bind"],
                "settings": site["settings"],
            }.items()
        ):
            raise RuntimeError("Maintenance identity differs from the deployed release")
        # Inspect before mounting so Docker cannot implicitly create a missing volume.
        volume = docker.json("volume", "inspect", record["volume"])[0]
        expected = {
            "es.fenix.ingesta.namespace": record["namespace"],
            "es.fenix.ingesta.channel": channel,
            "es.fenix.ingesta.instance": record["instance"],
        }
        if any((volume.get("Labels") or {}).get(k) != v for k, v in expected.items()):
            raise RuntimeError("Persistent volume identity mismatch")
        if docker.call("ps", "-q", "--filter", "volume=" + record["volume"]):
            raise RuntimeError("Stop every container mounting this volume before maintenance")
        image = docker.json("image", "inspect", record["image"])[0]
        if (
            record["image"] not in image.get("RepoDigests", [])
            or (image["Config"].get("Labels") or {}).get("org.opencontainers.image.revision")
            != record["commit"]
        ):
            raise RuntimeError("The exact owner image must be retained locally")
        docker.helper(record, ["identity"])
        script = Path(__file__).with_name("recovery.py").resolve()
        receipt = {
            "owner_commit": record["commit"],
            "owner_image": record["image"],
            "family": family,
            "run_url": run_url,
            "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
            "volume": record["volume"],
        }
        result = json.loads(
            docker.call(
                "run",
                "--rm",
                "--pull",
                "never",
                *common_args(record),
                "--mount",
                "type=volume,src=" + record["volume"] + ",dst=/lake",
                "--mount",
                "type=bind,src=" + script.as_posix() + ",dst=/repair.py,readonly",
                "--entrypoint",
                "python",
                record["image"],
                "/repair.py",
                "--family",
                family,
                timeout=1800,
            )
        )
        receipt["result"] = result
        save(root / ("recovery-" + family.lower() + ".json"), receipt)
        # Preflight of the owner proves all source schemas/versions remain compatible.
        receipt["check"] = docker.helper(record, ["check"])
        save(root / ("recovery-" + family.lower() + ".json"), receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, type=Path)
    parser.add_argument("--family", required=True, choices=["LISTA_RECH", "LISTA_DEFECTOS"])
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    site = load_site(args.site)
    print(
        encoded(execute(site, "develop", args.family, args.run_url, Docker(site["docker_context"])))
    )


if __name__ == "__main__":
    main()
