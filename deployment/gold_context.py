"""Download the pinned Gold build context without Windows Expand-Archive."""

import argparse
import io
import os
import re
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_EXTRACTED_BYTES = 64 * 1024 * 1024


def download(target):
    repository = os.environ["GITHUB_REPOSITORY"]
    commit = os.environ["GOLD_CONTEXT_COMMIT"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Repositorio de contexto inválido")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Identificador de contexto inválido")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/zipball/{commit}",
        headers={"Authorization": f"Bearer {os.environ['GOLD_CONTEXT_TOKEN']}"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("El contexto supera el tamaño permitido")

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_EXTRACTED_BYTES:
            raise ValueError("El contexto descomprimido supera el tamaño permitido")
        for item in archive.infolist():
            if item.is_dir():
                continue
            path = PurePosixPath(item.filename)
            parts = path.parts[1:]
            if (
                path.is_absolute()
                or len(parts) == 0
                or any(
                    part in {"", ".", ".."} or ":" in part or "\\" in part for part in path.parts
                )
            ):
                raise ValueError("Ruta de contexto inválida")
            destination = target.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(item))
    if (target / "GOLD_REVISION").read_text().strip() != os.environ["GOLD_SOURCE_COMMIT"]:
        raise ValueError("La identidad del código Gold no coincide")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    args.target.mkdir(parents=True, exist_ok=False)
    download(args.target)


if __name__ == "__main__":
    main()
