"""Read-only answer to "is there anything Gold can publish right now?" for schedulers.

READY: new accredited data for at least one production, or a store not yet published.
BLOCKED: Silver moved but no production can advance; the orchestrator may wait.
NOTHING_NEW: the published Silver version is already in Gold.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .coverage import SCOPE_PREFIX, production_scope, published_until
from .live_scope import LiveScope
from .pipeline import LIVE_FAMILIES
from .publications import Snapshot, envelope


def report(
    input_root: Path, output_root: Path, ceiling: datetime, *, scope: LiveScope | None = None
) -> dict:
    silver = Snapshot.open(input_root)
    if scope:
        silver = scope.coverage_view(silver)
    if not (output_root / "current.json").exists():
        return _result("READY", ceiling, silver.publication_id, None, [])
    gold = Snapshot.open(output_root, require_gold_contract=True)
    published = envelope(output_root)[1]["input_publication_id"]
    if published == silver.publication_id:
        return _result("NOTHING_NEW", ceiling, silver.publication_id, published, [])
    scope_rows = gold.rows("publication_scope_status")
    previous = published_until(scope_rows)
    productions = []
    for p in gold.rows("productions"):
        row, cutoff = production_scope(
            p,
            silver.manifest["coverage"],
            ceiling=ceiling,
            previous=previous.get(p["id_produccion"]),
            families=LIVE_FAMILIES,
            evidence=silver.publication_id,
            seen=None,
        )
        before = previous.get(p["id_produccion"])
        productions.append(
            {
                "id_produccion": p["id_produccion"],
                "origin": p["origen_fenix"],
                "estado": row["estado"],
                "missing": row["reason_code"],
                "calculated_until": before.isoformat() if before else None,
                "accredited_until": cutoff.isoformat() if cutoff else None,
                "advances": cutoff is not None and (before is None or cutoff > before),
            }
        )
    known = {r["id_produccion"] for r in scope_rows if str(r["scope_id"]).startswith(SCOPE_PREFIX)}
    new_productions = {p["id_produccion"] for p in productions} - known
    blocked = [p for p in productions if p["estado"] != "READY"]
    if any(p["advances"] for p in productions) or new_productions:
        status = "READY"
    elif blocked:
        status = "BLOCKED"
    else:
        status = "READY"
    return _result(status, ceiling, silver.publication_id, published, productions)


def _result(status, ceiling, silver_id, gold_input_id, productions):
    return {
        "status": status,
        "ceiling": ceiling.isoformat(),
        "silver_publication_id": silver_id,
        "gold_input_publication_id": gold_input_id,
        "blocked_productions": [p["id_produccion"] for p in productions if p["estado"] != "READY"],
        "productions": productions,
    }
