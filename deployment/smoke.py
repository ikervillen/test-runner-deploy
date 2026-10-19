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
            return {
                "status": "PASSED",
                "checks": [
                    "bootstrap",
                    "read-only-preflight",
                    "ready-gate",
                    "activation",
                    "sigterm-drain",
                    "persistent-update",
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    print(json.dumps(exercise(parser.parse_args().image)))
