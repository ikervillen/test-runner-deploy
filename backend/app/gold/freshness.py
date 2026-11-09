"""Age of the confirmed Silver input: the ingestion heartbeat as seen from Gold.

The producer republishes Silver whenever a discovery succeeds, so the publication
timestamp stops advancing when ingestion stalls even if Gold keeps running.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any


def source_freshness(manifest: dict[str, Any], families: Collection[str]) -> dict[str, Any]:
    discoveries: dict[str, datetime] = {}
    for row in manifest["coverage"]:
        seen = row.get("last_successful_discovery")
        if row.get("source") in families and seen:
            moment = datetime.fromisoformat(str(seen)).replace(tzinfo=None)
            discoveries[row["source"]] = max(moment, discoveries.get(row["source"], moment))
    newest = max(discoveries.values(), default=None)
    return {
        "publication_id": manifest["publication_id"],
        "published_at": manifest["created_at"],
        "source_mode": manifest.get("source_mode"),
        "discovery_lag_seconds": {
            family: (newest - moment).total_seconds()
            for family, moment in sorted(discoveries.items())
            if newest is not None
        },
    }
