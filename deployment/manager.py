"""Despliegue desde Windows/Linux mediante Docker CLI; sin SSH, shell o borrado de datos."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def locked(path):
    with path.open("a+b") as stream:
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def load_site(path):
    site = json.loads(path.read_text(encoding="utf-8-sig"))
    keys = {
        "repository",
        "namespace",
        "docker_context",
        "state_root",
        "input_bind",
        "drain_seconds",
        "interval_seconds",
        "environments",
        "settings",
    }
    if set(site) != keys:
        raise ValueError("Campos de configuración del servidor incompatibles")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", site["repository"]):
        raise ValueError("Repositorio no válido")
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,40}", site["namespace"]):
        raise ValueError("Namespace no válido")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", site["docker_context"]):
        raise ValueError("Contexto Docker no válido")
    input_bind = site["input_bind"]
    if not (input_bind.startswith("/") or PureWindowsPath(input_bind).is_absolute()) or any(
        c in input_bind for c in ",\n\r"
    ):
        raise ValueError("input_bind debe ser una ruta absoluta admitida por el contexto Docker")
    root = Path(site["state_root"])
    if not root.is_absolute() or root == Path(root.anchor):
        raise ValueError("state_root debe ser una carpeta absoluta externa al checkout")
    project = Path(__file__).resolve().parents[1]
    if root.resolve().is_relative_to(project) or project.is_relative_to(root.resolve()):
        raise ValueError("El estado de despliegue debe estar separado del código")
    if set(site["environments"]) != {"main", "develop"}:
        raise ValueError("Deben declararse main y develop")
    for env in site["environments"].values():
        if set(env) - {"volume", "instance", "review_volumes"} or not {"volume", "instance"} <= set(
            env
        ):
            raise ValueError("Configuración de volumen inválida")
        reviews = env.get("review_volumes", {})
        if reviews and (
            set(reviews) != {"commands", "results"}
            or len(set(reviews.values())) != 2
            or any(not re.fullmatch(r"[a-z][a-z0-9-]{2,60}", v) for v in reviews.values())
            or env["volume"] in reviews.values()
        ):
            raise ValueError("Los canales de decisiones requieren dos volúmenes independientes")
        if not re.fullmatch(r"[a-z][a-z0-9-]{2,60}", env["volume"]):
            raise ValueError("Nombre de volumen inválido")
        if not re.fullmatch(r"[0-9a-f]{32}", env["instance"]):
            raise ValueError("La identidad debe ser un UUID hexadecimal sin guiones")
    for field in ("volume", "instance"):
        if len({e[field] for e in site["environments"].values()}) != 2:
            raise ValueError("main y develop no pueden compartir volumen/identidad")
    review_names = [
        name
        for env in site["environments"].values()
        for name in env.get("review_volumes", {}).values()
    ]
    if len(review_names) != len(set(review_names)):
        raise ValueError("main y develop no pueden compartir canales de decisiones")
    for field, minimum, maximum in (("interval_seconds", 10, 86400), ("drain_seconds", 10, 3600)):
        if type(site[field]) is not int or not minimum <= site[field] <= maximum:
            raise ValueError(f"{field} fuera de rango")
    return site


def current_head(repository, branch):
    token = os.environ["INGESTA_GITHUB_TOKEN"]
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/git/ref/heads/{branch}",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "FenixIngestaDeployment",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["object"]["sha"]


class MaintenanceStillRunning(RuntimeError):
    """An uncertain helper must retain exclusivity; never start a second writer."""


class Docker:
    def __init__(self, context):
        self.context = context

    def call(self, *args, optional=False, timeout=900):
        try:
            result = subprocess.run(
                ["docker", "--context", self.context, *map(str, args)],
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # An optional probe (worker health) must not abort the Gold check.
            if optional:
                return None
            raise
        if result.returncode and not optional:
            raise RuntimeError("Docker " + args[0] + ": " + result.stderr[-4000:])
        return None if result.returncode else result.stdout.strip()

    def json(self, *args):
        return json.loads(self.call(*args))

    def container(self, name):
        names = self.call(
            "container", "ls", "-a", "--format", "{{.Names}}", "--filter", "name=" + name
        )
        return self.json("container", "inspect", name)[0] if name in names.splitlines() else None

    def running(self, name):
        c = self.container(name)
        return bool(c and c["State"]["Running"])

    def helper(self, record, command, *, readonly=True, root=False, timeout=900, trial_volume=None):
        if trial_volume:
            if (
                command[0] != "trial-control"
                or not readonly
                or root
                or not re.fullmatch(
                    re.escape(record["volume"]) + r"-trial-[a-f0-9]{12}", trial_volume
                )
            ):
                raise ValueError("Invalid isolated trial mount")
            record = {k: v for k, v in record.items() if k != "review_volumes"}
        if command[0] == "compact-associations":
            # Streaming maintenance runs beside ingestion, sharing neither its
            # memory budget nor its review command/result write channels.
            record = {k: v for k, v in record.items() if k != "review_volumes"}
            record["resources"] = {"cpus": 1, "memory_bytes": 512 * 1024**2, "workers": 1}
        args = ["--init", "--no-healthcheck", *common_args(record)]
        name = (
            "fenix-control-" + uuid.uuid4().hex
            if command[0]
            in {"compact-control", "trial-control", "rebuild-index", "compact-associations"}
            else None
        )
        if name:
            args += ["--name", name]
        if root:
            args += ["--user", "0:0", "--cap-add", "CHOWN"]
        mount = "type=volume,src=" + record["volume"] + ",dst=/lake"
        if readonly:
            mount += ",readonly"
        if trial_volume:
            args += ["--mount", "type=volume,src=" + trial_volume + ",dst=/trial"]
        try:
            result = self.call(
                "run", "--rm", *args, "--mount", mount, record["image"], *command, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            if name:
                try:
                    # --init forwards SIGTERM to Python; the volume remains
                    # locked until this isolated copy process has really exited.
                    self.call("kill", "--signal=SIGTERM", name, optional=True, timeout=30)
                    self.call("wait", name, optional=True, timeout=60)
                    if self.running(name):
                        raise RuntimeError("Maintenance helper is still active")
                except Exception as exc:
                    raise MaintenanceStillRunning(
                        "Confirm helper termination before resuming ingestion: " + name
                    ) from exc
            raise
        return json.loads(result)

    def fold_control_wal(self, record):
        """Fold a leftover WAL into its database, then drop a tail that will not close.

        The preflight opens controls read-only. A non-empty -wal from the previous
        process blocks that open. TRUNCATE keeps the committed frames. A file that
        is still non-empty afterwards is the unreadable tail and is removed.
        """
        script = (
            "import sqlite3\n"
            "from pathlib import Path\n"
            "root = Path('/lake/control')\n"
            "for path in sorted(root.glob('*.sqlite')):\n"
            "    wal = Path(str(path) + '-wal')\n"
            "    shm = Path(str(path) + '-shm')\n"
            "    if wal.exists() and wal.stat().st_size:\n"
            "        con = sqlite3.connect(path)\n"
            "        try:\n"
            "            con.execute('PRAGMA wal_checkpoint(TRUNCATE)')\n"
            "        finally:\n"
            "            con.close()\n"
            "    if wal.exists() and wal.stat().st_size:\n"
            "        wal.unlink()\n"
            "    if shm.exists() and not wal.exists():\n"
            "        shm.unlink()\n"
        )
        self.call(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "10001:10001",
            "--entrypoint",
            "python",
            "--mount",
            "type=volume,src=" + record["volume"] + ",dst=/lake",
            record["image"],
            "-c",
            script,
            timeout=120,
        )

    def create_control_trial(self, record):
        volume = record["volume"] + "-trial-" + uuid.uuid4().hex[:12]
        self.call(
            "volume",
            "create",
            "--label",
            "fenix.purpose=control-trial",
            "--label",
            "fenix.instance=" + record["instance"],
            volume,
        )
        isolated = {k: v for k, v in record.items() if k != "review_volumes"}
        self.call(
            "run",
            "--rm",
            "--init",
            "--no-healthcheck",
            *common_args(isolated),
            "--user",
            "0:0",
            "--cap-add",
            "CHOWN",
            "--entrypoint",
            "python",
            "--mount",
            "type=volume,src=" + volume + ",dst=/trial",
            record["image"],
            "-c",
            "import os;os.chown('/trial',10001,10001)",
            timeout=60,
        )
        return volume

    def ready(self, record, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running(record["container"]):
                raise RuntimeError("El worker no permanece en ejecución")
            output = self.call(
                "exec",
                record["container"],
                "python",
                "-m",
                "ingesta_delta_lake.runtime",
                "health",
                optional=True,
            )
            if output is not None:
                return json.loads(output)
            time.sleep(1)
        raise RuntimeError("El worker no alcanzó READY en el plazo permitido")

    def activate(self, record):
        self.call(
            "exec", record["container"], "python", "-m", "ingesta_delta_lake.runtime", "activate"
        )

    def drain(self, record, timeout):
        name = record["container"]
        if not self.container(name):
            return
        self.call("update", "--restart=no", name)
        if self.running(name):
            # SIGTERM cooperativo: nunca docker stop con escalado automático a SIGKILL.
            self.call("kill", "--signal=SIGTERM", name)
            deadline = time.monotonic() + timeout
            while self.running(name):
                if time.monotonic() >= deadline:
                    raise RuntimeError("No hay frontera segura; el proceso no se fuerza a terminar")
                time.sleep(1)

    def launch(self, record):
        self.call(
            "run",
            "-d",
            "--name",
            record["container"],
            "--restart=unless-stopped",
            "--label",
            "es.fenix.ingesta.namespace=" + record["namespace"],
            "--label",
            "es.fenix.ingesta.channel=" + record["channel"],
            "--label",
            "es.fenix.ingesta.role=worker",
            "--log-driver",
            "json-file",
            "--log-opt",
            "max-size=20m",
            "--log-opt",
            "max-file=5",
            *common_args(record),
            "--mount",
            "type=volume,src=" + record["volume"] + ",dst=/lake",
            record["image"],
            "worker",
            "--interval",
            record["interval_seconds"],
        )

    def prepare_reviews(self, record, *, allow_create=True):
        """Create only explicitly configured, scope-labelled command/result volumes."""
        reviews = record.get("review_volumes", {})
        if not reviews:
            return
        existing = self.call("volume", "ls", "--format", "{{.Name}}").splitlines()
        for role, name in reviews.items():
            labels = {
                "es.fenix.review.channel": record["channel"],
                "es.fenix.review.instance": record["instance"],
                "es.fenix.review.role": role,
            }
            if name not in existing:
                if not allow_create:
                    raise RuntimeError(
                        "Falta un canal persistente de decisiones; no se recrea vacío"
                    )
                args = ["volume", "create"]
                for key, value in labels.items():
                    args.extend(["--label", key + "=" + value])
                self.call(*args, name)
            actual = self.json("volume", "inspect", name)[0].get("Labels") or {}
            if any(actual.get(k) != v for k, v in labels.items()):
                raise RuntimeError("Canal de decisiones perteneciente a otro ámbito")
        self.call(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "CHOWN",
            "--read-only",
            "--mount",
            "type=volume,src=" + reviews["commands"] + ",dst=/commands",
            "--mount",
            "type=volume,src=" + reviews["results"] + ",dst=/results",
            "--entrypoint",
            "python",
            record["image"],
            "-c",
            "import os; os.chown('/commands',10001,10001); os.chown('/results',10001,10001)",
        )

    def resume(self, record):
        if not self.container(record["container"]):
            raise RuntimeError("Falta el contenedor previo; requiere recuperación operativa")
        # El propio worker comprueba bajo lock y recupera con su imagen/montaje RW.
        # No abrir WAL que necesite reconstruir SHM sobre un volumen readonly.
        self.helper(record, ["identity"])
        self.call("update", "--restart=unless-stopped", record["container"])
        if not self.running(record["container"]):
            self.call("start", record["container"])
        self.ready(record)
        self.activate(record)


def resource_budget(info):
    """Upper bounds, never allocations: leave headroom inside the WSL2 engine."""
    cpus, memory = info.get("NCPU"), info.get("MemTotal")
    if type(cpus) is not int or type(memory) is not int or cpus < 1:
        raise ValueError("Docker no comunica una capacidad de CPU/RAM válida")
    mib = 1024**2
    available = min(6144 * mib, ((memory - 1536 * mib) // (256 * mib)) * 256 * mib)
    if available < 2048 * mib:
        raise ValueError("Docker requiere al menos 3,5 GiB para ingesta y margen operativo")
    return {
        "cpus": min(4, cpus),
        "memory_bytes": available,
        "workers": 2 if cpus >= 2 and available >= 4096 * mib else 1,
    }


def common_args(record):
    # A previous descriptor must retain its original limits during rollback.
    resources = record.get("resources", {"cpus": 2, "memory_bytes": 4 * 1024**3, "workers": 1})
    environment = {
        "INGESTA_CHANNEL": record["channel"],
        "INGESTA_INSTANCE": record["instance"],
        "INGESTA_ACTIVATION": record["token"],
        "INGESTA_SETTINGS": encoded(record["settings"]),
        "INGESTA_WORKERS": str(resources["workers"]),
        "INGESTA_SUPERVISION_EXPORT": "1",
    }
    if record.get("gold_revision"):
        if record["channel"] != "develop":
            raise ValueError("La exportación Gold se habilita únicamente en develop")
        environment["INGESTA_GOLD_EXPORT"] = "1"
    args = [
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "256",
        "--memory",
        str(resources["memory_bytes"]),
        "--memory-swap",
        str(resources["memory_bytes"]),
        "--cpus",
        str(resources["cpus"]),
        "--tmpfs",
        "/tmp:rw,nosuid,size=512m",
        "--mount",
        "type=bind,src=" + record["input_bind"] + ",dst=/input,readonly",
    ]
    for key, value in environment.items():
        args += ["--env", key + "=" + value]
    if record.get("review_volumes"):
        args += [
            "--env",
            "INGESTA_REVIEW_ROOT=/reviews",
            "--mount",
            "type=volume,src="
            + record["review_volumes"]["commands"]
            + ",dst=/reviews/commands,readonly",
            "--mount",
            "type=volume,src=" + record["review_volumes"]["results"] + ",dst=/reviews/results",
        ]
    return args


class Deployment:
    def __init__(self, site, channel, docker, head=current_head):
        if channel not in {"main", "develop"}:
            raise ValueError("Rama no desplegable")
        self.site, self.channel, self.docker, self.head = site, channel, docker, head
        self.root = Path(site["state_root"]) / channel
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_path = self.root / "active.json"
        self.journal_path = self.root / "transaction.json"

    def active(self):
        return (
            json.loads(self.active_path.read_text(encoding="utf-8"))
            if self.active_path.exists()
            else None
        )

    def _adopt_engine_id(self, active, engine_id):
        """Keep the release when Docker Desktop regenerates its daemon id.

        The named volume and its instance labels are the identity. A different
        daemon id with that volume still present is the same machine.
        """
        if active.get("engine_id") == engine_id:
            return active
        try:
            labels = self.docker.json("volume", "inspect", active["volume"])[0].get("Labels") or {}
        except Exception:
            labels = {}
        expected = {
            "es.fenix.ingesta.namespace": active["namespace"],
            "es.fenix.ingesta.channel": active["channel"],
            "es.fenix.ingesta.instance": active["instance"],
        }
        if any(labels.get(key) != value for key, value in expected.items()):
            raise RuntimeError("El motor Docker no coincide con el descriptor activo")
        adopted = dict(active)
        adopted["engine_id"] = engine_id
        save(self.active_path, adopted)
        return adopted

    def adopt_engine_id(self, engine_id):
        with locked(self.root / "deployment.lock"):
            active = self.active()
            if not active:
                raise RuntimeError("El motor Docker no coincide con el descriptor activo")
            return self._adopt_engine_id(active, engine_id)

    def audit(self, journal, phase):
        journal["phase"] = phase
        journal.setdefault("timeline", []).append({"phase": phase, "time": time.time()})
        save(self.journal_path, journal)

    def complete(self, journal, outcome):
        journal["outcome"] = outcome
        self.audit(journal, "COMPLETE")
        receipts = self.root / "receipts"
        receipts.mkdir(exist_ok=True)
        receipt_id = journal.get("operation_id", journal["candidate"]["token"])
        save(receipts / (receipt_id + ".json"), journal)

    def recover(self):
        if not self.journal_path.exists():
            return
        journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        if journal["phase"] == "COMPLETE":
            receipts = self.root / "receipts"
            receipts.mkdir(exist_ok=True)
            key = journal.get("operation_id", journal["candidate"]["token"])
            if not (receipts / (key + ".json")).exists():
                save(receipts / (key + ".json"), journal)
            return
        candidate, old = journal["candidate"], journal["previous"]
        active = self.active()
        if active and active["token"] == candidate["token"]:
            # El puntero se confirmó antes de habilitar escrituras; completar ese handoff.
            self.docker.resume(candidate)
            self.complete(journal, "RECOVERED_ACTIVATION")
            return
        self.docker.drain(candidate, self.site["drain_seconds"])
        if old:
            self.docker.resume(old)
        self.complete(journal, "RECOVERED_PREVIOUS")

    def prepare_volume(self, record, *, allow_create, storage_image=None):
        volume = record["volume"]
        existing = self.docker.call("volume", "ls", "--format", "{{.Name}}").splitlines()
        labels = {
            "es.fenix.ingesta.namespace": record["namespace"],
            "es.fenix.ingesta.channel": self.channel,
            "es.fenix.ingesta.instance": record["instance"],
        }
        if volume not in existing:
            if not allow_create:
                raise RuntimeError("Falta el volumen persistente; no se recrea un histórico vacío")
            args = ["volume", "create"]
            for key, value in labels.items():
                args += ["--label", key + "=" + value]
            self.docker.call(*args, volume)
        actual = self.docker.json("volume", "inspect", volume)[0].get("Labels") or {}
        if any(actual.get(k) != v for k, v in labels.items()):
            raise RuntimeError("El volumen no pertenece al entorno esperado")
        if not allow_create:
            self.docker.helper(record, ["identity"])
        storage_record = {**record, "image": storage_image or record["image"]}
        self.docker.helper(storage_record, ["bootstrap"], readonly=False, root=True)
        self.docker.helper(storage_record, ["storage-check"], readonly=False)

    def assert_latest(self, sha):
        if self.head(self.site["repository"], self.channel) != sha:
            raise RuntimeError("El commit ya no es HEAD de la rama; despliegue obsoleto")

    def deploy(self, image, sha, run_url):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Commit completo obligatorio")
        expected_image = "ghcr.io/" + self.site["repository"].lower()
        if not re.fullmatch(re.escape(expected_image) + r"@sha256:[0-9a-f]{64}", image):
            raise ValueError("Imagen por digest del repositorio autorizado obligatoria")
        with locked(self.root / "deployment.lock"):
            info = self.docker.json("info", "--format", "{{json .}}")
            if info["OSType"] != "linux" or info["Architecture"] not in {"x86_64", "amd64"}:
                raise RuntimeError("Este paquete requiere motor Linux/amd64")
            resources = resource_budget(info)
            self.recover()
            self.assert_latest(sha)
            self.docker.call("pull", image)
            meta = self.docker.json("image", "inspect", image)[0]
            labels = meta["Config"].get("Labels") or {}
            if (
                labels.get("org.opencontainers.image.revision") != sha
                or labels.get("org.opencontainers.image.source")
                != "https://github.com/" + self.site["repository"]
                or labels.get("es.fenix.ingesta.branch") != self.channel
                or image not in meta.get("RepoDigests", [])
            ):
                raise RuntimeError("La identidad/procedencia de imagen no coincide con la release")
            gold_revision = labels.get("es.fenix.ingesta.gold.revision")
            if gold_revision is not None and (
                self.channel != "develop" or not re.fullmatch(r"[0-9a-f]{40}", gold_revision)
            ):
                raise RuntimeError("La imagen Gold exige develop y un commit Gold completo")
            manifest = json.loads(
                self.docker.call(
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,nosuid,size=64m",
                    image,
                    "describe",
                )
            )
            token = uuid.uuid4().hex
            record = {
                **self.site["environments"][self.channel],
                "namespace": self.site["namespace"],
                "channel": self.channel,
                "image": image,
                "commit": sha,
                "run_url": run_url,
                "docker_context": self.site["docker_context"],
                "engine_id": info["ID"],
                "settings": self.site["settings"],
                "input_bind": self.site["input_bind"],
                "interval_seconds": self.site["interval_seconds"],
                "resources": resources,
                "manifest": manifest,
                "token": token,
                "container": self.site["namespace"] + "-" + self.channel + "-" + token[:12],
            }
            if gold_revision:
                record["gold_revision"] = gold_revision
            old = self.active()
            if old and old.get("engine_id") != info["ID"]:
                try:
                    old = self._adopt_engine_id(old, info["ID"])
                except RuntimeError:
                    pass
            if old and (
                old["volume"] != record["volume"]
                or old["instance"] != record["instance"]
                or old["docker_context"] != record["docker_context"]
                or old["engine_id"] != record["engine_id"]
            ):
                raise RuntimeError(
                    "Cambiar almacenamiento/contexto no es una actualización de código"
                )
            prior_manifest = dict(old["manifest"]) if old else {}
            current_manifest = dict(manifest)
            prior_format = prior_manifest.pop("control_payload_format", 1)
            current_format = current_manifest.pop("control_payload_format", 1)
            prior_artifacts = prior_manifest.pop("association_artifact_format", 1)
            current_artifacts = current_manifest.pop("association_artifact_format", 1)
            if current_artifacts < prior_artifacts:
                raise RuntimeError("Association artifact downgrade is not supported")
            if old and (
                prior_manifest != current_manifest
                or (
                    prior_format != current_format
                    and (prior_format, current_format) not in {(1, 2), (1, 3), (2, 3)}
                )
            ):
                raise RuntimeError(
                    "Contratos/protocolo incompatibles; exige migración independiente"
                )
            keys = (
                "image",
                "settings",
                "input_bind",
                "interval_seconds",
                "resources",
                "review_volumes",
                "gold_revision",
            )
            if old and all(old.get(k) == record.get(k) for k in keys):
                if record.get("review_volumes"):
                    self.docker.prepare_reviews(record, allow_create=False)
                self.prepare_volume(record, allow_create=False)
                self.docker.ready(old)
                return {"status": "ALREADY_ACTIVE", "release": old}
            # La imagen ya superó self-test en el job image, sobre este mismo digest.
            if record.get("review_volumes"):
                self.docker.prepare_reviews(
                    record, allow_create=not bool(old and old.get("review_volumes"))
                )
            self.prepare_volume(record, allow_create=old is None)
            self.assert_latest(sha)
            journal = {
                "operation_id": uuid.uuid4().hex,
                "candidate": record,
                "previous": old,
                "started_at": time.time(),
            }
            self.audit(journal, "PREPARED")
            try:
                workers = self.docker.call(
                    "ps",
                    "--format",
                    "{{.Names}}",
                    "--filter",
                    "label=es.fenix.ingesta.role=worker",
                    "--filter",
                    "label=es.fenix.ingesta.namespace=" + record["namespace"],
                    "--filter",
                    "label=es.fenix.ingesta.channel=" + self.channel,
                ).splitlines()
                if set(workers) - ({old["container"]} if old else set()):
                    raise RuntimeError("Hay otro worker del entorno sin reconciliar")
                self.audit(journal, "DRAINING")
                if old:
                    self.docker.drain(old, self.site["drain_seconds"])
                self.docker.fold_control_wal(record)
                checked = self.docker.helper(record, ["check"])
                if not old and any(v is not None for v in checked["versions"].values()):
                    raise RuntimeError("No adoptar tablas sin descriptor de release anterior")
                self.audit(journal, "OLD_STOPPED")
                self.assert_latest(sha)
                self.docker.launch(record)
                self.audit(journal, "STARTED")
                self.docker.ready(record)
                save(self.active_path, record)
                self.audit(journal, "SWITCHED")
                self.docker.activate(record)
                self.docker.ready(record)
                self.complete(journal, "DEPLOYED")
                return {"status": "DEPLOYED", "release": record}
            except Exception:
                # Si se interrumpe también esta recuperación, el journal permite repetirla.
                if self.active() and self.active()["token"] == token:
                    self.docker.drain(record, self.site["drain_seconds"])
                    # Una operación iniciada por la candidata se recupera con ESA imagen.
                    # Si queda pendiente, conservar puntero/journal para reanudarla, sin
                    # arrancar un escritor anterior sobre esa operación incierta.
                    self.docker.helper(record, ["check"])
                    if old and prior_format < current_format:
                        # The new writer may already have compacted a confirmed
                        # payload. An old decoder must never resume on that volume.
                        self.audit(journal, "SWITCHED")
                        raise RuntimeError(
                            "La recuperación requiere la nueva imagen: cambió el formato de control"
                        ) from None
                    if old:
                        self.docker.resume(old)
                        save(self.active_path, old)
                    else:
                        # No hay release anterior; mantener descriptor para recuperar la misma.
                        self.audit(journal, "SWITCHED")
                        raise
                else:
                    self.docker.drain(record, self.site["drain_seconds"])
                    if old:
                        self.docker.resume(old)
                self.complete(journal, "ACTIVATION_ABORTED")
                raise

    def rollback(self, run_url):
        """Reactivar la release previa compatible, conservando todos los datos confirmados."""
        with locked(self.root / "deployment.lock"):
            self.recover()
            if not self.journal_path.exists():
                raise RuntimeError("No existe historial de despliegue")
            history = json.loads(self.journal_path.read_text(encoding="utf-8"))
            current, target = self.active(), history.get("previous")
            if not current or not target or current["token"] == target["token"]:
                raise RuntimeError("No existe una release previa diferente")
            info = self.docker.json("info", "--format", "{{json .}}")
            if (
                info["ID"] != current["engine_id"]
                or target["manifest"] != current["manifest"]
                or target["volume"] != current["volume"]
            ):
                raise RuntimeError("Rollback incompatible con motor/contratos/datos actuales")
            # The previous image may predate storage-check. Use the current
            # runtime to prepare the same validated volume before rolling back.
            self.prepare_volume(target, allow_create=False, storage_image=current["image"])
            journal = {
                "operation_id": uuid.uuid4().hex,
                "candidate": target,
                "previous": current,
                "run_url": run_url,
                "started_at": time.time(),
            }
            self.audit(journal, "DRAINING")
            try:
                self.docker.drain(current, self.site["drain_seconds"])
                self.docker.helper(target, ["check"])
                save(self.active_path, target)
                self.audit(journal, "SWITCHED")
                self.docker.resume(target)
                self.complete(journal, "ROLLED_BACK")
            except Exception:
                self.docker.drain(target, self.site["drain_seconds"])
                if self.active() and self.active()["token"] == target["token"]:
                    self.docker.helper(target, ["check"])
                self.docker.resume(current)
                save(self.active_path, current)
                self.complete(journal, "ROLLBACK_ABORTED")
                raise
            return {"status": "ROLLED_BACK", "release": target}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Despliegue controlado GitHub Actions/WSL2")
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--branch", choices=["main", "develop"], required=True)
    parser.add_argument("--image")
    parser.add_argument("--commit")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args(argv)
    try:
        site = load_site(args.site)
        controller = Deployment(site, args.branch, Docker(site["docker_context"]))
        if args.rollback:
            result = controller.rollback(args.run_url)
        elif args.image and args.commit:
            result = controller.deploy(args.image, args.commit, args.run_url)
        else:
            raise ValueError("Indicar imagen/commit o --rollback")
        print(encoded(result))
        return 0
    except Exception as exc:
        print(encoded({"status": "DEPLOYMENT_FAILED", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
