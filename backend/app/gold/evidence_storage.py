"""Inspect Gold storage and consolidate byte-identical immutable evidence only."""

import argparse
import errno
import os
import re
import stat
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

from .common import IntegrityError, within
from .evidence import checked_file

OBJECT = re.compile(r"([0-9a-f]{64})\.parquet")


def publish_reference(source, target, ref):
    from ingesta_delta_lake.association_cas import publish, sync

    src = within(source, ref["uri"])
    dst = within(target, ref["uri"])
    if dst.exists():
        if not src.samefile(dst):
            checked_file(source, ref)
            checked_file(target, ref)
            return "existing_copy"
        return "existing_link"
    checked_file(source, ref)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sync(dst.parent.parent)
    kind = "new_link"
    try:
        os.link(src, dst)
    except FileExistsError:
        checked_file(target, ref)
        kind = "existing_link" if src.samefile(dst) else "existing_copy"
    except OSError as exc:
        # Separate filesystems can still use the existing, self-contained copy protocol.
        if exc.errno not in {errno.EXDEV, errno.ENOTSUP}:
            raise
        publish(dst, src.read_bytes())
        kind = "new_copy"
    sync(dst.parent)
    return kind


def inventory(root):
    groups, visited = defaultdict(int), set()
    for folder, directories, names in os.walk(root):
        directories.sort()
        for name in sorted(names):
            path = Path(folder) / name
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            identity = info.st_dev, info.st_ino
            parts = path.relative_to(root).parts
            group = "/".join(parts[:2]) if len(parts) > 2 else parts[0]
            if identity not in visited:
                groups[group] += (
                    info.st_blocks * 512 if hasattr(info, "st_blocks") else info.st_size
                )
                visited.add(identity)
    return dict(groups)


def _file_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def consolidate(root, *, apply=False):
    """Both evidence paths and every existing publication remain readable."""
    from ingesta_delta_lake.association_cas import sync

    source = root / "inputs"
    target = root / "published/evidence"
    for relative in (
        "inputs",
        "inputs/data",
        "published",
        "published/evidence",
        "published/evidence/data",
    ):
        path = root / relative
        if path.is_symlink():
            raise IntegrityError(f"Symlink in immutable evidence directory: {path}")
    copies, physical = 0, 0
    for src in sorted((source / "data").glob("*.parquet")):
        matched = OBJECT.fullmatch(src.name)
        dst = target / "data" / src.name
        if not matched or src.is_symlink() or dst.is_symlink() or not dst.is_file():
            continue
        left, right = src.stat(), dst.stat()
        if (
            not stat.S_ISREG(left.st_mode)
            or not stat.S_ISREG(right.st_mode)
            or left.st_dev != right.st_dev
            or left.st_ino == right.st_ino
            or left.st_size != right.st_size
            or (left.st_uid, left.st_gid, left.st_mode)
            != (right.st_uid, right.st_gid, right.st_mode)
        ):
            continue
        ref = {"uri": f"data/{src.name}", "hash": matched[1]}
        checked_file(source, ref)
        checked_file(target, ref)
        if apply:
            temporary = dst.with_name(f".link-{uuid4().hex}")
            try:
                os.link(src, temporary)
                if _file_identity(src.stat()) != _file_identity(left) or _file_identity(
                    dst.stat()
                ) != _file_identity(right):
                    raise IntegrityError(f"Evidence changed during consolidation: {src.name}")
                os.replace(temporary, dst)
                sync(dst.parent)
            finally:
                temporary.unlink(missing_ok=True)
        copies += 1
        if right.st_nlink == 1:
            physical += right.st_blocks * 512 if hasattr(right, "st_blocks") else right.st_size
    return copies, physical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--inventory", action="store_true")
    action.add_argument("--plan", action="store_true")
    action.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if args.inventory:
        sizes = inventory(root)
        for key, size in sorted(sizes.items(), key=lambda item: -item[1]):
            print(f"{size / 2**30:10.3f} GiB  {key}")
        print(f"{sum(sizes.values()) / 2**30:10.3f} GiB  TOTAL")
    else:
        count, size = consolidate(root, apply=args.apply)
        print(f"copias={'consolidadas' if args.apply else 'verificadas'} {count}")
        print(
            f"espacio_fisico={'recuperado' if args.apply else 'recuperable'} {size / 2**30:.3f} GiB"
        )


if __name__ == "__main__":
    main()
