"""Prepare the Airflow volumes and inspect the existing Gold develop store."""

import argparse
import base64
import gzip
import json
import os
import re
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
export_path = root / 'gold-export-status.json'
export = json.loads(export_path.read_text(encoding='utf-8')) if export_path.is_file() else {}
families = {}
for path in sorted((root / 'logs').glob('*.jsonl'))[-2:]:
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 512 * 1024))
        for line in stream:
            try:
                entry = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            result = entry.get('result', {})
            families[entry['family']] = {
                'time': entry.get('time'),
                **{key: result[key] for key in (
                    'status', 'error_type', 'remaining_versions', 'reason',
                    'attempted', 'input_scan_performed'
                ) if key in result},
            }
print(json.dumps({
    'gold_exists': gold.is_dir(),
    'gold_publication': pointer.is_file(),
    'gold_publication_id': json.loads(pointer.read_text(encoding='utf-8'))['publication_id']
                           if pointer.is_file() else None,
    'gold_config_files': [name for name in paths if (gold / name).is_file()],
    'silver_publication': (root / 'current.json').is_file(),
    'volume_root_uid': root.stat().st_uid,
    'volume_root_mode': oct(root.stat().st_mode & 0o777),
    'silver_export': {key: export[key] for key in (
        'status', 'error_type', 'error', 'last_attempt_at', 'last_success_at', 'publication_id'
    ) if key in export},
    'worker_families': families,
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


def prepare(site, *, enable_export=False):
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
    mount = f"type=volume,source={volume},target=/lake"
    probe = json.loads(
        docker.call(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--mount",
            f"{mount},readonly",
            UTILITY_IMAGE,
            "python",
            "-c",
            PROBE,
        )
    )
    heartbeat = docker.call(
        "exec",
        active["container"],
        "python",
        "-m",
        "ingesta_delta_lake.runtime",
        "health",
        optional=True,
        timeout=15,
    )
    if heartbeat:
        report = json.loads(heartbeat)
        probe["worker_heartbeat"] = {
            key: report[key] for key in ("time", "phase", "family", "workers") if key in report
        }
    else:
        probe["worker_heartbeat"] = {"phase": "UNAVAILABLE"}
    logs = docker.call("logs", "--tail", 300, active["container"], optional=True) or ""
    export_events = []
    for line in logs.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("family") == "GOLD_EXPORT":
            export_events.append({"time": event.get("time"), "result": event.get("result")})
    probe["worker_export_events"] = export_events[-3:]
    if enable_export:
        if not active.get("gold_revision") or probe["volume_root_uid"] not in {0, 10001}:
            raise RuntimeError("No se reconoce el propietario o el exportador activo de develop")
        probe["export_root_repair"] = docker.call(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--user",
            "0:0",
            "--mount",
            mount,
            UTILITY_IMAGE,
            "python",
            "-c",
            "import os, stat; p='/lake'; s=os.stat(p); "
            "assert s.st_uid in (0, 10001) and s.st_mode & stat.S_IWUSR; "
            "os.chown(p, 10001, 10001) if s.st_uid == 0 else None; "
            "assert os.stat(p).st_uid == 10001; print('uid=10001')",
        )
    present = set(docker.call("volume", "ls", "--format", "{{.Name}}").splitlines())
    airflow = {role: compose_volume(docker, role, present) for role in AIRFLOW_VOLUMES}
    return {"volume": volume, "airflow_volumes": airflow, **probe}


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
    parser.add_argument("--enable-export", action="store_true")
    parser.add_argument("--airflow-status", action="store_true")
    args = parser.parse_args()
    site = load_site(args.site)
    report = prepare(site, enable_export=args.enable_export)
    if args.configure_from:
        report.update(configure(site, report, args.configure_from))
    if args.airflow_status:
        docker = Docker(site["docker_context"])
        name = f"{PROJECT}-airflow-dag-processor-1"
        report["dag_processor"] = docker.json(
            "container", "inspect", "--format", "{{json .State}}", name
        )
        logs = subprocess.run(
            ["docker", "--context", docker.context, "logs", "--tail", "30", name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=True,
        )
        report["dag_processor_logs"] = re.sub(
            r"(://[^:/\s]+:)[^@\s]+@", r"\1***@", (logs.stdout + logs.stderr)[-8000:]
        )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
