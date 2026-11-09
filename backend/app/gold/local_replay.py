"""Explicit offline replay through the existing ingestion, outside its active storage."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .common import IntegrityError, atomic_json
from .producer import FAMILIES, export_confirmed


def replay(config_path: Path, publication_root: Path) -> dict:
    from ingesta_delta_lake.config import load_config
    from ingesta_delta_lake.service import run

    config = load_config(config_path)
    root = publication_root.resolve()
    if config.environment != "test" or not config.topology_enabled:
        raise IntegrityError("Offline replay requires an explicit test environment and topology")
    if (
        not all(
            p.is_relative_to(root)
            for p in (config.bronze_root, config.silver_root, config.control_root)
        )
        or root == config.input_root
        or root.is_relative_to(config.input_root)
    ):
        raise IntegrityError("Replay destinations must be together and separate from original CSVs")
    if (root / "current.json").exists():
        # Resuming the SAME local replica is allowed; its manifest remains offline.
        from .publications import envelope

        if envelope(root)[1].get("source_mode") != "offline_snapshot":
            raise IntegrityError("Refusing to replay over a non-offline publication")
    results = []
    for family in (*FAMILIES, "ASIGNACION_PRODUCCION_RAMAL"):
        result = run(replace(config, family=family))
        results.append({"family": family, **result})
        atomic_json(root / "replay-results.json", {"sources": results})
    exported = export_confirmed(config, root)
    return {**exported, "mode": "offline_snapshot", "sources": len(results)}
