"""Build and validate a control in separate storage; industrial mounts are read-only."""

import os
import sqlite3
from contextlib import closing
from dataclasses import replace

from .common import IntegrityError
from .control_inventory import inventory
from .control_migration import migrate
from .maintenance import _tune


def trial(config, root):
    root = root.resolve()
    if any(
        root.is_relative_to(p) or p.is_relative_to(root)
        for p in (config.input_root, config.bronze_root, config.silver_root, config.control_root)
    ):
        raise IntegrityError("Trial storage overlaps industrial roots")
    cfg = replace(config, control_root=root / config.family)
    cfg.validate()
    cfg.control_root.mkdir(parents=True, exist_ok=False)
    before = inventory(config.control_path)
    with closing(sqlite3.connect(config.control_path.as_uri() + "?mode=ro", uri=True)) as source:
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        _tune(source)
        if dict(source.execute("SELECT key,value FROM settings")).get("control_format") == "3":
            return {"status": "ALREADY_FORMAT_3", "source": before}
        with closing(sqlite3.connect(cfg.control_path)) as target:
            _tune(target)
            target.execute("PRAGMA synchronous=FULL")
            proof = migrate(source, target, cfg)
        if source.total_changes:
            raise IntegrityError("Read-only source unexpectedly changed")
    with cfg.control_path.open("r+b") as stream:
        os.fsync(stream.fileno())
    return {
        "status": "TRIAL_VERIFIED",
        "source": before,
        "destination": str(cfg.control_path),
        "bytes_after": cfg.control_path.stat().st_size,
        "audit_bytes": sum(p.stat().st_size for p in cfg.control_root.rglob("*.gz")),
        "proof": proof,
        "source_sql_changes": 0,
    }
