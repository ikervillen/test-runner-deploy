"""Prepare the Airflow volumes and inspect the existing Gold develop store."""

import argparse
import base64
import gzip
import json
import os
import subprocess
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
pointer = gold / 'current.json'
print(json.dumps({
    'gold_exists': gold.is_dir(),
    'gold_publication': pointer.is_file(),
    'gold_publication_id': json.loads(pointer.read_text(encoding='utf-8'))['publication_id']
                           if pointer.is_file() else None,
    'gold_config_files': [name for name in paths if (gold / name).is_file()],
    'silver_publication': (root / 'current.json').is_file(),
}))
"""
WRITE_CONFIG = """
import json
import os
import sys
from pathlib import Path

values = json.load(sys.stdin)
for name, value in values.items():
    path = Path('/config') / name
    if path.exists() and json.loads(path.read_text(encoding='utf-8')) != value:
        raise SystemExit(f'Configuration already exists with different content: {name}')
for name, value in values.items():
    path = Path('/config') / name
    if not path.exists():
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        temporary.chmod(0o644)
        temporary.replace(path)
gold = Path('/gold')
if not (gold / 'current.json').exists():
    os.chown(gold, 10001, 0)
    gold.chmod(0o750)
"""


def compose_volume(docker, role, present):
    name = f"{PROJECT}_{role}"
    labels = {"com.docker.compose.project": PROJECT, "com.docker.compose.volume": role}
    if name not in present:
        args = [
            argument for key, value in labels.items() for argument in ("--label", f"{key}={value}")
        ]
        docker.call("volume", "create", *args, name)
    metadata = docker.json("volume", "inspect", name)[0].get("Labels") or {}
    if any(metadata.get(key) != value for key, value in labels.items()):
        raise RuntimeError(f"El volumen {name} pertenece a otra pila")
    return name


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
    airflow = {role: compose_volume(docker, role, present) for role in AIRFLOW_VOLUMES}
    return {"volume": volume, "airflow_volumes": airflow, **json.loads(probe)}


def configure(site, report, source):
    if not report["gold_exists"]:
        raise RuntimeError("No existe el subdirectorio Gold esperado")
    docker = Docker(site["docker_context"])
    catalog = json.loads(gzip.decompress(base64.b64decode(os.environ["GOLD_CATALOGS_GZIP_B64"])))
    if not catalog.get("model_segments") or any(
        row.get("estado_dar") == "CONFIRMED" for row in catalog["model_segments"]
    ):
        raise ValueError("Se esperaba el catálogo provisional de develop")
    settings = json.loads(
        (source / "gold/settings.container.example.json").read_text(encoding="utf-8")
    )
    settings["plant"] = site["settings"]["plant"]
    config = {
        "settings.json": settings,
        "customer_score.json": json.loads(
            (source / "config/customer_score.json").read_text(encoding="utf-8")
        ),
        "catalogs.json": catalog,
    }
    present = set(docker.call("volume", "ls", "--format", "{{.Name}}").splitlines())
    target = compose_volume(docker, "gold-config", present)
    subprocess.run(
        [
            "docker",
            "--context",
            docker.context,
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--mount",
            f"type=volume,source={target},target=/config",
            "--mount",
            f"type=volume,source={report['volume']},target=/gold,volume-subpath=gold",
            UTILITY_IMAGE,
            "python",
            "-c",
            WRITE_CONFIG,
        ],
        input=json.dumps(config, ensure_ascii=True),
        text=True,
        timeout=90,
        check=True,
    )
    return {"gold_config_volume": target, "provisional_models": len(catalog["model_segments"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--configure-from", type=Path)
    args = parser.parse_args()
    site = load_site(args.site)
    report = prepare(site)
    if args.configure_from:
        report.update(configure(site, report, args.configure_from))
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
