"""Gold's owner-local bridge over immutable Silver/Bronze publications only."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta
from functools import cache
from json import loads

import pyarrow as pa

from . import fragment_delta, scoped_reconciliation
from .common import IntegrityError, digest
from .evidence import OPERATIONS, attribution, registry, save
from .lanes import hour_holding
from .producer import FAMILIES, _apply_horizon, _apply_live, auxiliary
from .series_handover import RELATION, apply_series_handover, recent_relations
from .silver_incremental import FullReadRequired, references

AUXILIARY = frozenset(
    {"MP9_PARAM_MODELO", "SIP_NUMERO_GOTAS_SIMULTANEAS", "SIP_MAQUINA_MOLDEO", "MP9_LISTA_RECH_ZC"}
)


def auxiliary_turn(pipelines, lane, *, encoding):
    from ingesta_delta_lake.pipeline_bronze import BronzeFeed

    from .evidence import Evidence

    storage, policy = getattr(pipelines, lane), pipelines.policy
    root = storage.gold / "checkpoint" / "auxiliary"
    previous = Evidence.open(root) if (root / "current.json").exists() else None
    cursor = previous.document["cursor"] if previous else 0
    initial_cursor = cursor
    relation_cursor = previous.document.get("relation_cursor", 0) if previous else 0
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
        return (
            auxiliary(root, (), captures=(), initial=initial, encoding=encoding),
            cursor,
            ([r["hash"] for r in previous.document["tables"]], relation_cursor)
            if previous
            else ([], relation_cursor),
        )
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
        if capture.family == RELATION:
            relation_cursor = capture.sequence
        cursor = capture.sequence
    tables = auxiliary(root, (), captures=captures, initial=initial, encoding=encoding)
    if sum(t.num_rows for t in tables.values()) > policy.read_max_rows:
        raise IntegrityError("Gold auxiliary state exceeds its explicit row budget")
    confirmed = previous
    if previous is None or cursor != initial_cursor:
        confirmed = save(
            root,
            {"cursor": cursor, "feed_identity": feed.identity, "relation_cursor": relation_cursor},
            tables,
            reuse=previous,
        )
    return tables, cursor, ([r["hash"] for r in confirmed.document["tables"]], relation_cursor)


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

    from .common import atomic_json, immutable_json, live_epoch, read_json
    from .evidence import Evidence

    reconciliation_root = storage.gold / "checkpoint" / "reconciliation"
    active_path = reconciliation_root / "active.json"
    input_pointer = storage.gold / "inputs" / "current.json"
    active = None
    if lane == "live" and active_path.exists() and input_pointer.exists():
        pinned = read_json(active_path)
        if read_json(input_pointer)["publication_id"] == pinned["base"] and (
            not pinned.get("blocked") or pinned["target"] == own.manifest["publication_id"]
        ):
            active = pinned
            own = SilverSnapshot.open(storage, publication_id=active["target"])

    archive = None
    cached_archive = storage.gold / "checkpoint" / "archive-reference.json"
    if active is not None:
        if active.get("archive"):
            archive = ArchiveSnapshot(
                pipelines.history,
                manifest_path=reconciliation_root / f"archive-{active['archive']}.json",
            )
            if archive.manifest["publication_id"] != active["archive"]:
                raise IntegrityError("Frozen archive reference differs")
    elif not (lane == "live" and live_epoch()):
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
    auxiliary_tables, cursor, auxiliary_version = auxiliary_turn(
        pipelines, lane, encoding=config.encoding
    )
    historical_gold = None
    try:
        historical_gold = read_json(pipelines.history.gold / "published" / "current.json")[
            "publication_id"
        ]
    except (OSError, ValueError, KeyError):
        historical_gold = None
    if lane == "live" and live_epoch():
        historical_gold = None
    if active is not None:
        historical_gold = active["history"]
    vector = [s.manifest["publication_id"] for s in snapshots]
    vector.extend(
        [
            archive.manifest["publication_id"] if archive else None,
            list(auxiliary_version) if lane == "live" else cursor,
            historical_gold,
        ]
    )
    previous = None
    if (storage.gold / "inputs" / "current.json").exists():
        previous = Evidence.open(storage.gold / "inputs")
        if previous.document.get("source_vector") == vector and (
            lane != "live" or "series_handover_scopes" in previous.document
        ):
            return previous

    @cache
    def confirmed_control(snapshot, family):
        return snapshot.control(family)

    projections = {
        family: confirmed_control(own, family) for family in own.manifest.get("controls", {})
    }
    tables, lineages, coverage, rejections = {}, {}, [], {}
    views = {
        "operations": SilverView(**{lane: own}, archive=archive if lane == "history" else None),
        "references": SilverView(
            **{lane: own, **({"live": reference} if reference else {})}, archive=archive
        ),
    }
    silver_refs = references(own) if lane == "live" else {}
    archive_id = archive.manifest["publication_id"] if archive else None
    incremental = {}
    can_apply_cdf = (
        lane == "live"
        and previous is not None
        and "silver_refs" in previous.document
        and (
            not previous.document.get("retained_live")
            or (
                previous.document["archive"] == archive_id
                and previous.document.get("source_vector", [None])[-1] == historical_gold
            )
        )
    )
    migrating = (
        lane == "live"
        and previous is not None
        and "silver_refs" in previous.document
        and previous.document.get("fragment_lineage") != policy.window_seconds
    )
    if migrating:
        previous = fragment_delta.migrate_lineage(
            previous, policy.window_seconds, batch_size=policy.read_batch_rows
        )
    if can_apply_cdf:
        for family in sorted(OPERATIONS):
            try:
                incremental[family] = fragment_delta.update(
                    own,
                    previous,
                    family,
                    silver_refs.get(family),
                    CONTRACTS[family].schema,
                    budget=policy.read_max_rows,
                    seconds=policy.window_seconds,
                )
            except FullReadRequired:
                continue
    reconciled = {}
    if (
        lane == "live"
        and previous is not None
        and previous.document.get("fragment_lineage") == policy.window_seconds
        and (pending_families := sorted(OPERATIONS - incremental.keys()))
    ):
        if archive:
            immutable_json(reconciliation_root / f"archive-{archive_id}.json", archive.manifest)
        pinned = {
            "base": previous.publication_id,
            "target": own.manifest["publication_id"],
            "history": historical_gold,
            "archive": archive_id,
        }
        atomic_json(active_path, pinned)

        @cache
        def historical_evidence():
            return (
                published_evidence(pipelines.history.gold / "published", historical_gold)
                if historical_gold
                else None
            )

        @cache
        def legacy_proofs(family):
            return historical_evidence().proofs(max_rows=policy.read_max_rows, families={family})

        def certified(family, scope, identity, sources):
            history = historical_evidence()
            if history is None or not sources:
                return False
            ref = next((r for r in history.document["tables"] if r["name"] == "__lineage"), {})
            if "family_roots" in ref:
                part = history.index.find(ref["family_roots"].get(family), (family, *scope))
                candidates = (
                    [
                        loads(r["proof"])
                        for r in history._read_part("__lineage", part).to_pylist()
                        if r["key"] == identity
                    ]
                    if part
                    else []
                )
                history._fragments.pop("__lineage", None)
            else:
                candidates = legacy_proofs(family).get(family, {}).get(identity, [])
            return all(
                any(
                    p["source_table"] == q["source_table"]
                    and p["row_hash"] == q["row_hash"]
                    and p["source_version"] <= q["source_version"]
                    for q in candidates
                )
                for p in sources
            )

        for family in pending_families:
            try:
                fact, lineage, state = scoped_reconciliation.run(
                    reconciliation_root,
                    previous,
                    own,
                    family,
                    CONTRACTS[family].schema,
                    origins=tuple(origin for origin, _ in config.sources),
                    seconds=policy.window_seconds,
                    budget=policy.read_max_rows,
                    batch_size=policy.read_batch_rows,
                    certified=certified,
                    historical_id=historical_gold,
                    archive_id=archive_id,
                    archive_rejections=archive.manifest.get("rejections", {}).get(family, ())
                    if archive
                    else (),
                )
            except (IntegrityError, NativeIntegrityError) as exc:
                atomic_json(active_path, {**pinned, "blocked": str(exc)})
                raise
            incremental[family] = fact, lineage, 0
            reconciled[family] = {k: state[k] for k in ("examined", "peak_rows", "retained")}
    cases, scanned, logical_rows, materialized = {}, 0, 0, 0
    reused_catalogues = []
    prior_proofs = (
        previous.proofs(max_rows=policy.read_max_rows, families=set(FAMILIES) - incremental.keys())
        if lane == "live" and previous
        else None
    )
    incremental_families = set(incremental)
    for family in FAMILIES:
        view = views["operations" if family in OPERATIONS else "references"]
        if family in incremental:
            _, _, scanned_rows = incremental[family]
            rows, proofs = [], {}
        elif (
            lane == "live"
            and family not in OPERATIONS
            and previous
            and "silver_refs" in previous.document
            and previous.document["archive"] == archive_id
            and previous.document["silver_refs"].get(family) == silver_refs.get(family)
        ):
            rows = previous.rows(family, max_rows=policy.read_max_rows)
            proofs, scanned_rows = (prior_proofs or {}).get(family, {}), 0
            reused_catalogues.append(family)
        else:
            result = view.rows(
                family, max_rows=policy.read_max_rows, batch_size=policy.read_batch_rows
            )
            if result.conflicts:
                raise IntegrityError("Gold input requires explicit cross-lane review")
            rows, proofs, scanned_rows = result.rows, result.lineages, len(result.rows)
        scanned += scanned_rows
        logical_rows += incremental[family][0]["rows"] if family in incremental else len(rows)
        materialized += len(rows)
        if scanned > policy.read_max_rows or materialized > policy.read_max_rows:
            raise IntegrityError(f"Gold source work exceeds its row budget while reading {family}")
        if family not in incremental:
            tables[family] = pa.Table.from_pylist(rows, schema=CONTRACTS[family].schema)
            lineages[family] = proofs
        controls = [
            confirmed_control(s, family)
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
            instants = (
                [
                    datetime.fromisoformat(stamp)
                    for part in incremental[family][0].get("parts", [])
                    if part["partition"][0] == origin
                    for stamp in part["time_bounds"]
                ]
                if family in incremental and "FECHA" in CONTRACTS[family].schema.names
                else [r["FECHA"] for r in rows if r.get("origen_fenix") == origin and "FECHA" in r]
            )
            if instants:
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
        confirmed_control(s, "ASIGNACION_PRODUCCION_RAMAL")
        for s in snapshots
        if "ASIGNACION_PRODUCCION_RAMAL" in s.manifest.get("controls", {})
    ]
    published = [p["extra"]["association"] for p in assoc if p["extra"].get("association")]
    accepted_registry = registry(published)
    catalogs = [
        confirmed_control(s, "CATALOGO_PRODUCCIONES")["extra"]
        for s in snapshots
        if "CATALOGO_PRODUCCIONES" in s.manifest.get("controls", {})
    ]
    composition = {}
    for catalog in catalogs:
        for key, value in catalog.get("composition", {}).items():
            prior_production = composition.get(key)
            composition[key] = (
                {**value, "coverage": "REVIEW"}
                if prior_production is not None and prior_production != value
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
        "archive": archive_id,
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
    window = None
    if lane == "live":
        full_families = OPERATIONS - incremental_families
        retained = bool(
            incremental_families and previous and previous.document.get("retained_live")
        )
        if full_families:
            retained = (
                retain_live(
                    pipelines,
                    tables,
                    doc,
                    previous=previous,
                    families=full_families,
                    previous_proofs=prior_proofs,
                )
                or retained
            )
        doc["retained_live"] = retained or any(s["retained"] for s in reconciled.values())
        horizon = _source_horizon(coverage)
        if horizon:
            window = (hour_holding(horizon)[0], horizon)
        if incremental:
            # Attribution consumes only the active window; the immutable manifest keeps
            # the full logical facts. Read through the original cache for untouched parts.
            for family, (ref, _, _) in incremental.items():
                view = Evidence(previous.root, previous.publication_id, {"tables": [ref]})
                view._fragments, view.read_stats = previous._fragments, previous.read_stats
                view.index = previous.index
                tables[family] = (
                    pa.Table.from_batches(
                        view.batches(family, max_rows=policy.read_max_rows, window=window),
                        schema=CONTRACTS[family].schema,
                    )
                    if window
                    else pa.Table.from_pylist([], schema=CONTRACTS[family].schema)
                )
                materialized += tables[family].num_rows
                if materialized > policy.read_max_rows:
                    raise IntegrityError("Gold attribution exceeds its materialization budget")
        evidence_decoded = (
            sum(counts["rows_decoded"] for counts in previous.read_stats.values())
            if previous
            else 0
        )
        if scanned + evidence_decoded > policy.read_max_rows:
            raise IntegrityError("Gold export exceeds its decoded work budget")
        if horizon:
            evidence = recent_relations(
                storage, policy, config.encoding, horizon, dict(config.sources)
            )
            if evidence:
                apply_series_handover(doc, tables, evidence, window, policy.window_seconds)
        doc["attribution_window"] = portable(window)
        doc.setdefault("series_handover_scopes", [])
        doc["silver_refs"] = silver_refs
        doc["fragment_lineage"] = policy.window_seconds
        doc["silver_read"] = {
            "incremental": bool(incremental_families),
            "fragment_incremental_families": sorted(incremental),
            "lineage_bootstrap": bool(migrating),
            "full_read_families": sorted(full_families),
            "reconciliation": reconciled,
            "rows": scanned,
            "decoded_evidence_rows": evidence_decoded,
            "materialized_rows": materialized,
            "logical_rows": logical_rows,
            "index_pages_read": previous.index.pages_read if previous else 0,
            "index_pages_written": previous.index.pages_written if previous else 0,
            "referenced_operational_rows": sum(ref["rows"] for ref, _, _ in incremental.values()),
            "reused_catalogues": reused_catalogues,
            "previous_evidence_reads": dict(previous.read_stats) if previous else {},
        }
    tables.update(attribution(doc, tables, window=window))
    tables.update(auxiliary_tables)
    doc["bronze_cursor"] = cursor
    doc["source_vector"] = vector
    for family in incremental:
        del tables[family]
    return save(
        storage.gold / "inputs",
        doc,
        tables,
        window_seconds=policy.window_seconds if lane == "live" else None,
        reuse=previous if lane == "live" else None,
        fact_refs=(ref for ref, _, _ in incremental.values()),
        lineage_refs=(part for _, parts, _ in incremental.values() for part in parts),
    )


def cutoff(evidence):
    """Industrial cutoff follows the latest confirmed Fenix block, even if stale."""
    horizon = _source_horizon(evidence.manifest["coverage"])
    if horizon is None:
        raise IntegrityError("Gold needs a confirmed source horizon")
    return horizon


def _source_horizon(coverage):
    values = [datetime.fromisoformat(r["live_until"]) for r in coverage if r.get("live_until")]
    if not values:
        values = [
            datetime.fromisoformat(r["covered_until"]) for r in coverage if r.get("covered_until")
        ]
    return max(values, default=None)


def published_evidence(root, publication_id=None):
    from .evidence import Evidence
    from .publications import Snapshot

    snapshot = Snapshot.open(root, publication_id=publication_id, require_gold_contract=True)
    descriptor = next(
        d for d in snapshot.manifest["dependencies"] if d.get("kind") == "independent-input"
    )
    return Evidence.open(root / "evidence", descriptor["evidence_id"])


def retain_live(
    pipelines, tables, document, *, previous=None, families=OPERATIONS, previous_proofs=None
):
    """Keep previously published live facts until historical Gold proves their copy."""
    from ingesta_delta_lake.pipeline_rows import business_key, review_hash

    from .evidence import Evidence

    root, budget = pipelines.live.gold / "inputs", pipelines.policy.read_max_rows
    if not (root / "current.json").exists():
        return False
    previous = previous or Evidence.open(root)
    proofs = previous.proofs(max_rows=budget) if previous_proofs is None else previous_proofs
    historical = None
    with suppress(OSError, ValueError, StopIteration):
        historical = published_evidence(pipelines.history.gold / "published")
    copied = historical.proofs(max_rows=budget, families=families) if historical else {}
    retained = False
    for family in families:
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
            retained = True
            current[key] = row
            document["lineages"].setdefault(family, {})[key] = old
        if len(current) > budget:
            raise IntegrityError("Unconfirmed Gold archive exceeds its explicit row budget")
        tables[family] = pa.Table.from_pylist(list(current.values()), schema=tables[family].schema)
    return retained
