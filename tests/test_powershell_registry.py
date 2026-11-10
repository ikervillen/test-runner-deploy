"""Regression: a Windows runner profile must not prefix the registry token with a BOM."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(
    sys.platform != "win32" or not shutil.which("powershell"),
    reason="Exercises native stdin under Windows PowerShell 5.1",
)
@pytest.mark.parametrize("login_exit", [0, 1])
def test_registry_stdin_is_bom_free_and_encoding_is_restored(tmp_path, login_exit):
    script = Path("scripts/deploy.ps1").resolve()
    capture = tmp_path / "capture.py"
    capture.write_text(
        "import json,os,sys,subprocess\n"
        "from pathlib import Path\n"
        "root=Path(os.environ['REGISTRY_PROBE_ROOT'])\n"
        "if sys.argv[1]=='docker':\n"
        "    (root/'stdin.bin').write_bytes(sys.stdin.buffer.read())\n"
        "    (root/'docker-args.json').write_text(json.dumps(sys.argv[2:]))\n"
        "    sys.exit(int(os.environ['REGISTRY_PROBE_EXIT']))\n"
        "if len(sys.argv)>2 and sys.argv[2]=='-c':\n"
        "    sys.exit(0)\n"
        "if Path(sys.argv[2]).name=='registry.py':\n"
        "    sys.exit(subprocess.call([sys.executable,*sys.argv[2:]]))\n"
        "(root/'manager-called.json').write_text(json.dumps(sys.argv[2:]))\n",
        encoding="utf-8",
    )
    for name, mode in (("docker", "docker"), ("manager", "manager")):
        (tmp_path / f"{name}.cmd").write_text(
            f'@"{sys.executable}" "{capture}" {mode} %*\n', encoding="utf-8"
        )
    wrapper = tmp_path / "invoke.ps1"
    wrapper.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$OutputEncoding=[Text.UTF8Encoding]::new($true)\n"
        "$before=$OutputEncoding\n"
        "$caught=$false\n"
        "try {\n"
        f"  & '{script}' -Site 'unused.json' -Branch develop -Image 'unused' "
        "-Commit 'unused' -RunUrl 'https://example.invalid/run' "
        f"-Python '{tmp_path / 'manager.cmd'}'\n"
        "} catch { $caught=$true }\n"
        "if (-not [object]::ReferenceEquals($before,$OutputEncoding)) "
        "{ throw 'Caller encoding was not restored' }\n"
        f"if ($caught -ne ${str(bool(login_exit)).lower()}) "
        "{ throw 'Unexpected deployment result' }\n"
        "exit 0\n",
        encoding="utf-8-sig",
    )
    source_config = tmp_path / "source-config"
    source_config.mkdir()
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
        "DOCKER_CONFIG": str(source_config),
        "INGESTA_GITHUB_TOKEN": "ghs_synthetic_registry_token",
        "INGESTA_REGISTRY_USER": "synthetic-user",
        "REGISTRY_PROBE_ROOT": str(tmp_path),
        "REGISTRY_PROBE_EXIT": str(login_exit),
    }
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
        ],
        capture_output=True,
        text=True,
        timeout=45,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "stdin.bin").read_bytes().strip() == b"ghs_synthetic_registry_token"
    assert json.loads((tmp_path / "docker-args.json").read_text()) == [
        "login",
        "ghcr.io",
        "--username",
        "synthetic-user",
        "--password-stdin",
    ]
    assert (tmp_path / "manager-called.json").exists() == (login_exit == 0)
    assert not list(tmp_path.glob("fenix-registry-*"))
