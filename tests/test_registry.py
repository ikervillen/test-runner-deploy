import subprocess
from types import SimpleNamespace

import pytest

from deployment import registry


@pytest.mark.parametrize("failure", [None, "denied", "timeout", "missing"])
def test_login_uses_binary_stdin_and_redacts_failures(monkeypatch, capsys, failure):
    token = "ghs_synthetic_test_token"
    monkeypatch.setenv("INGESTA_GITHUB_TOKEN", token)
    monkeypatch.setenv("INGESTA_REGISTRY_USER", "test-user")
    monkeypatch.setattr(registry.shutil, "which", lambda _: "docker")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 120)
        return SimpleNamespace(returncode=int(failure == "denied"), stderr=token.encode())

    monkeypatch.setattr(registry.subprocess, "run", run)
    if failure == "missing":
        monkeypatch.delenv("INGESTA_GITHUB_TOKEN")
    assert registry.main() == int(failure is not None)
    if failure == "missing":
        assert not calls
    else:
        assert calls[0][0] == [
            "docker",
            "login",
            "ghcr.io",
            "--username",
            "test-user",
            "--password-stdin",
        ]
        assert calls[0][1]["input"] == token.encode() + b"\n"
    output = capsys.readouterr()
    assert token not in output.out + output.err
