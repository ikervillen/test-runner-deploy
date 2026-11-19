"""Gold's owner-local bridge over immutable Silver/Bronze publications only."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime

import pyarrow as pa

from .common import IntegrityError, digest
from .evidence import attribution, registry, save
from .producer import FAMILIES, _apply_horizon, _apply_live, auxiliary

AUXILIARY = frozenset(
    {"MP9_PARAM_MODELO", "SIP_NUMERO_GOTAS_SIMULTANEAS", "SIP_MAQUINA_MOLDEO", "MP9_LISTA_RECH_ZC"}
)
OPERATIONS = frozenset({"LISTA_DEFECTOS", "LISTA_RECH", "LISTA_AR", "CONT_ZC"})


def auxiliary_turn(pipelines, lane, *, encoding):
    from ingesta_delta_lake.pipeline_bronze import BronzeFeed

    from .evidence import Evidence

    storage, policy = getattr(pipelines, lane), pipelines.policy
    root = storage.gold / "checkpoint" / "auxiliary"
    previous = Evidence.open(root) if (root / "current.json").exists() else None
    cursor = previous.document["cursor"] if previous else 0
    initial = (
        {
            r["name"]: previous.table(r["name"], max_rows=policy.read_max_rows).to_pylist()
            for r in previous.document["tables"]
        }
        if previous
        else {}
    )
    try:
        feed = BronzeFeed(storage)
    except FileNotFoundError:
        return auxiliary(root, (), captures=(), initial=initial, encoding=encoding), cursor
    if previous and previous.document["feed_identity"] != feed.identity:
        raise IntegrityError("Gold auxiliary checkpoint belongs to another Bronze owner")
    captures = []
    limit = getattr(policy, f"{lane}_batch_files")
    for capture in feed.captures(after=cursor, limit=limit):
        if capture.family in AUXILIARY:
            captures.append(
                (
                    capture.snapshot.origin,
                    capture.family,
                    feed.read(capture, max_file_bytes=policy.max_file_bytes),
                )
            )
        cursor = capture.sequence
    tables = auxiliary(root, (), captures=captures, initial=initial, encoding=encoding)
    if sum(t.num_rows for t in tables.values()) > policy.read_max_rows:
        raise IntegrityError("Gold auxiliary state exceeds its explicit row budget")
    save(root, {"cursor": cursor, "feed_identity": feed.identity}, tables)
    return tables, cursor


def export(pipelines, lane, config):
    """Never reads foreign SQLite, changes a foreign pointer, or acquires its locks."""
    from ingesta_delta_lake import associations as native
    from ingesta_delta_lake.contracts import CONTRACTS
    from ingesta_delta_lake.pipeline_publications import SilverSnapshot
    from ingesta_delta_lake.pipeline_rows import portable
    from ingesta_delta_lake.pipeline_view import ArchiveSnapshot, SilverView

    storage, policy = getattr(pipelines, lane), pipelines.policy
    own = SilverSnapshot.open(storage)
    from ingesta_delta_lake.common import IntegrityError as NativeIntegrityError

    from .common import atomic_json, read_json
    from .evidence import Evidence

    archive = None
    cached_archive = storage.gold / "checkpoint" / "archive-reference.json"
    try:
        archive = ArchiveSnapshot(pipelines.history)
        atomic_json(cached_archive, archive.manifest)
    except (OSError, NativeIntegrityError, ValueError):
        if cached_archive.exists():
            archive = ArchiveSnapshot(pipelines.history, manifest_path=cached_archive)
    # Live has no dependency on the history backfill's current Silver publication.
    reference = None
    if lane == "history" and (pipelines.live.exchange / "silver" / "current.json").exists():
        reference = SilverSnapshot.open(pipelines.live)
    snapshots = [own, *([reference] if reference else [])]
    auxiliary_tables, cursor = auxiliary_turn(pipelines, lane, encoding=config.encoding)
    historical_gold = None
    try:
        historical_gold = read_json(pipelines.history.gold / "published" / "current.json")[
            "publication_id"
        ]
    except (OSError, ValueError, KeyError):
        historical_gold = None
    vector = [s.manifest["publication_id"] for s in snapshots]
    vector.extend(
        [archive.manifest["publication_id"] if archive else None, cursor, historical_gold]
    )
    if (storage.gold / "inputs" / "current.json").exists():
        previous = Evidence.open(storage.gold / "inputs")
        if previous.document.get("source_vector") == vector:
            return previous
    projections = {family: own.control(family) for family in own.manifest.get("controls", {})}
    tables, lineages, coverage, rejections = {}, {}, [], {}
    views = {
        "operations": SilverView(**{lane: own}, archive=archive if lane == "history" else None),
        "references": SilverView(
            **{lane: own, **({"live": reference} if reference else {})}, archive=archive
        ),
    }
    cases, scanned = {}, 0
    for family in FAMILIES:
        view = views["operations" if family in OPERATIONS else "references"]
        result = view.rows(family, max_rows=policy.read_max_rows, batch_size=policy.read_batch_rows)
        scanned += len(result.rows)
        if scanned > policy.read_max_rows:
            raise IntegrityError("Gold source vector exceeds its explicit row budget")
        if result.conflicts:
            raise IntegrityError("Gold input requires explicit cross-lane review")
        tables[family] = pa.Table.from_pylist(result.rows, schema=CONTRACTS[family].schema)
        lineages[family] = result.lineages
        controls = [
            s.control(family)
            for s in snapshots
            if family in s.manifest.get("controls", {}) and (family not in OPERATIONS or s is own)
        ]
        cases[family] = [case for c in controls for case in c["cases"]]
        hashes = {
            h
            for s in snapshots
            for h in s.manifest["tables"].get(family, {}).get("rejected_hashes", [])
        }
        if archive:
            hashes.update(archive.manifest.get("rejections", {}).get(family, []))
        rejections[family] = sorted(hashes)
        for origin, _path in config.sources:
            relevant = [
                c for c in cases[family] if c["payload"].get("origen_fenix") in {None, origin}
            ]
            ref = own.manifest["tables"].get(family, {})
            row = {
                "source": family,
                "origin": origin,
                "estado": "READY"
                if controls and any(c["version"] is not None for c in controls)
                else "WAITING_INPUT",
                "reason_code": "MANUAL_REVIEW" if relevant else None,
                "quality_hash": digest(cases[family]),
                "last_successful_discovery": ref.get("owner_confirmed_at"),
            }
            instants = [
                r["FECHA"] for r in result.rows if r.get("origen_fenix") == origin and "FECHA" in r
            ]
            if instants:
                from datetime import timedelta

                row.update(
                    covered_from=min(instants) - timedelta(seconds=policy.window_seconds),
                    covered_until=max(instants),
                )
            for control in controls:
                _apply_horizon(row, family, origin, control["meta"].get("observed_until"))
                if control is projections.get(family) or control["control_id"] == projections.get(
                    family, {}
                ).get("control_id"):
                    _apply_live(row, family, origin, control["meta"].get("live_until"))
            coverage.append(portable(row))
    assoc = [
        s.control("ASIGNACION_PRODUCCION_RAMAL")
        for s in snapshots
        if "ASIGNACION_PRODUCCION_RAMAL" in s.manifest.get("controls", {})
    ]
    published = [p["extra"]["association"] for p in assoc if p["extra"].get("association")]
    accepted_registry = registry(published)
    catalogs = [
        s.control("CATALOGO_PRODUCCIONES")["extra"]
        for s in snapshots
        if "CATALOGO_PRODUCCIONES" in s.manifest.get("controls", {})
    ]
    composition = {}
    for catalog in catalogs:
        for key, value in catalog.get("composition", {}).items():
            previous = composition.get(key)
            composition[key] = (
                {**value, "coverage": "REVIEW"}
                if previous is not None and previous != value
                else value
            )
    diagnostics = catalogs[0].get("diagnostics", {}) if catalogs else {}
    historical_closures = [
        c["manifest"]["MP9_CAMBIO_MODELO"]["historical_closures"]
        for c in catalogs
        if c.get("manifest", {}).get("MP9_CAMBIO_MODELO", {}).get("historical_closures")
    ]
    doc = {
        "confirmed_at": max(
            (r["confirmed_at"] for r in own.manifest["tables"].values()), default=None
        ),
        "row_budget": policy.read_max_rows,
        "lane": lane,
        "environment": config.environment,
        "plant": config.plant,
        "sources": [s.manifest["publication_id"] for s in snapshots],
        "archive": archive.manifest["publication_id"] if archive else None,
        "lineages": lineages,
        "rejections": rejections,
        "registry": accepted_registry,
        "lines": dict(config.line_by_origin),
        "association_cases": [c for p in assoc for c in p["cases"]],
        "bundle": {
            "contract": native.CONTRACT,
            "rule": native.RULE,
            "plant": config.plant,
            "topology_enabled": config.topology_enabled,
            "line_codes": dict(config.line_code_by_origin),
            "cases": cases,
            "catalog_composition": composition,
            "catalog_diagnostics": diagnostics,
            **({"historical_mp9_closures": historical_closures} if historical_closures else {}),
        },
        "coverage": coverage,
    }
    if lane == "live":
        retain_live(pipelines, tables, doc)
    tables.update(attribution(doc, tables))
    tables.update(auxiliary_tables)
    doc["bronze_cursor"] = cursor
    doc["source_vector"] = vector
    return save(storage.gold / "inputs", doc, tables)


def cutoff(evidence):
    """Industrial cutoff follows the latest confirmed Fenix block, even if stale."""
    values = [
        datetime.fromisoformat(r["live_until"])
        for r in evidence.manifest["coverage"]
        if r.get("live_until")
    ]
    if not values:
        values = [
            datetime.fromisoformat(r["covered_until"])
            for r in evidence.manifest["coverage"]
            if r.get("covered_until")
        ]
    if not values:
        raise IntegrityError("Gold needs a confirmed source horizon")
    return max(values)


def published_evidence(root):
    from .evidence import Evidence
    from .publications import Snapshot

    snapshot = Snapshot.open(root, require_gold_contract=True)
    descriptor = next(
        d for d in snapshot.manifest["dependencies"] if d.get("kind") == "independent-input"
    )
    return Evidence.open(root / "evidence", descriptor["evidence_id"])


def retain_live(pipelines, tables, document):
    """Keep previously published live facts until historical Gold proves their copy."""
    from ingesta_delta_lake.pipeline_rows import business_key, review_hash

    from .evidence import Evidence

    root, budget = pipelines.live.gold / "inputs", pipelines.policy.read_max_rows
    if not (root / "current.json").exists():
        return
    previous = Evidence.open(root)
    proofs = previous.proofs(max_rows=budget)
    historical = None
    with suppress(OSError, ValueError, StopIteration):
        historical = published_evidence(pipelines.history.gold / "published")
    copied = historical.proofs(max_rows=budget) if historical else {}
    for family in OPERATIONS:
        table = previous.table(family, max_rows=budget)
        current = {business_key(family, r): r for r in tables[family].to_pylist()}
        for row in table.to_pylist():
            key = business_key(family, row)
            if key in current or review_hash(family, row) in document["rejections"][family]:
                continue
            old = proofs.get(family, {}).get(key, [])
            confirmed = copied.get(family, {}).get(key, [])
            if old and all(
                any(
                    p["source_table"] == q["source_table"]
                    and p["source_version"] <= q["source_version"]
                    and p["row_hash"] == q["row_hash"]
                    for q in confirmed
                )
                for p in old
            ):
                continue
            current[key] = row
            document["lineages"].setdefault(family, {})[key] = old
        if len(current) > budget:
            raise IntegrityError("Unconfirmed Gold archive exceeds its explicit row budget")
        tables[family] = pa.Table.from_pylist(list(current.values()), schema=tables[family].schema)
