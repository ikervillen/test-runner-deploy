"""Send ephemeral credentials to Docker as bytes, independent of PowerShell encoding."""

import os
import shutil
import subprocess
import sys


def main():
    token = os.environ.get("INGESTA_GITHUB_TOKEN")
    user = os.environ.get("INGESTA_REGISTRY_USER")
    if not token or not user:
        print("Missing ephemeral registry credentials.", file=sys.stderr)
        return 1
    docker = shutil.which("docker")
    if not docker:
        print("Docker CLI is not available.", file=sys.stderr)
        return 1
    try:
        result = subprocess.run(
            [docker, "login", "ghcr.io", "--username", user, "--password-stdin"],
            input=token.encode("utf-8") + b"\n",
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        print("Registry login could not complete.", file=sys.stderr)
        return 1
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").replace(token, "[REDACTED]")
        print("Registry login failed: " + message[-2000:], file=sys.stderr)
        return 1
    print("Registry authentication succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
