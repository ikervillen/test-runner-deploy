"""Transición real de dos workers Docker sobre datos sintéticos en un runner hospedado."""

import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path

from deployment.manager import Docker


def exercise(image):
    docker = Docker("default")
    identity = uuid.uuid4().hex
    volume = "fenix-ci-" + identity
    records = []
    trial_volumes = []
    with tempfile.TemporaryDirectory(prefix="fenix-ci-input-") as folder:
        Path(folder).chmod(0o755)  # El UID no privilegiado del contenedor solo necesita lectura.
        incoming = Path(folder) / "AV32"
        incoming.mkdir()
        source = incoming / "SIP_MODELO.csv"
        source.write_text("1;3081/360;1;\n", encoding="utf-8")
        record = {
            "namespace": "fenix-ci",
            "channel": "develop",
            "volume": volume,
            "instance": identity,
            "image": image,
            "input_bind": folder,
            "interval_seconds": 10,
            "resources": {"cpus": 2, "memory_bytes": 4 * 1024**3, "workers": 2},
            "settings": {
                "plant": "CI",
                "sources": {"AV32": "AV32"},
                "encoding": "utf-8",
                "line_by_origin": {"AV32": "AV32"},
                "line_code_by_origin": {"AV32": 32},
            },
        }
        docker.call("volume", "create", "--label", "es.fenix.ingesta.ci=" + identity, volume)
        try:

            def start(expected):
                token = uuid.uuid4().hex
                current = {**record, "token": token, "container": "fenix-ci-" + token}
                records.append(current)
                if len(records) == 1:
                    docker.helper(current, ["bootstrap"], readonly=False, root=True)
                    # Reproduce the plant's legacy root-owned Bronze directory.
                    docker.call(
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--user",
                        "0:0",
                        "--cap-drop",
                        "ALL",
                        "--cap-add",
                        "CHOWN",
                        "--mount",
                        f"type=volume,src={volume},dst=/lake",
                        "--entrypoint",
                        "python",
                        image,
                        "-c",
                        "import os; os.chown('/lake/bronze', 0, 0)",
                    )
                    try:
                        docker.helper(current, ["storage-check"], readonly=False)
                    except RuntimeError as exc:
                        if "Sin permiso de escritura" not in str(exc):
                            raise
                    else:
                        raise RuntimeError("Root-owned Bronze did not block the worker UID")
                    docker.helper(current, ["bootstrap"], readonly=False, root=True)
                    assert docker.helper(current, ["storage-check"], readonly=False) == {
                        "status": "WRITABLE"
                    }
                    assert all(
                        v is None for v in docker.helper(current, ["check"])["versions"].values()
                    )
                docker.launch(current)
                docker.ready(current)
                # READY no ha creado tablas antes de la activación.
                if len(records) == 1:
                    assert docker.helper(current, ["check"])["versions"]["SIP_MODELO"] is None
                docker.activate(current)
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    try:
                        if docker.helper(current, ["check"])["versions"]["SIP_MODELO"] == expected:
                            break
                    except RuntimeError:
                        pass  # Puede coincidir con un commit fuente todavía no confirmado.
                    time.sleep(1)
                else:
                    raise RuntimeError("El worker no confirmó la versión esperada")
                docker.drain(current, 90)
                docker.helper(current, ["check"])

            start(0)
            source.write_text("1;3081/360;1;\n2;3082/360;1;\n", encoding="utf-8")
            start(1)
            current = records[-1]  # Already drained: test the real read-only mount.
            trial_volume = docker.create_control_trial(current)
            trial_volumes.append(trial_volume)
            trial = docker.helper(
                current,
                ["trial-control", "--family", "SIP_MODELO", "--trial-root", "/trial"],
                trial_volume=trial_volume,
            )
            assert trial["status"] == "ALREADY_FORMAT_3"
            assert docker.helper(current, ["check"])["versions"]["SIP_MODELO"] == 1
            return {
                "status": "PASSED",
                "checks": [
                    "bootstrap",
                    "legacy-bronze-ownership-repair",
                    "read-only-preflight",
                    "ready-gate",
                    "activation",
                    "sigterm-drain",
                    "persistent-update",
                    "isolated-read-only-control-trial",
                ],
            }
        finally:
            for current in records:
                docker.drain(current, 90)
                docker.call("container", "rm", current["container"])
            # Únicamente el volumen efímero CI creado por esta ejecución, comprobando propiedad.
            metadata = docker.json("volume", "inspect", volume)[0]
            if metadata.get("Labels", {}).get("es.fenix.ingesta.ci") == identity:
                docker.call("volume", "rm", volume)
            for trial_volume in trial_volumes:
                metadata = docker.json("volume", "inspect", trial_volume)[0]
                labels = metadata.get("Labels", {})
                if (
                    labels.get("fenix.instance") == identity
                    and labels.get("fenix.purpose") == "control-trial"
                ):
                    docker.call("volume", "rm", trial_volume)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    print(json.dumps(exercise(parser.parse_args().image)))
