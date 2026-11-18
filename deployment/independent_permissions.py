"""Initialize new Docker volume roots; never change ownership recursively."""

import argparse
import os
from pathlib import Path


def initialize(paths, *, uid, gid):
    if type(uid) is not int or type(gid) is not int or min(uid, gid) < 1:
        raise ValueError("A non-root worker identity is required")
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute() or path != path.resolve() or path == Path(path.anchor):
            raise ValueError("Initialization requires explicit resolved store roots")
        if any(path.iterdir()) and (path.stat().st_uid, path.stat().st_gid) != (uid, gid):
            raise ValueError("Refusing to adopt a nonempty store owned by another user")
        os.chown(path, uid, gid)
        path.chmod(0o700)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", type=int, required=True)
    parser.add_argument("--gid", type=int, required=True)
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    initialize(args.paths, uid=args.uid, gid=args.gid)


if __name__ == "__main__":
    main()
