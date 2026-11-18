"""Run the generated Compose with synthetic input on a GitHub-hosted runner."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from deployment.independent import ContainerPipelines, read, render
from ingesta_delta_lake.pipeline_policy import LANES, STORES


def synthetic_stack(root, image, gold_context):
    config = root / "configuration"
    config.mkdir()
    incoming = root / "incoming"
    origin = incoming / "AV21"
    origin.mkdir(parents=True)
    for day, model in ((14, 1), (15, 2)):
        (origin / f"{day}102026_120000_SIP_MODELO.csv").write_text(f"{model};MODEL/{model};1;\n")
        columns = [f"{day}/10/2026 12:00:00", 0, 1, 13, 15, 96, 2, 2, *([0] * 9), ""]
        (origin / f"{day}102026_120000_LISTA_DEFECTOS.csv").write_text(
            ";".join(map(str, columns)) + ";\n"
        )

    site = read(Path("deployment/independent.example.json"))
    project = f"fenix-smoke-{uuid4().hex[:12]}"
    site.update(project=project, input_bind=str(incoming), configuration_bind=str(config))
    site["volumes"] = {
        lane: {store: f"{project}-{lane}-{store}" for store in STORES} for lane in LANES
    }
    # The runner only needs two CPUs: live has one reserved CPU and the others share one.
    for name, limits in site["resources"].items():
        limits.update(cpus="1", cpuset="0" if name == "ingesta-live" else "1", memory="1g")

    (config / site["source_file"]).write_text(
        f'environment = "test"\nplant = "AIALA"\ninput_root = "{site["input_target"]}"\n'
        'bronze_root = "/unused/bronze"\nsilver_root = "/unused/silver"\n'
        'control_root = "/unused/control"\ntimestamp_policy = "local_naive_test_v1"\n'
        '[sources]\nAV21 = "AV21"\n',
        encoding="utf-8",
    )
    (config / site["catalogs_file"]).write_text(
        json.dumps(
            {
                "schema_id": "quality-catalog-bundle-v1",
                "defect_categories": {},
                "mia_labels": {"15": "Synthetic defect"},
                "zone_rules": {},
                "profiles": [],
                "model_segments": [],
            }
        ),
        encoding="utf-8",
    )
    (config / site["api_env_file"]).write_text("", encoding="utf-8")
    shutil.copyfile("deployment/pipelines.example.json", config / site["pipelines_file"])
    shutil.copyfile(gold_context / "config/customer_score.json", config / site["score_file"])
    stack = render(site, ContainerPipelines(read(config / site["pipelines_file"])))
    for service in stack["services"].values():
        # Compose uses the actual image built in this job instead of a registry reference.
        service.update(image=image, pull_policy="never", network_mode="none")
    del stack["services"]["backend"]["ports"]
    return stack


def smoke(image, gold_context):
    if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("This container test requires an isolated GitHub-hosted runner")
    with TemporaryDirectory(prefix="fenix-compose-") as directory:
        root = Path(directory)
        stack = synthetic_stack(root, image, gold_context)
        path = root / "compose.json"
        path.write_text(json.dumps(stack), encoding="utf-8")
        compose = ["docker", "compose", "-f", str(path)]
        services = stack["services"]

        def run(*args, check=True):
            subprocess.run([*compose, *args], check=check, timeout=120)

        def once(service):
            run("run", "--rm", "--no-deps", "-T", service, *services[service]["command"], "--once")

        try:
            run("config", "--quiet")
            run("run", "--rm", "--no-deps", "-T", "initialize")
            for lane, model in (("live", 2), ("history", 1)):
                service = f"ingesta-{lane}"
                once(service)
                run(
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--entrypoint",
                    "python",
                    service,
                    "-c",
                    "from deltalake import DeltaTable; "
                    f"table = DeltaTable('/pipelines/{lane}/silver/AIALA/sip_modelo'); "
                    f"assert table.to_pyarrow_table()['id_modelo_fenix'].to_pylist() == [{model}]",
                )
                once(f"gold-{lane}")
            run(
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--entrypoint",
                "python",
                "backend",
                "-c",
                "from main import app; from app.gold.publications import Snapshot; "
                "from pathlib import Path; import os; "
                "live, history = [Snapshot.open(Path(os.environ[key]), require_gold_contract=True) "
                "for key in ('APP_GOLD_ROOT', 'APP_GOLD_HISTORY_ROOT')]; "
                "assert app and live.publication_id != history.publication_id",
            )
        finally:
            run("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("gold_context", type=Path)
    args = parser.parse_args()
    smoke(args.image, args.gold_context)
