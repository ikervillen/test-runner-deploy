"""Prepare the Airflow volumes and inspect the existing Gold develop store."""

import argparse
import json
from pathlib import Path

from deployment.manager import Deployment, Docker, load_site

UTILITY_IMAGE = (
    "python:3.12.14-slim-bookworm@sha256:"
    "392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e"
)
PROJECT = "calidad-serie-develop"
AIRFLOW_VOLUMES = ("airflow-db", "airflow-logs")
PROBE = """
import json
from pathlib import Path

root = Path('/lake')
gold = root / 'gold'
paths = ('settings.json', 'catalogs.json', 'customer_score.json',
         'config/settings.json', 'config/catalogs.json', 'config/customer_score.json')
print(json.dumps({
    'gold_exists': gold.is_dir(),
    'gold_publication': (gold / 'current.json').is_file(),
    'gold_config_files': [name for name in paths if (gold / name).is_file()],
    'silver_publication': (root / 'current.json').is_file(),
}))
"""


def prepare(site):
    docker = Docker(site["docker_context"])
    controller = Deployment(site, "develop", docker)
    active = controller.active()
    volume = site["environments"]["develop"]["volume"]
    if not active or active["volume"] != volume:
        raise RuntimeError("El volumen no coincide con el descriptor activo de develop")
    if docker.json("info", "--format", "{{json .}}")["ID"] != active["engine_id"]:
        raise RuntimeError("El motor Docker no coincide con el descriptor activo")
    expected = {
        "es.fenix.ingesta.namespace": site["namespace"],
        "es.fenix.ingesta.channel": "develop",
        "es.fenix.ingesta.instance": active["instance"],
    }
    ownership = docker.json("volume", "inspect", volume)[0].get("Labels") or {}
    if any(ownership.get(key) != value for key, value in expected.items()):
        raise RuntimeError("El volumen de ingesta no pertenece a la instancia activa")
    probe = docker.call(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--mount",
        f"type=volume,source={volume},target=/lake,readonly",
        UTILITY_IMAGE,
        "python",
        "-c",
        PROBE,
    )
    present = set(docker.call("volume", "ls", "--format", "{{.Name}}").splitlines())
    airflow = {}
    for role in AIRFLOW_VOLUMES:
        name = f"{PROJECT}_{role}"
        labels = {"com.docker.compose.project": PROJECT, "com.docker.compose.volume": role}
        if name not in present:
            docker.call(
                "volume",
                "create",
                "--label",
                f"com.docker.compose.project={PROJECT}",
                "--label",
                f"com.docker.compose.volume={role}",
                name,
            )
        metadata = docker.json("volume", "inspect", name)[0].get("Labels") or {}
        if any(metadata.get(key) != value for key, value in labels.items()):
            raise RuntimeError(f"El volumen {name} pertenece a otra pila")
        airflow[role] = name
    return {"volume": volume, "airflow_volumes": airflow, **json.loads(probe)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(load_site(args.site)), sort_keys=True))


if __name__ == "__main__":
    main()
