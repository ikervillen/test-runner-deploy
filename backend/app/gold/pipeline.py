"""Local Gold orchestration, with explicit input version, resource budgets and cutoff."""

from __future__ import annotations

import multiprocessing
import os
import platform
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from errno import ENOTEMPTY
from pathlib import Path
from time import perf_counter, sleep
from typing import Any

import portalocker

from . import (
    boundaries,
    catalogs,
    context,
    contracts,
    coverage,
    dimensional,
    facts,
    lanes,
    mia_statistics,
    references,
)
from .ar_units import conflicts, record_episode
from .attribution import Attribution
from .common import (
    IntegrityError,
    PendingBasis,
    atomic_json,
    digest,
    immutable_json,
    read_json,
    utc_now,
)
from .coverage import summary as coverage_summary
from .evidence import Evidence, operational_expression
from .fingerprint import source_fingerprint
from .freshness import source_freshness
from .incremental import Prepared, window_key
from .metrics import Window, ar_bases, live_windows, mia_bases, production_windows, zc_bases
from .publications import CommitConflict, Publisher, Snapshot, commit_scope
from .schemas import KEYS, SCHEMAS, check_row
from .scoring import ScoreResult, calculate, validate_config
from .spill import FactStore


def _unique(name, rows):
    result = {}
    for r in rows:
        key = tuple(r[k] for k in KEYS[name])
        if key in result and result[key] != r:
            raise IntegrityError(f"{name}: different values for one output key")
        result[key] = r
    return [r for _, r in sorted(result.items(), key=lambda kv: repr(kv[0]))]


def _coverage_ready(snapshot, p, window, pending, required=None):
    from .common import live_epoch
    from .silver_coverage import covers

    floor = live_epoch()
    coverage_window = window
    if floor is not None and window.start < floor < window.cutoff:
        coverage_window = Window(
            window.production_id, window.kind, floor, window.end, window.cutoff, window.selection_id
        )

    required = required or {"LISTA_DEFECTOS", "LISTA_RECH", "LISTA_AR", "CONT_ZC"}
    scopes = [
        r
        for r in snapshot.manifest["coverage"]
        if r.get("origin") in {None, p["origen_fenix"]}
        and r.get("id_produccion") in {None, p["id_produccion"]}
    ]
    if not required.issubset({r["source"] for r in scopes if r["estado"] == "READY"}):
        return False
    for family in required:
        matching = [r for r in scopes if r["source"] == family and r["estado"] == "READY"]
        if not any(
            covers(r, coverage_window)
            if "confirmed_slots" in r
            else (
                (r.get("covered_from") is None or context.stamp(r["covered_from"]) <= window.start)
                and (
                    r.get("covered_until") is None
                    or window.cutoff <= context.stamp(r["covered_until"])
                )
            )
            for r in matching
        ):
            return False
    for r in [*scopes, *pending]:
        if r.get("source") not in required:
            continue
        if r.get("id_produccion") not in {None, p["id_produccion"]}:
            continue
        if r.get("origin") not in {None, p["origen_fenix"]}:
            continue
        if r.get("estado") == "READY":
            continue
        left, right = r.get("period_start"), r.get("period_end")
        if left and datetime.fromisoformat(str(left)) >= window.cutoff:
            continue
        if right and datetime.fromisoformat(str(right)) <= window.start:
            continue
        return False
    return True


def _component_coverage(snapshot, production, window, pending):
    """Use each family's accredited zone, preserving unknown boundary states."""
    result = {}
    for component, families, zone, absent_code, coverage_code in (
        ("MIA", MIA_FAMILIES, "zf", "MIA_NOT_APPLICABLE", "MIA_SOURCE_COVERAGE_PENDING"),
        ("ZC", {"CONT_ZC"}, "zc", "ZC_NOT_APPLICABLE", "ZC_SOURCE_COVERAGE_PENDING"),
        ("AR", {"LISTA_AR"}, None, None, "AR_SOURCE_COVERAGE_PENDING"),
    ):
        start, cutoff = window.start, window.cutoff
        if zone:
            began = production.get(f"fecha_inicio_{zone}")
            finished = production.get(f"fecha_fin_{zone}")
            if (began is not None and began >= cutoff) or (
                finished is not None and finished <= start
            ):
                result[component] = absent_code
                continue
            start = max(start, began or start)
            cutoff = min(cutoff, finished or cutoff)
        effective = Window(window.production_id, window.kind, start, window.end, cutoff)
        if not _coverage_ready(snapshot, production, effective, pending, families):
            result[component] = coverage_code
    return result


SCORE_TABLES = (
    "quality_scores",
    "quality_score_components",
    "mia_score_details",
    "aql_score_details",
    "dar_score_details",
    "zc_score_details",
)
REUSED_TABLES = (
    *SCORE_TABLES,
    "production_calculation_state",
    "mia_reference_sets",
    "mia_reference_members",
    "mia_reference_values",
    "mia_window_exposures",
    "mia_window_bases",
    "inspection_bases",
)
REFERENCE_TABLES = ("mia_reference_sets", "mia_reference_members", "mia_reference_values")
DIMENSION_TABLES = ("dim_machine", "dim_model", "dim_defect", "dim_date")
MIA_FAMILIES = frozenset({"LISTA_DEFECTOS", "LISTA_RECH"})
LIVE_FAMILIES = frozenset({"LISTA_RECH", "LISTA_DEFECTOS", "LISTA_AR", "CONT_ZC"})
# Scoring runs outside this wait. The pointer swap is the only shared step.
COMMIT_WAIT_SECONDS = 30
ZC_LINK_STATUSES = {
    "ASSIGNED",
    "SHARED_MACHINE",
    "TRANSITION_TO_OUTGOING",
    "OLD_PLUS_PREPARATION",
    "NEW_PLUS_PREPARATION",
}


class _Stopwatch:
    """Per-stage wall-clock seconds, reported with the incremental statistics."""

    def __init__(self):
        self.stages: dict[str, float] = {}
        self.calls: dict[str, int] = {}

    @contextmanager
    def stage(self, name: str):
        started = perf_counter()
        try:
            yield
        finally:
            elapsed = perf_counter() - started
            self.stages[name] = round(self.stages.get(name, 0.0) + elapsed, 6)
            self.calls[name] = self.calls.get(name, 0) + 1


class _SourceReader:
    """Bounded reads of one input publication; the total budget covers every table."""

    def __init__(self, snapshot: Snapshot, *, max_rows_per_source: int, max_total_rows: int):
        self.snapshot = snapshot
        self.max_rows_per_source = max_rows_per_source
        self.max_total_rows = max_total_rows
        self.seen_rows = 0
        self._cache: dict[str, list[dict]] = {}
        self._names = {r["name"] for r in snapshot.manifest["tables"]}

    def count(self, rows: int) -> None:
        self.seen_rows += rows
        if self.seen_rows > self.max_total_rows:
            raise IntegrityError("Gold input scope exceeds row budget; split by origin/period")

    def release(self, name: str) -> None:
        self._cache.pop(name, None)

    def rows(self, name: str, optional: bool = False) -> list[dict]:
        if name not in self._cache:
            if name not in self._names and optional:
                return []
            # One Silver table is not a query scope: the total budget is the guard.
            self._cache[name] = self.snapshot.rows(name, max_rows=self.max_total_rows)
            self.count(len(self._cache[name]))
        return self._cache[name]

    def batches(self, name: str, *, window=None):
        expression = operational_expression(name, window)
        options: dict[str, Any] = {"window": window} if isinstance(self.snapshot, Evidence) else {}
        for batch in self.snapshot.batches(
            name,
            max_rows=self.max_total_rows,
            expression=expression,
            pushdown=expression is not None,
            **options,
        ):
            self.count(batch.num_rows)
            yield batch


def _last_discovery(snapshot: Snapshot) -> datetime | None:
    # Reading a frozen publication is not a fresh discovery of live Fenix files.
    discoveries = [
        r.get("last_successful_discovery")
        for r in snapshot.manifest["coverage"]
        if r.get("source") in LIVE_FAMILIES
    ]
    if not discoveries or not all(discoveries):
        return None
    return min(datetime.fromisoformat(d).replace(tzinfo=None) for d in discoveries)


def _context_tables(snapshot, reader, bundle, previous, recalculate_closed_ids):
    """Productions, capacity, catalogs and assignments. Returns (tables, pending, decisions)."""
    catalog_rows = reader.rows("CATALOGO_PRODUCCIONES")
    boundary_rows, pending = boundaries.derive(
        catalog_rows, reader.rows("hot_rejection_evidence", True), bundle.get("physical_links", [])
    )
    prods, issues = context.productions(
        catalog_rows,
        context.catalog_mp9(
            reader.rows("MP9_CAMBIO_MODELO"), getattr(snapshot, "document", {}).get("bundle", {})
        ),
        reader.rows("SIP_MODELO"),
        reader.rows("SIP_MAQUINA_MOLDEO"),
        boundary_rows,
    )
    if recalculate_closed_ids and recalculate_closed_ids - {p["id_produccion"] for p in prods}:
        raise IntegrityError("Explicit recalculation refers to unknown productions")
    pending.extend(issues)
    capacities, issues = context.capacity(
        prods,
        reader.rows("production_parameters", True),
        reader.rows("machine_gobs", True),
        reader.rows("gobs_dictionary", True),
    )
    pending.extend(issues)
    if previous and "production_capacity" in {r["name"] for r in previous.manifest["tables"]}:
        capacities = context.capacity_revisions(capacities, previous.rows("production_capacity"))
    catalog_bundle = {k: v for k, v in bundle.items() if k != "physical_links"}
    tables, issues = catalogs.build(snapshot, catalog_bundle)
    pending.extend(issues)
    tables["productions"] = prods
    tables["production_capacity"] = capacities
    tables["production_assignments"] = context.assignments(
        reader.rows("ASIGNACION_PRODUCCION_RAMAL"), prods, reader.rows("zc_presences", True)
    )
    return tables, pending, catalog_bundle


def _check_assignment(name, row, assigned):
    identity = row["id_asignacion"]
    if identity is not None and assigned.get(identity) != row["id_produccion"]:
        raise IntegrityError(
            f"{name}: attribution does not reference its accepted production assignment"
        )


def _in_chunk(spans, production_id: str, start, end) -> bool:
    """No span means the full production. An interval that crosses the hour is kept whole."""
    if spans is None:
        return True
    return any(chunk.overlaps(start, end) for chunk in spans.get(production_id, ()))


def _fact_window(spans):
    """The only Silver interval this chunk needs. None means the whole table."""
    if not spans:
        return None
    chunks = [chunk for group in spans.values() for chunk in group]
    if not chunks:
        return None
    return (
        min(chunk.read_from or chunk.start for chunk in chunks),
        max(chunk.cutoff for chunk in chunks),
    )


def _spill_facts(
    reader,
    tables,
    attribution,
    pending,
    store: FactStore,
    by_zc,
    link_rules,
    only_ids=None,
    spans=None,
    current_window=None,
):
    """Write accepted facts for this chunk. The scan keeps one Arrow batch, not the history."""
    assigned = {r["id_asignacion"]: r["id_produccion"] for r in tables["production_assignments"]}
    productions = {r["id_produccion"]: r for r in tables["productions"]}
    episodes: dict = {}

    def wanted(production_id: str) -> bool:
        return only_ids is None or production_id in only_ids

    window = current_window or _fact_window(spans)
    for target, family in (("mia_rejections", "LISTA_DEFECTOS"), ("mia_inspections", "LISTA_RECH")):
        for batch in reader.batches(family, window=window):
            rows, issues = facts.mia(family, batch.to_pylist(), attribution)
            for row in rows:
                if not wanted(row["id_produccion"]) or not _in_chunk(
                    spans, row["id_produccion"], row["periodo_inicio"], row["periodo_fin"]
                ):
                    continue
                _check_assignment(target, row, assigned)
                store.add(
                    target,
                    row["id_produccion"],
                    row["id_observacion"],
                    row,
                    key=tuple(row[k] for k in KEYS[target]),
                )
            pending.extend(issues)
    for batch in reader.batches("LISTA_AR", window=window):
        rows, issues = facts.ar(
            batch.to_pylist(),
            attribution,
            tables["defect_catalog"],
            tables["ar_status_catalog"],
            tables["ar_type_catalog"],
            tables["dar_profile_members"],
            tables["model_segment_catalog"],
        )
        for row in rows:
            record_episode(episodes, row, productions[row["id_produccion"]])
            if not wanted(row["id_produccion"]):
                continue
            if not _in_chunk(spans, row["id_produccion"], row["fecha_on"], row["fecha_off"]):
                continue
            _check_assignment("ar_intervals", row, assigned)
            store.add(
                "ar_intervals",
                row["id_produccion"],
                row["id_observacion"],
                row,
                key=tuple(row[k] for k in KEYS["ar_intervals"]),
            )
        pending.extend(issues)
    linked = defaultdict(list)
    for production_id, observations in by_zc.items():
        for observation_id in observations:
            linked[observation_id].append(production_id)
    for batch in reader.batches("CONT_ZC", window=window):
        for row in facts.zc(batch.to_pylist()):
            observation_id = row["id_observacion"]
            owners = [
                production_id
                for production_id in linked.get(observation_id, ())
                if wanted(production_id)
                and _in_chunk(spans, production_id, row["periodo_inicio"], row["periodo_fin"])
            ]
            if (
                (only_ids is not None or spans is not None)
                and not owners
                and current_window is None
            ):
                continue
            store.add(
                "zc_observations",
                "",
                observation_id,
                row,
                key=tuple(row[k] for k in KEYS["zc_observations"]),
            )
            for production_id in owners:
                store.add("zc_for_production", production_id, observation_id, row)
                store.add(
                    "zc_production_links",
                    production_id,
                    observation_id,
                    {
                        "id_produccion": production_id,
                        "id_observacion": observation_id,
                        "regla_atribucion": link_rules[observation_id],
                    },
                    key=(production_id, observation_id),
                )
    store.ready()
    return conflicts(episodes)


def _zc_links(decisions, tables):
    """Producer-accepted counter links; never inferred from code coincidence."""
    by_zc = defaultdict(set)
    rules = {}
    tables.setdefault("zc_production_links", [])
    for d in decisions:
        if d["source_family"] != "CONT_ZC" or d["status"] not in ZC_LINK_STATUSES:
            continue
        rules[d["source_id"]] = d["rule"]
        for identity in d["productions"]:
            by_zc[identity].add(d["source_id"])
    return by_zc, rules


def _previous_tables(previous, max_rows_per_source, names):
    if previous is None:
        return {}
    available = {r["name"] for r in previous.manifest["tables"]}
    return {
        name: previous.rows(name, max_rows=max_rows_per_source)
        for name in names
        if name in available
    }


def _score_workers() -> int:
    raw = os.environ.get("GOLD_SCORE_WORKERS", "1")
    try:
        count = int(raw)
    except ValueError as exc:
        raise IntegrityError("GOLD_SCORE_WORKERS must be a positive integer") from exc
    if count < 1:
        raise IntegrityError("GOLD_SCORE_WORKERS must be a positive integer")
    if multiprocessing.current_process().daemon:
        return 1
    return count


def _prepared_for(store, production, capacities, by_zc, previous_states, old_tables, *, clock=None):
    """One production's rows. The caller drops this object before loading the next."""
    identity = production["id_produccion"]
    with clock.stage("sqlite_load") if clock else nullcontext():
        rows = {
            "mia_rejections": store.load("mia_rejections", identity),
            "mia_inspections": store.load("mia_inspections", identity),
            "ar_intervals": store.load("ar_intervals", identity),
            "zc_observations": store.load("zc_for_production", identity),
        }
    own_capacities = [r for r in capacities if r["id_produccion"] == identity]
    state = previous_states.get(identity)
    with clock.stage("prepare") if clock else nullcontext():
        prepared = Prepared(
            [production],
            rows,
            own_capacities,
            {identity: set(by_zc.get(identity, ()))},
            {identity: state} if state else {},
            old_tables,
        )
    return prepared, rows


class _CoverageView:
    """The publication fields a score needs. A Delta snapshot does not travel to a worker."""

    def __init__(self, publication_id, coverage_rows):
        self.publication_id = publication_id
        self.manifest = {"coverage": coverage_rows}


def _score_job(payload: dict) -> dict:
    """Score one production from its spilled rows and return only that production's output."""
    clock = _Stopwatch()
    windows_timing: list[dict] = []
    with clock.stage("sqlite_open"):
        store = FactStore(Path(payload["spill"]))
    try:
        prepared, fact_rows = _prepared_for(
            store,
            payload["production"],
            payload["capacities"],
            payload["by_zc"],
            payload["previous_states"],
            payload["old_tables"],
            clock=clock,
        )
    finally:
        store.close()
    production = payload["production"]
    identity = production["id_produccion"]
    pending = list(payload["pending"])
    raw_hash = prepared.raw_hashes[identity]
    preserve_closed = (
        production["estado_produccion"] == "CLOSED"
        and payload["previous_states"].get(identity, {}).get("raw_inputs_hash") == raw_hash
        and identity not in payload["recalculate_closed_ids"]
    )
    with clock.stage("reference"):
        ref = payload.get("mia_reference") or references.build(
            production,
            payload["productions"],
            payload["segments"],
            payload["rates"],
            input_version=payload["publication_id"],
            catalog_version=payload["catalog_version"],
            minimum=payload["minimum_samples"],
        )
    scope_row, production_cutoff = coverage.production_scope(
        production,
        payload["coverage"],
        ceiling=payload["cutoff"],
        previous=payload["previous_coverage"],
        families=LIVE_FAMILIES,
        evidence=payload["publication_id"],
        seen=payload["last_discovery"],
    )
    scope_rows = [scope_row]
    produced: dict[str, list] = {}
    if production_cutoff is not None:
        local = {
            "ar_intervals": fact_rows["ar_intervals"],
            "zc_observations": fact_rows["zc_observations"],
        }
        scope_rows.extend(
            _score_production(
                production,
                snapshot=_CoverageView(payload["publication_id"], payload["coverage"]),
                tables=local,
                prepared=prepared,
                capacities=payload["capacities"],
                by_zc=payload["by_zc"],
                segments=payload["segments"],
                ref=ref,
                live_references=payload.get("live_references"),
                config=payload["config"],
                config_id=payload["config_id"],
                cutoff=production_cutoff,
                calculation_time=payload["calculation_time"],
                pending=pending,
                preserve_closed=preserve_closed,
                previous_scores=payload["previous_scores"],
                old_tables=payload["old_tables"],
                last_discovery=payload["last_discovery"],
                windows=payload.get("windows"),
                ar_conflicts=payload.get("ar_conflicts", ()),
                mia_facts=(fact_rows["mia_inspections"], fact_rows["mia_rejections"]),
                live_mia="mia_reference" in payload,
                production_clock=clock,
                windows_timing=windows_timing,
            )
        )
        produced = {
            name: rows
            for name, rows in local.items()
            if name not in {"ar_intervals", "zc_observations"}
        }
    return {
        "state": {
            "id_produccion": identity,
            "raw_inputs_hash": raw_hash,
            "config_id": payload["config_id"],
        },
        "reference": {
            key: [
                row
                for group in [ref, *payload.get("live_references", {}).values()]
                for row in group[key]
            ]
            for key in ("sets", "members", "values")
        },
        "scope_rows": scope_rows,
        "tables": produced,
        "pending": pending[len(payload["pending"]) :],
        "reused": prepared.stats["mia_windows_reused"],
        "recalculated": prepared.stats["mia_windows_recalculated"],
        "unchanged": identity in prepared.unchanged,
        "timing": {"seconds": clock.stages, "calls": clock.calls, "windows": windows_timing},
    }


def _production_windows(p, cutoff):
    windows = production_windows(p, cutoff)
    for w in live_windows(p["id_produccion"], cutoff, delay=cutoff - cutoff):
        start = max(w.start, p["fecha_inicio_operativo_zc"] or w.start)
        end = min(w.cutoff, p["fecha_fin_zf"] or w.cutoff)
        if p["fecha_inicio_operativo_zc"] is not None and start < end:
            windows.append(Window(w.production_id, w.kind, start, w.end, end))
    return windows


def _preserved_result(window, previous_scores, old_tables):
    """A closed result with identical inputs keeps its published rows and keys."""
    return ScoreResult(
        {
            name: [r for r in old_tables.get(name, []) if r["id_resultado"] == window.identity]
            for name in SCORE_TABLES
        },
        (),
    )


def _scope_row(scope_id, production_id, source, origin, start, end, estado, reason, evidence, seen):
    return {
        "scope_id": scope_id,
        "id_produccion": production_id,
        "source": source,
        "origin": origin,
        "period_start": start,
        "period_end": end,
        "estado": estado,
        "reason_code": reason,
        "evidence_reference": evidence,
        "last_successful_discovery": seen,
        "confirmed_source_version": None,
        "pending_files": None,
        "earliest_pending_period": None,
    }


def _score_production(
    p,
    *,
    snapshot,
    tables,
    prepared,
    capacities,
    by_zc,
    segments,
    ref,
    live_references,
    config,
    config_id,
    cutoff,
    calculation_time,
    pending,
    preserve_closed,
    previous_scores,
    old_tables,
    last_discovery,
    windows=None,
    ar_conflicts=(),
    mia_facts=(),
    live_mia=False,
    production_clock,
    windows_timing,
):
    """Window bases and scores for one production; returns its scope status rows."""
    scope_rows = []
    windows = list(windows) if windows is not None else _production_windows(p, cutoff)
    if not windows:
        pending.append(context.issue("quality_scores", "NO_OPERATIVE_WINDOW", p["id_produccion"]))
    segment = segments.get(p["codigo_modelo_botella"], {})
    for w in windows:
        clock = _Stopwatch()
        with clock.stage("mia_bases"):
            m = prepared.mia(w)
        tables.setdefault("inspection_bases", []).extend(m["inspection_bases"])
        tables.setdefault("mia_window_exposures", []).append(m["exposure"])
        tables.setdefault("mia_window_bases", []).extend(m["amounts"])
        ar_pending_reason = "ASSIGNED_CAPACITY_PENDING"
        try:
            with clock.stage("ar_bases"):
                a = ar_bases(
                    tables["ar_intervals"],
                    capacities,
                    w,
                    production=p,
                    conflicts=ar_conflicts,
                    independent=live_mia,
                )
        except PendingBasis as exc:
            a = None
            ar_pending_reason = exc.code
        if a:
            tables.setdefault("ar_window_exposures", []).append(a["exposure"])
            tables.setdefault("ar_window_bases", []).extend(a["amounts"])
        with clock.stage("zc_bases"):
            z = zc_bases(tables["zc_observations"], w, by_zc[p["id_produccion"]])
        tables.setdefault("zc_window_bases", []).extend(z)
        with clock.stage("coverage"):
            component_coverage = _component_coverage(snapshot, p, w, pending) if live_mia else None
            if component_coverage is not None and w.cutoff < w.end:
                for component, groups in (("MIA", mia_facts), ("ZC", (tables["zc_observations"],))):
                    if component not in component_coverage and any(
                        row["periodo_inicio"] < w.cutoff < row["periodo_fin"]
                        for rows in groups
                        for row in rows
                    ):
                        component_coverage[component] = f"{component}_TRANSITION_SAMPLE_UNRESOLVED"
            ready = live_mia or _coverage_ready(snapshot, p, w, pending)
        if (
            not live_mia
            and preserve_closed
            and w.identity in previous_scores
            and ready
            and all(previous_scores[w.identity][k] == v for k, v in w.key.items())
        ):
            with clock.stage("preserved_result"):
                result = _preserved_result(w, previous_scores, old_tables)
        else:
            with clock.stage("calculate"):
                result = calculate(
                    w,
                    mia=m,
                    ar=a,
                    zc=z,
                    reference=live_references.get(w.cutoff, ref)
                    if live_references is not None
                    else ref,
                    config=config,
                    config_id=config_id,
                    profile_confirmed=segment.get("estado_correspondencia") == "CONFIRMED"
                    and segment.get("estado_dar") != "PENDING",
                    configuration_status="PROVISIONAL_DAR"
                    if segment.get("estado_dar") == "PROVISIONAL"
                    else "CONFIRMED",
                    coverage_ready=ready,
                    calculation_time=calculation_time,
                    line=p["linea"],
                    ar_pending_reason=ar_pending_reason,
                    live_mia=live_mia,
                    component_coverage=component_coverage,
                )
        for name, seconds in clock.stages.items():
            production_clock.stages[name] = round(
                production_clock.stages.get(name, 0.0) + seconds, 6
            )
            production_clock.calls[name] = production_clock.calls.get(name, 0) + clock.calls[name]
        windows_timing.append(
            {
                "start": w.start.isoformat(),
                "cutoff": w.cutoff.isoformat(),
                "seconds": clock.stages,
                "total_seconds": sum(clock.stages.values()),
            }
        )
        windows_timing.sort(key=lambda row: row["total_seconds"], reverse=True)
        del windows_timing[8:]
        for name, rows in result.tables.items():
            tables.setdefault(name, []).extend(rows)
        scope_rows.append(
            _scope_row(
                w.identity,
                p["id_produccion"],
                "quality_scores",
                p["origen_fenix"],
                w.start,
                w.cutoff,
                "WAITING_DEPENDENCY" if result.pending else "READY",
                ";".join(result.pending) if result.pending else None,
                snapshot.publication_id,
                last_discovery,
            )
        )
    return scope_rows


def _pending_scope_rows(pending, snapshot, last_discovery):
    return [
        _scope_row(
            digest(p),
            p.get("id_produccion"),
            p["source"],
            p.get("origin"),
            None,
            None,
            p["estado"],
            p.get("reason_code"),
            p.get("source_id", snapshot.publication_id),
            last_discovery,
        )
        for p in pending
    ]


_SPILLED = object()


class SpilledTables(dict):
    """Small tables stay here. A family that grows with history is read back from the store."""

    def __init__(self, store: FactStore, directory):
        super().__init__()
        self.store = store
        self.spilled: set[str] = set()
        self._directory = directory
        self._closed = False
        self.replace_ids: set[str] | None = None
        self.hours: tuple[tuple[str, datetime], ...] | None = None
        self.fact_bounds: tuple[tuple[str, datetime, datetime], ...] | None = None
        self.result_ids: tuple[str, ...] | None = None

    def mark(self, name: str) -> None:
        self.spilled.add(name)
        super().__setitem__(name, _SPILLED)

    def __getitem__(self, key):
        value = super().__getitem__(key)
        if value is _SPILLED:
            # A caller that inspects or edits a family owns that list from then on.
            loaded = list(self.store.iter_rows(key))
            super().__setitem__(key, loaded)
            self.spilled.discard(key)
            return loaded
        return value

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.store.close()
        finally:
            for attempt in range(3):
                try:
                    self._directory.cleanup()
                    break
                except OSError as exc:
                    # Windows may briefly retain a deleted SQLite sidecar.
                    if exc.errno != ENOTEMPTY or attempt == 2:
                        raise
                    sleep(0.05)

    def __del__(self) -> None:
        self.close()


def _store_row(store: FactStore, name: str, production_id, row: dict) -> None:
    row = check_row(name, row)
    key = tuple(row[k] for k in KEYS[name])
    store.add(name, str(production_id or ""), str(key[0]), row, key=key)


def _history(previous, production_id: str, max_rows: int, *, clock=None, prune=False):
    """Previous rows for one production. A later pass never reloads the whole publication."""
    if previous is None:
        return {}, {}, {}
    import pyarrow.dataset as ds

    available = {r["name"] for r in previous.manifest["tables"]}

    def load(name, field, keys):
        with clock.stage(name) if clock else nullcontext():
            return previous.rows(
                name,
                expression=ds.field(field).isin(keys),
                max_rows=max_rows,
                file_pruning_predicate=[(field, "in", keys)] if prune else None,
            )

    old = {}
    for name in REUSED_TABLES:
        if name not in available or "id_produccion" not in SCHEMAS[name].names:
            continue
        old[name] = load(name, "id_produccion", [production_id])
    scores = {r["id_resultado"]: r for r in old.get("quality_scores", [])}
    result_ids = list(scores)
    for name in SCORE_TABLES:
        if name in old or name not in available:
            continue
        old[name] = load(name, "id_resultado", result_ids) if result_ids else []
    states = {r["id_produccion"]: r for r in old.get("production_calculation_state", [])}
    return old, states, scores


class _SharedHistoryBudget(Exception):
    """The selected projections cannot be held within a single cycle's row budget."""


def _shared_history(previous, windows, max_rows, *, clock, decoded):
    """Load only the inputs that the independent Live MIA reuse path consumes."""
    import pyarrow as pa
    import pyarrow.dataset as ds

    old: dict[str, dict[str, list[dict]]] = {identity: {} for identity in windows}
    available = {r["name"] for r in previous.manifest["tables"]}
    remaining = max_rows
    batch_size = 8192

    def load(name, field, owners, *, filter_=None, pruning=(), accepts=None):
        nonlocal remaining
        if not owners or name not in available:
            return
        keys = list(owners)
        expression = ds.field(field).isin(keys)
        if filter_ is not None:
            expression &= filter_

        def note_decode(count):
            decoded[name] += count

        with clock.stage(name):
            for batch in previous.batches(
                name,
                expression=expression,
                file_pruning_predicate=[(field, "in", keys), *pruning],
                batch_size=batch_size,
                max_rows=max_rows + batch_size,
                on_decode=note_decode,
            ):
                remaining -= batch.num_rows
                if remaining < 0:
                    raise _SharedHistoryBudget
                for row in batch.to_pylist():
                    if accepts is None or accepts(row):
                        old[owners[row[field]]].setdefault(name, []).append(row)

    productions = {identity: identity for identity in windows}
    load("production_calculation_state", "id_produccion", productions)
    wanted = {window_key(w.key): w for group in windows.values() for w in group}
    if wanted:
        cutoffs = sorted({w.cutoff for w in wanted.values()})
        load(
            "mia_window_exposures",
            "id_produccion",
            productions,
            filter_=ds.field("corte").isin(pa.array(cutoffs, type=pa.timestamp("us"))),
            pruning=(("corte", ">=", cutoffs[0]), ("corte", "<=", cutoffs[-1])),
            accepts=lambda r: window_key(r) in wanted,
        )
    exposures = defaultdict(list)
    basis_owners = {}
    for identity, tables in old.items():
        for row in tables.get("mia_window_exposures", []):
            basis = row["inspected_basis_id"]
            if basis in basis_owners and basis_owners[basis] != identity:
                raise IntegrityError("A published MIA basis belongs to multiple productions")
            basis_owners[basis] = identity
            exposures[identity].append(wanted[window_key(row)])
    load("mia_window_bases", "inspected_basis_id", basis_owners)
    if exposures:
        start = min(w.start for group in exposures.values() for w in group)
        end = max(w.cutoff for group in exposures.values() for w in group)
        load(
            "inspection_bases",
            "id_produccion",
            {identity: identity for identity in exposures},
            filter_=(ds.field("periodo_inicio") >= start) & (ds.field("periodo_fin") <= end),
            pruning=(("periodo_inicio", ">=", start), ("periodo_fin", "<=", end)),
            accepts=lambda r: any(
                r["seleccion_id"] == w.selection_id and w.includes_sample(r)
                for w in exposures[r["id_produccion"]]
            ),
        )
    return {
        identity: (
            tables,
            {r["id_produccion"]: r for r in tables.get("production_calculation_state", [])},
            {},
        )
        for identity, tables in old.items()
    }


def _carry_references(previous, store: FactStore, max_rows: int, *, exclude=()) -> None:
    """Keep reference rows a preserved score still points at."""
    if previous is None:
        return
    available = {r["name"] for r in previous.manifest["tables"]}
    for name in REFERENCE_TABLES:
        if name not in available:
            continue
        for row in previous.rows(name, max_rows=max_rows):
            if row["reference_id"] not in exclude:
                _store_row(store, name, row.get("id_produccion") or "", row)


def _reference_rows(snapshot, name, max_rows):
    if snapshot is None or name not in {ref["name"] for ref in snapshot.manifest["tables"]}:
        return []
    return snapshot.rows(name, max_rows=max_rows)


def _live_mia_samples(snapshot, store, selected, windows, pending):
    denominators = defaultdict(int)
    counts = defaultdict(lambda: defaultdict(int))
    for p in selected:
        model = p["codigo_modelo_botella"]
        if not model:
            continue
        pid = p["id_produccion"]
        rejections = store.load("mia_rejections", pid)
        inspections = store.load("mia_inspections", pid)
        for window in windows[pid]:
            if not _coverage_ready(snapshot, p, window, pending, MIA_FAMILIES):
                continue
            basis = mia_bases(rejections, inspections, window)
            inspected = basis["exposure"]["inspeccionadas"]
            if inspected <= 0:
                continue
            denominators[model] += inspected
            for row in basis["rates"]:
                counts[model][row["espacio_defecto"], row["codigo_defecto"]] += row["rechazos"]
    return {
        model: {key: 100 * rejected / inspected for key, rejected in counts[model].items()}
        for model, inspected in denominators.items()
    }


def _previous_coverage(previous, max_rows: int) -> dict:
    if previous is None:
        return {}
    import pyarrow.dataset as ds

    available = {r["name"] for r in previous.manifest["tables"]}
    if "publication_scope_status" not in available:
        return {}
    return coverage.published_until(
        previous.rows(
            "publication_scope_status",
            expression=ds.field("source") == "coverage",
            max_rows=max_rows,
        )
    )


def _stored_rates(previous, production_id: str, max_rows: int) -> list[dict] | None:
    """Reference rates already published for one production. Other chunks are not reloaded."""
    if previous is None:
        return None
    import pyarrow.dataset as ds

    available = {r["name"] for r in previous.manifest["tables"]}
    if "mia_window_exposures" not in available or "mia_window_bases" not in available:
        return None
    exposures = previous.rows(
        "mia_window_exposures",
        expression=(ds.field("id_produccion") == production_id)
        & (ds.field("tipo_ventana") == "PRODUCTION"),
        max_rows=max_rows,
    )
    if not exposures:
        return None
    exposure = max(exposures, key=lambda row: row["corte"])
    inspected = exposure["inspeccionadas"]
    if not inspected:
        return None
    bases = previous.rows(
        "mia_window_bases",
        expression=(ds.field("id_produccion") == production_id)
        & (ds.field("inspected_basis_id") == exposure["inspected_basis_id"]),
        max_rows=max_rows,
    )
    return [
        {
            "espacio_defecto": row["espacio_defecto"],
            "codigo_defecto": row["codigo_defecto"],
            "rechazos": row["rechazos"],
            "inspeccionadas": inspected,
            "percent": 100 * row["rechazos"] / inspected,
        }
        for row in bases
        if row["rechazos"] > 0
    ]


def _naive(value) -> datetime | None:
    value = context.stamp(value) if value is not None else None
    if value is not None and value.tzinfo is not None:
        return value.replace(tzinfo=None)
    return value


def _ar_version(snapshot) -> int | None:
    for table in snapshot.manifest.get("tables", []):
        if table.get("name") == "LISTA_AR":
            return table.get("delta_version")
    return None


def _hour_key(production_id: str, start: datetime) -> str:
    return f"{production_id}|{start.isoformat()}"


def _recorded_ar(previous) -> dict:
    if previous is None:
        return {}
    path = previous.root / "lanes" / "ar-hours.json"
    if not path.exists():
        return {}
    hours = read_json(path).get("hours", {})
    return hours if isinstance(hours, dict) else {}


def _stops_to_reopen(snapshot, previous, productions, stored_hours, ar_version):
    """Stored hours a newly arrived stop crosses. Unchanged AR versions are not reread."""
    if ar_version is None:
        return set(), []
    recorded = _recorded_ar(previous)
    pending = []
    for production_id, hours in stored_hours.items():
        for start in hours:
            key = _hour_key(production_id, start)
            if recorded.get(key) != ar_version:
                pending.append((production_id, start, key))
    if not pending:
        return set(), []
    try:
        intervals = _ar_intervals(snapshot)
    except IntegrityError:
        return set(), []
    by_id = {production["id_produccion"]: production for production in productions}
    overlapped = set()
    for production_id, start, _key in pending:
        production = by_id.get(production_id)
        if production is None:
            continue
        origin = production.get("origen_fenix")
        end = start + timedelta(hours=1)
        for row_start, row_end, row_origin in intervals:
            if row_origin != origin or row_start is None:
                continue
            finish = row_end if row_end is not None else end
            if row_start < end and finish > start:
                overlapped.add((production_id, start))
                break
    reopen = {
        (production_id, start)
        for production_id, start, _key in pending
        if (production_id, start) in overlapped
    }
    # Hours the stop does not cross are remembered. The crossed ones stay open
    # until the lane that owns them writes them.
    return reopen, [
        key for production_id, start, key in pending if (production_id, start) not in reopen
    ]


def _ar_intervals(snapshot):
    rows = snapshot.rows(
        "LISTA_AR",
        columns=["origen_fenix", "FECHA_ON", "FECHA_OFF"],
        max_rows=1_000_000,
    )
    intervals = []
    for row in rows:
        start = context.stamp(row["FECHA_ON"])
        if start is None:
            continue
        intervals.append((start, context.stamp(row["FECHA_OFF"]), row["origen_fenix"]))
    return intervals


def _remember_ar(output_root: Path, statistics: dict) -> None:
    version = statistics.get("ar_version")
    keys = statistics.get("ar_recorded") or []
    if version is None or not keys:
        return
    path = output_root / "lanes" / "ar-hours.json"
    current = read_json(path).get("hours", {}) if path.exists() else {}
    if not isinstance(current, dict):
        current = {}
    for key in keys:
        current[key] = version
    atomic_json(path, {"hours": current})


def _stored_hours(previous, max_rows: int) -> dict[str, dict[datetime, datetime]]:
    """Clock hours already stored, keyed by production and hour start."""
    if previous is None:
        return {}
    import pyarrow.dataset as ds

    available = {r["name"] for r in previous.manifest["tables"]}
    if "quality_scores" not in available:
        return {}
    stored: dict[str, dict[datetime, datetime]] = {}
    for row in previous.rows(
        "quality_scores",
        expression=ds.field("tipo_ventana") == "HOUR",
        max_rows=max_rows,
    ):
        start, corte = _naive(row["inicio"]), _naive(row["corte"])
        if start is None or corte is None:
            continue
        current = stored.setdefault(row["id_produccion"], {}).get(start)
        if current is None or corte > current:
            stored[row["id_produccion"]][start] = corte
    return stored


def _reference_rates(previous, production: dict, max_rows: int) -> list[dict] | None:
    """A peer's rate from its stored production window, or from every stored hour once complete."""
    identity = production["id_produccion"]
    stored = _stored_rates(previous, identity, max_rows)
    if stored:
        return stored
    finished = _naive(production.get("fecha_fin_zf"))
    if previous is None or production.get("estado_produccion") != "CLOSED" or finished is None:
        return None
    expected = lanes.expected_hours(production, finished)
    if not expected:
        return None
    import pyarrow.dataset as ds

    available = {r["name"] for r in previous.manifest["tables"]}
    if "mia_window_exposures" not in available or "mia_window_bases" not in available:
        return None
    exposures = previous.rows(
        "mia_window_exposures",
        expression=(ds.field("id_produccion") == identity) & (ds.field("tipo_ventana") == "HOUR"),
        max_rows=max_rows,
    )
    latest: dict[datetime, tuple[datetime, dict]] = {}
    for row in exposures:
        start, corte = _naive(row["inicio"]), _naive(row["corte"])
        if start is None or corte is None:
            continue
        current = latest.get(start)
        if current is None or corte >= current[0]:
            latest[start] = (corte, row)
    chosen = []
    for chunk in expected:
        have = latest.get(chunk.start)
        if have is None or have[0] < chunk.cutoff:
            return None
        chosen.append(have[1])
    inspected = sum(row["inspeccionadas"] for row in chosen)
    if not inspected:
        return None
    basis_ids = {row["inspected_basis_id"] for row in chosen}
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for row in previous.rows(
        "mia_window_bases",
        expression=(ds.field("id_produccion") == identity) & (ds.field("tipo_ventana") == "HOUR"),
        max_rows=max_rows,
    ):
        if row["inspected_basis_id"] not in basis_ids or row["rechazos"] <= 0:
            continue
        totals[(row["espacio_defecto"], row["codigo_defecto"])] += row["rechazos"]
    return [
        {
            "espacio_defecto": space,
            "codigo_defecto": code,
            "rechazos": rejected,
            "inspeccionadas": inspected,
            "percent": 100 * rejected / inspected,
        }
        for (space, code), rejected in sorted(totals.items())
    ]


def build(
    snapshot: Snapshot,
    *,
    bundle: dict,
    config: dict,
    cutoff: datetime,
    calculation_time: datetime,
    max_rows_per_source: int = 5_000_000,
    max_total_rows: int = 20_000_000,
    previous: Snapshot | None = None,
    statistics: dict | None = None,
    recalculate_closed_ids: set[str] | None = None,
    lane: str = "all",
    since: datetime | None = None,
    copied_until: dict[str, datetime] | None = None,
    reference_snapshot=None,
    spill_root: Path | None = None,
    spill_db_bytes: int | None = None,
    current_slot_seconds: int | None = None,
    mia_reference_mode: str | None = None,
    validate_result: bool = True,
    review_hour: lanes.HourChunk | None = None,
) -> tuple[SpilledTables | None, list[dict]]:
    """Score one lane. `all` is the manual full build; live and history score a chunk."""
    validate_config(config)
    recalculate_closed_ids = recalculate_closed_ids or set()
    clock = _Stopwatch()
    reader = _SourceReader(
        snapshot, max_rows_per_source=max_rows_per_source, max_total_rows=max_total_rows
    )
    last_discovery = _last_discovery(snapshot)

    with clock.stage("context"):
        tables, pending, catalog_bundle = _context_tables(
            snapshot, reader, bundle, previous, recalculate_closed_ids
        )
        prods, capacities = tables["productions"], tables["production_capacity"]
        decisions = reader.rows("observation_assignments")
    score_stats: dict[str, Any] = {
        "mia_windows_reused": 0,
        "mia_windows_recalculated": 0,
        "unchanged_productions": 0,
        "productions_by_origin": {
            origin: sum(p["origen_fenix"] == origin for p in prods)
            for origin in sorted({p["origen_fenix"] for p in prods if p["origen_fenix"]})
        },
        "score_workers": _score_workers(),
        "lane": lane,
    }
    previous_coverage = _previous_coverage(previous, max_total_rows)
    stored_hours = (
        {}
        if lane == "all" or current_slot_seconds is not None
        else _stored_hours(previous, max_total_rows)
    )
    ar_version = None if lane == "all" else _ar_version(snapshot)
    reopen, checked = (
        _stops_to_reopen(snapshot, previous, prods, stored_hours, ar_version)
        if current_slot_seconds is None
        else (set(), [])
    )
    hour_plan = (
        None
        if lane == "all" or current_slot_seconds is not None
        else lanes.plan(
            prods,
            stored_hours,
            snapshot.manifest["coverage"],
            cutoff,
            lane,
            families=LIVE_FAMILIES,
            reopen=reopen,
            since=since,
            copied_until=copied_until,
        )
    )
    if current_slot_seconds is not None:
        hour_plan = lanes.current_slot(prods, cutoff, current_slot_seconds)
        if isinstance(snapshot, Evidence):
            closed = set(snapshot.document.get("closed_hours", []))
            hour_plan = [
                c
                for c in hour_plan
                if Window(c.production_id, "HOUR", c.start, c.end, c.cutoff).identity not in closed
            ]
        current_keys = {(chunk.production_id, chunk.start) for chunk in hour_plan}
        if isinstance(snapshot, Evidence):
            for identity, left, right, accredited in snapshot.document.get("retry_closures", []):
                start = datetime.fromisoformat(left)
                if (identity, start) not in current_keys:
                    hour_plan.append(
                        lanes.HourChunk(
                            identity,
                            start,
                            datetime.fromisoformat(right),
                            datetime.fromisoformat(accredited),
                        )
                    )
                    current_keys.add((identity, start))
    if review_hour is not None:
        if not isinstance(snapshot, Evidence) or review_hour.cutoff > cutoff:
            raise IntegrityError("A dry-run hour needs pinned Gold evidence and an accredited cut")
        hour_plan = [review_hour]
    recorded = list(checked)
    if hour_plan:
        recorded.extend(_hour_key(chunk.production_id, chunk.start) for chunk in hour_plan or [])
    score_stats["ar_version"] = ar_version
    score_stats["ar_recorded"] = recorded
    chosen = None if hour_plan is None else {chunk.production_id for chunk in hour_plan}
    selected = prods if chosen is None else [p for p in prods if p["id_produccion"] in chosen]
    score_stats["productions_by_origin"] = {
        origin: sum(p["origen_fenix"] == origin for p in selected)
        for origin in sorted({p["origen_fenix"] for p in selected if p["origen_fenix"]})
    }
    score_stats["chunk_productions"] = sorted(p["id_produccion"] for p in selected)
    score_stats["chunk_hours"] = [] if not hour_plan else [c.start.isoformat() for c in hour_plan]
    score_stats["horizon_complete"] = lane == "live" and not hour_plan
    if chosen is not None and not selected and current_slot_seconds is None:
        if statistics is not None:
            statistics.update(score_stats)
            statistics["lane_status"] = "IDLE"
        return None, []
    if spill_root is not None:
        spill_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    spill_dir = tempfile.TemporaryDirectory(prefix="gold-facts-", dir=spill_root)
    try:
        store = FactStore(Path(spill_dir.name) / "facts.sqlite", max_bytes=spill_db_bytes)
    except BaseException:
        spill_dir.cleanup()
        raise
    published = SpilledTables(store, spill_dir)
    handed = False
    try:
        with clock.stage("facts"):
            by_zc, link_rules = _zc_links(decisions, tables)
            tables.pop("zc_production_links", None)
            spans = None
            if hour_plan is not None:
                spans = defaultdict(list)
                for chunk in hour_plan:
                    spans[chunk.production_id].append(chunk)
            ar_conflicts = _spill_facts(
                reader,
                tables,
                Attribution(decisions, prods),
                pending,
                store,
                by_zc,
                link_rules,
                only_ids=chosen,
                spans=spans,
                current_window=(review_hour.start, review_hour.cutoff)
                if review_hour
                else (
                    sorted({(c.read_from or c.start, c.cutoff) for c in hour_plan or []})
                    if isinstance(snapshot, Evidence) and snapshot.document.get("retry_closures")
                    else (lanes.hour_holding(cutoff)[0], cutoff)
                )
                if current_slot_seconds is not None
                else None,
            )
            del decisions
            reader.release("observation_assignments")
        if mia_reference_mode is None:
            with clock.stage("previous"):
                _carry_references(previous, store, max_total_rows)
        scoring = {p["id_produccion"] for p in selected}
        facts_only = bool(hour_plan) and all(chunk.facts_only for chunk in hour_plan or [])
        if facts_only:
            selected = []
        else:
            selected = [
                production
                for production in selected
                if hour_plan is None
                or any(
                    not chunk.facts_only and chunk.production_id == production["id_produccion"]
                    for chunk in hour_plan
                )
            ]
            scoring = {production["id_produccion"] for production in selected}
        rates = {}
        if not facts_only and mia_reference_mode != "live":
            for production in prods:
                # One hour is not a production rate. Peers come from stored slices.
                if hour_plan is not None or production["id_produccion"] not in scoring:
                    stored = _reference_rates(previous, production, max_total_rows)
                    if stored:
                        rates[production["id_produccion"]] = stored
                    continue
                windows = production_windows(production, cutoff)
                if not (windows and production["estado_produccion"] == "CLOSED"):
                    continue
                if (
                    mia_reference_mode == "history"
                    and windows[0].cutoff != production["fecha_fin_zf"]
                ):
                    continue
                old_tables, previous_states, _scores = _history(
                    previous, production["id_produccion"], max_total_rows
                )
                prepared, _rows = _prepared_for(
                    store, production, capacities, by_zc, previous_states, old_tables
                )
                base = prepared.mia(windows[0])
                score_stats["mia_windows_reused"] += prepared.stats["mia_windows_reused"]
                score_stats["mia_windows_recalculated"] += prepared.stats[
                    "mia_windows_recalculated"
                ]
                if base["exposure"]["inspeccionadas"] > 0 and _coverage_ready(
                    snapshot,
                    production,
                    windows[0],
                    pending,
                    {"LISTA_DEFECTOS", "LISTA_RECH"},
                ):
                    rates[production["id_produccion"]] = base["rates"]
                del prepared, _rows, old_tables
        if reference_snapshot is not None and mia_reference_mode != "live":
            for production in prods:
                identity = production["id_produccion"]
                if identity not in rates and production["estado_produccion"] == "CLOSED":
                    stored = _reference_rates(reference_snapshot, production, max_total_rows)
                    if stored:
                        rates[identity] = stored
        segments = {r["codigo_modelo_botella"]: r for r in tables["model_segment_catalog"]}
        config_id = digest([config, bundle])
        catalog_version = digest(catalog_bundle)
        scope_rows: list[dict] = []
        score_windows = defaultdict(list)
        for chunk in hour_plan or ():
            if not chunk.facts_only:
                score_windows[chunk.production_id].append(
                    Window(chunk.production_id, "HOUR", chunk.start, chunk.end, chunk.cutoff)
                )
        effective = {}
        if mia_reference_mode is not None:
            with clock.stage("previous"):
                minimum = int(config["mia"]["historical_scope"]["min_samples"])
                previous_sets = _reference_rows(previous, "mia_reference_sets", max_total_rows)
                if mia_reference_mode == "live":
                    with clock.stage("reference_history"):
                        historical = mia_statistics.historical_index(
                            _reference_rows(
                                reference_snapshot, "mia_reference_sets", max_total_rows
                            ),
                            _reference_rows(
                                reference_snapshot, "mia_reference_values", max_total_rows
                            ),
                            minimum=minimum,
                        )
                    holding_start = lanes.hour_holding(cutoff)[0]
                    current_windows = defaultdict(list)
                    prior_windows: dict[datetime, dict[str, list[Window]]] = defaultdict(
                        lambda: defaultdict(list)
                    )
                    for identity, windows in score_windows.items():
                        for window in windows:
                            if window.start == holding_start:
                                current_windows[identity].append(window)
                            if window.cutoff < cutoff:
                                prior_windows[window.cutoff][identity].append(window)
                    with clock.stage("reference_samples"):
                        samples = _live_mia_samples(
                            snapshot, store, selected, current_windows, pending
                        )
                    with clock.stage("reference_statistics"):
                        previous_values = _reference_rows(
                            previous, "mia_reference_values", max_total_rows
                        )
                        parameters = dict(
                            previous_sets=previous_sets,
                            previous_values=previous_values,
                            historical=historical,
                            segments=segments,
                            catalog=catalog_version,
                        )
                        if current_windows:
                            published_refs, effective[cutoff] = mia_statistics.live_statistics(
                                samples,
                                cutoff,
                                **parameters,
                                reuse_cut_sample=review_hour is not None,
                            )
                        else:
                            published_refs = {"sets": [], "members": [], "values": []}
                        for when, review_windows in sorted(prior_windows.items()):
                            earlier = [p for p in selected if p["id_produccion"] in review_windows]
                            measurements = _live_mia_samples(
                                snapshot, store, earlier, review_windows, pending
                            )
                            _, effective[when] = mia_statistics.live_statistics(
                                measurements,
                                when,
                                **{**parameters, "historical": {}},
                                reuse_cut_sample=True,
                            )
                else:
                    published_refs = mia_statistics.historical_statistics(
                        prods,
                        segments,
                        rates,
                        catalog=catalog_version,
                        minimum=minimum,
                    )
                    replaced = {
                        r["reference_id"]
                        for r in previous_sets
                        if r["reference_rule_id"] == mia_statistics.HISTORICAL
                    }
                    _carry_references(previous, store, max_total_rows, exclude=replaced)
                for key, name in zip(("sets", "members", "values"), REFERENCE_TABLES, strict=True):
                    for row in published_refs[key]:
                        _store_row(store, name, row.get("id_produccion"), row)

        def payload_for(production, old_tables, previous_states, previous_scores):
            payload = {
                "spill": str(store.path),
                "production": production,
                "productions": prods,
                "capacities": capacities,
                "ar_conflicts": ar_conflicts,
                "by_zc": by_zc,
                "previous_states": previous_states,
                "old_tables": old_tables,
                "previous_scores": previous_scores,
                "previous_coverage": previous_coverage.get(production["id_produccion"]),
                "pending": pending,
                "segments": segments,
                "rates": rates,
                "config": config,
                "config_id": config_id,
                "catalog_version": catalog_version,
                "minimum_samples": int(config["mia"]["historical_scope"]["min_samples"]),
                "cutoff": cutoff,
                "calculation_time": calculation_time,
                "last_discovery": last_discovery,
                "publication_id": snapshot.publication_id,
                "coverage": snapshot.manifest["coverage"],
                "recalculate_closed_ids": recalculate_closed_ids,
            }
            if hour_plan is not None:
                payload["windows"] = score_windows[production["id_produccion"]]
            if mia_reference_mode == "live":
                empty_reference = {"sets": [], "members": [], "values": []}
                payload["mia_reference"] = empty_reference
                payload["live_references"] = {
                    when: by_model.get(production["codigo_modelo_botella"], empty_reference)
                    for when, by_model in effective.items()
                }
            return payload

        history_clock = _Stopwatch()
        history_timing: dict[str, float] = {}
        history_decoded: dict[str, int] = defaultdict(int)
        shared_history = None
        shared_history_fallback = False
        live_history = (
            lane == "live" and current_slot_seconds is not None and mia_reference_mode == "live"
        )
        slow_productions: list[dict] = []
        score_work: dict[str, float] = defaultdict(float)
        score_calls: dict[str, int] = defaultdict(int)

        def read_score_history(production):
            started = perf_counter()
            identity = production["id_produccion"]
            if shared_history is not None:
                return shared_history.pop(identity)
            result = _history(
                previous, identity, max_total_rows, clock=history_clock, prune=live_history
            )
            history_timing[identity] = round(perf_counter() - started, 6)
            return result

        def absorb(result):
            identity = result["state"]["id_produccion"]
            timing = result["timing"]
            for name, seconds in timing["seconds"].items():
                score_work[name] += seconds
                score_calls[name] += timing["calls"][name]
            history_seconds = history_timing.pop(identity, None)
            slow_productions.append(
                {
                    "production": identity,
                    "history_seconds": history_seconds,
                    "total_seconds": (history_seconds or 0.0) + sum(timing["seconds"].values()),
                    **timing,
                }
            )
            slow_productions.sort(key=lambda row: row["total_seconds"], reverse=True)
            del slow_productions[16:]
            tables.setdefault("production_calculation_state", []).append(result["state"])
            for key, name in zip(("sets", "members", "values"), REFERENCE_TABLES, strict=True):
                for row in result["reference"][key]:
                    _store_row(store, name, identity, row)
            scope_rows.extend(result["scope_rows"])
            for name, rows in result["tables"].items():
                for row in rows:
                    _store_row(store, name, row.get("id_produccion") or identity, row)
            pending.extend(result["pending"])
            score_stats["mia_windows_reused"] += result["reused"]
            score_stats["mia_windows_recalculated"] += result["recalculated"]
            score_stats["unchanged_productions"] += int(result["unchanged"])

        with clock.stage("scores"):
            if live_history and previous is not None and selected:
                with clock.stage("shared_score_history"):
                    try:
                        shared_history = _shared_history(
                            previous,
                            {
                                p["id_produccion"]: score_windows[p["id_produccion"]]
                                for p in selected
                            },
                            max_total_rows,
                            clock=history_clock,
                            decoded=history_decoded,
                        )
                    except _SharedHistoryBudget:
                        shared_history_fallback = True
            workers = score_stats["score_workers"]
            if workers == 1 or len(selected) < 2:
                score_stats["score_workers"] = 1
                for production in selected:
                    old_tables, previous_states, previous_scores = read_score_history(production)
                    absorb(
                        _score_job(
                            payload_for(production, old_tables, previous_states, previous_scores)
                        )
                    )
                    del old_tables, previous_states, previous_scores
            else:
                payloads = []
                for production in selected:
                    old_tables, previous_states, previous_scores = read_score_history(production)
                    payloads.append(
                        payload_for(production, old_tables, previous_states, previous_scores)
                    )
                context = multiprocessing.get_context("spawn")
                with ProcessPoolExecutor(
                    max_workers=min(workers, len(payloads)), mp_context=context
                ) as pool:
                    for result in pool.map(_score_job, payloads, chunksize=1):
                        absorb(result)
        score_stats["score_timing"] = {
            "productions": len(selected),
            "worker_seconds": {name: round(value, 6) for name, value in score_work.items()},
            "worker_calls": dict(score_calls),
            "history_table_seconds": history_clock.stages,
            "history_table_calls": history_clock.calls,
            "history_shared_rows_decoded": dict(history_decoded),
            "history_shared_seconds": clock.stages.get("shared_score_history"),
            "history_shared_budget_fallback": shared_history_fallback,
            "slow_productions": slow_productions,
        }
        store.dedupe()
        scope_rows.extend(_pending_scope_rows(pending, snapshot, last_discovery))
        score_chunks = [chunk for chunk in hour_plan or [] if not chunk.facts_only]
        published.replace_ids = None if chosen is None or facts_only else scoring
        published.hours = (
            None
            if not score_chunks
            else tuple((chunk.production_id, chunk.start) for chunk in score_chunks)
        )
        published.fact_bounds = (
            None
            if hour_plan is None
            else tuple(
                (chunk.production_id, chunk.read_from or chunk.start, chunk.cutoff)
                for chunk in hour_plan
            )
        )
        published.result_ids = (
            None
            if not score_chunks
            else tuple(
                Window(chunk.production_id, "HOUR", chunk.start, chunk.end, chunk.cutoff).identity
                for chunk in score_chunks
            )
        )
        if current_slot_seconds is not None:
            score_stats["horizon_complete"] = True
        elif lane == "live" and hour_plan:
            projected = {pid: dict(starts) for pid, starts in stored_hours.items()}
            done = set()
            copied_now = dict(copied_until or {})
            for chunk in hour_plan:
                if not chunk.facts_only:
                    projected.setdefault(chunk.production_id, {})[chunk.start] = chunk.cutoff
                copied_now[chunk.production_id] = chunk.cutoff
                done.add((chunk.production_id, chunk.start))
            score_stats["copied"] = {
                identity: moment.isoformat() for identity, moment in copied_now.items()
            }
            score_stats["horizon_complete"] = not lanes.plan(
                prods,
                projected,
                snapshot.manifest["coverage"],
                cutoff,
                "live",
                families=LIVE_FAMILIES,
                reopen=reopen - done,
                since=since,
                copied_until=copied_now,
            )
        published.update(tables)
        for name in store.families():
            if name in contracts.CONTRACTS:
                published.mark(name)
        published["publication_scope_status"] = scope_rows
        for name in (*SCORE_TABLES, *contracts.CONTRACTS):
            published.setdefault(name, [])
        with clock.stage("dimensions"):
            lookup = dimensional.materialize(
                published,
                plant=snapshot.manifest["plant"],
                machines=reader.rows("SIP_MAQUINA_MOLDEO"),
                recorded_at=calculation_time,
                previous=_previous_tables(previous, max_total_rows, DIMENSION_TABLES),
            )
            for name in sorted(published.spilled & set(dimensional.DIMENSIONAL_FACTS)):

                def stamp(row, name=name):
                    return dimensional.attach_dimensions(name, row, lookup)

                store.rewrite(name, stamp)
            published["dim_date"] = sorted(lookup.dates.values(), key=lambda r: r["date_key"])
        with clock.stage("validate"):
            for name in list(published):
                if name in published.spilled:
                    continue
                published[name] = _unique(name, published[name])
            if validate_result:
                contracts.validate(published, complete=True)
        if statistics is not None:
            statistics.update(score_stats)
            statistics["source_rows_read"] = reader.seen_rows
            statistics["stage_seconds"] = clock.stages
            if isinstance(snapshot, Evidence):
                statistics["evidence_reads"] = dict(snapshot.read_stats)
        handed = True
        return published, published["publication_scope_status"]
    finally:
        if not handed:
            published.close()


def _live_receipt(output_root: Path) -> dict | None:
    path = output_root / "lanes" / "live.json"
    return read_json(path) if path.exists() else None


def _live_anchor(cutoff: datetime) -> datetime:
    """Start of the ten-minute slot live copies. Same clock as Silver."""
    return lanes.slot_start(cutoff)


def _mark_live_since(output_root: Path, _cutoff: datetime) -> tuple[datetime | None, bool]:
    """The last Silver slot Gold already copied. The wall clock does not move it."""
    path = output_root / "lanes" / "since.json"
    if not path.exists():
        return None, False
    return datetime.fromisoformat(read_json(path)["since"]), False


def _read_copied(output_root: Path) -> dict[str, datetime]:
    path = output_root / "lanes" / "copied.json"
    if not path.exists():
        return {}
    return {key: datetime.fromisoformat(value) for key, value in read_json(path).items()}


def _remember_copied(output_root: Path, statistics: dict) -> None:
    copied = statistics.get("copied")
    if copied:
        atomic_json(output_root / "lanes" / "copied.json", copied)
        moments = [datetime.fromisoformat(value) for value in copied.values()]
        if moments:
            atomic_json(
                output_root / "lanes" / "since.json",
                {"since": max(moments).isoformat()},
            )


def _remember_live(
    output_root: Path,
    silver_id: str,
    publication_id: str | None,
    *,
    mia_reference_mode=None,
    effective_config_id=None,
) -> None:
    """The eight-hour window is stored for this Silver publication, so the next tick can exit."""
    atomic_json(
        output_root / "lanes" / "live.json",
        {
            "status": "ACKNOWLEDGED",
            "horizon": "COMPLETE",
            "input_publication_id": silver_id,
            "mia_reference_mode": mia_reference_mode,
            "effective_config_id": effective_config_id,
            "publication_id": publication_id,
        },
    )


def _live_caught_up(
    input_root: Path,
    output_root: Path,
    confirmed_input=None,
    *,
    mia_reference_mode=None,
    effective_config_id=None,
) -> dict | None:
    """Same Silver publication and a complete window: do not open the fact history."""
    receipt = _live_receipt(output_root)
    if (
        receipt is None
        or receipt.get("status") != "ACKNOWLEDGED"
        or receipt.get("horizon") != "COMPLETE"
        or receipt.get("mia_reference_mode") != mia_reference_mode
        or receipt.get("effective_config_id") != effective_config_id
        or not (output_root / "current.json").exists()
    ):
        return None
    snap = confirmed_input or Snapshot.open(input_root)
    if receipt.get("input_publication_id") != snap.publication_id:
        return None
    return {
        "status": "UNCHANGED",
        "lane": "live",
        "publication_id": receipt.get("publication_id"),
        "source": source_freshness(snap.manifest, LIVE_FAMILIES),
    }


def _run(
    input_root: Path,
    output_root: Path,
    *,
    bundle: dict,
    config: dict,
    cutoff: datetime,
    environment: str,
    plant: str,
    max_rows_per_source=5_000_000,
    max_total_rows=20_000_000,
    failpoint=None,
    recalculate_closed_ids=None,
    recalculation_reason=None,
    lane: str = "all",
    confirmed_input=None,
    reference_snapshot=None,
    recovery: tuple[str, dict] | None = None,
    spill_root: Path | None = None,
    spill_db_bytes: int | None = None,
    current_slot_seconds: int | None = None,
    mia_reference_mode: str | None = None,
) -> dict:
    input_root, output_root = input_root.resolve(strict=True), output_root.resolve()
    if (
        input_root == output_root
        or output_root.is_relative_to(input_root)
        or input_root.is_relative_to(output_root)
    ):
        raise IntegrityError("Gold and its input store must be independent directories")
    output_root.mkdir(parents=True, exist_ok=True)
    recalculation = {
        "productions": sorted(recalculate_closed_ids or []),
        "reason": recalculation_reason,
    }
    if bool(recalculation["productions"]) != bool(
        recalculation_reason and recalculation_reason.strip()
    ):
        raise IntegrityError("Historical recalculation requires explicit productions and a reason")
    effective_config_id = digest([config, bundle])
    if lane == "live" and recovery is None:
        caught_up = _live_caught_up(
            input_root,
            output_root,
            confirmed_input,
            mia_reference_mode=mia_reference_mode,
            effective_config_id=effective_config_id,
        )
        if caught_up is not None:
            return caught_up
    if lane == "history":
        try:
            held = portalocker.Lock(output_root / "live.priority.lock", timeout=0)
            held.acquire()
        except portalocker.exceptions.LockException:
            return {"status": "UNCHANGED", "lane": "history", "reason": "LIVE_PRIORITY"}
        else:
            held.release()
    priority = (
        portalocker.Lock(output_root / "live.priority.lock", timeout=2) if lane == "live" else None
    )
    try:
        if priority is not None:
            priority.acquire()
    except portalocker.exceptions.LockException:
        return {"status": "UNCHANGED", "lane": "live", "reason": "LIVE_BUSY"}
    try:
        # A lane scores outside it and waits only for the other lane's commit.
        hold = (
            portalocker.Lock(output_root / "pipeline.lock", timeout=0)
            if lane == "all"
            else nullcontext()
        )
        with hold:
            snap = confirmed_input or Snapshot.open(input_root)
            code_id = digest(
                [
                    source_fingerprint(__name__),
                    ("python", platform.python_version()),
                    reference_snapshot.publication_id if reference_snapshot else None,
                ]
            )
            immutable_json(
                output_root / "configs" / f"{digest([config, bundle])}.json",
                {"schema_id": "quality-effective-config-v1", "score": config, "catalogs": bundle},
            )
            # A full build is one operation. A lane is not: the next chunk must still run
            # after this one is stored, so its receipt is not a reason to skip the lane.
            if recovery is not None:
                _, state = recovery
                if state["input_publication_id"] != snap.publication_id:
                    raise IntegrityError("Recovery run refers to another input")
                if state["config_id"] != digest([config, bundle]):
                    raise IntegrityError("Recovery configuration differs from the prepared run")
                code_id = state["code_id"]
                state_path = output_root / "runs" / f"{state['work_id']}.json"
            elif lane == "all":
                work_id = digest(
                    [snap.publication_id, bundle, config, cutoff, code_id, recalculation]
                )
                state_path = output_root / "runs" / f"{work_id}.json"
                if state_path.exists():
                    state = read_json(state_path)
                else:
                    state = {
                        "work_id": work_id,
                        "calculation_time": utc_now(),
                        "status": "PREPARED",
                        "code_id": code_id,
                        "input_publication_id": snap.publication_id,
                        "config_id": digest([config, bundle]),
                        "python": platform.python_version(),
                        "recalculation": recalculation,
                        "lane": lane,
                        "mia_reference_mode": mia_reference_mode,
                    }
                    atomic_json(state_path, state)
            else:
                state = {
                    "work_id": digest([snap.publication_id, cutoff.isoformat(), lane, utc_now()]),
                    "calculation_time": utc_now(),
                    "status": "PREPARED",
                    "code_id": code_id,
                    "input_publication_id": snap.publication_id,
                    "config_id": digest([config, bundle]),
                    "python": platform.python_version(),
                    "recalculation": recalculation,
                    "lane": lane,
                    "current_slot_seconds": current_slot_seconds,
                    "mia_reference_mode": mia_reference_mode,
                }
                state_path = output_root / "runs" / f"{state['work_id']}.json"
                atomic_json(state_path, state)
            source = source_freshness(snap.manifest, LIVE_FAMILIES)
            if lane == "all" and state["status"] == "ACKNOWLEDGED":
                Snapshot.open(
                    output_root, publication_id=state["publication_id"], require_gold_contract=True
                )
                return {
                    "status": "UNCHANGED",
                    "publication_id": state["publication_id"],
                    "source": source,
                }
            calculation_time = datetime.fromisoformat(state["calculation_time"]).replace(
                tzinfo=None
            )
            previous = (
                Snapshot.open(output_root) if (output_root / "current.json").exists() else None
            )
            previous_id = previous.publication_id if previous else None
            if (
                recovery is not None
                and state.get("base_publication_id", previous_id) != previous_id
            ):
                raise IntegrityError("Recovery base differs from the published Gold snapshot")
            since = copied_until = None
            if lane == "live" and current_slot_seconds is None:
                since, _ = _mark_live_since(output_root, cutoff)
                copied_until = _read_copied(output_root)
            statistics: dict[str, Any] = {}
            tables, coverage = build(
                snap,
                bundle=bundle,
                config=config,
                cutoff=cutoff,
                previous=previous,
                calculation_time=calculation_time,
                max_rows_per_source=max_rows_per_source,
                max_total_rows=max_total_rows,
                statistics=statistics,
                recalculate_closed_ids=set(recalculate_closed_ids or []),
                lane=lane,
                since=since,
                copied_until=copied_until,
                reference_snapshot=reference_snapshot,
                spill_root=spill_root,
                spill_db_bytes=spill_db_bytes,
                current_slot_seconds=current_slot_seconds,
                mia_reference_mode=mia_reference_mode,
                validate_result=False,  # Publisher validates the final rows before any commit.
            )
            if tables is None:
                if recovery is not None:
                    raise IntegrityError("Recovery did not reproduce the prepared publication")
                state.update(status="IDLE", lane=lane)
                atomic_json(state_path, state)
                _remember_ar(output_root, statistics)
                _remember_copied(output_root, statistics)
                published_id = previous.publication_id if previous is not None else None
                if lane == "live" and statistics.get("horizon_complete"):
                    _remember_live(
                        output_root,
                        snap.publication_id,
                        published_id,
                        mia_reference_mode=mia_reference_mode,
                        effective_config_id=effective_config_id,
                    )
                return {
                    "status": "UNCHANGED",
                    "lane": lane,
                    "publication_id": published_id,
                    "source": source,
                    "incremental": statistics,
                    "chunk_productions": [],
                }
            commit = nullcontext()  # The full-build owner already holds pipeline.lock.
            try:
                with commit:
                    # Bounded first snapshot; the writer also supports explicit
                    # per-scope replacement.
                    publication_config = digest([bundle, config, code_id, recalculation])
                    replace_ids = getattr(tables, "replace_ids", None)
                    hours = getattr(tables, "hours", None)
                    result_ids = getattr(tables, "result_ids", None)
                    publication_id = digest(
                        [
                            "gold-v1",
                            snap.publication_id,
                            publication_config,
                            cutoff.isoformat(),
                            commit_scope(replace_ids, hours),
                        ]
                    )
                    if recovery is not None and recovery[0] != publication_id:
                        raise IntegrityError("Recovery did not reproduce the prepared operation ID")
                    if confirmed_input is not None:
                        state.update(
                            operation_id=publication_id,
                            cutoff=cutoff.isoformat(),
                            base_publication_id=previous_id,
                            reference_publication_id=(
                                reference_snapshot.publication_id if reference_snapshot else None
                            ),
                        )
                        atomic_json(state_path, state)
                    if "quality_scores" not in getattr(tables, "spilled", ()):
                        for row in tables["quality_scores"]:
                            if row.get("basis_publication_id") is None:
                                row["basis_publication_id"] = publication_id
                    for _attempt in range(5):
                        try:
                            result = Publisher(
                                output_root,
                                environment=environment,
                                plant=plant,
                                failpoint=failpoint,
                                verified_base=previous,
                            ).publish(
                                snap,
                                tables,
                                config_id=publication_config,
                                cutoff=cutoff.isoformat(),
                                replace_ids=replace_ids,
                                hours=hours,
                                result_ids=result_ids,
                                fact_bounds=getattr(tables, "fact_bounds", None),
                                lock_timeout=0 if lane == "all" else 60,
                                coverage=[
                                    {
                                        "source": "publication_scope_status",
                                        "source_discovery_at": min(
                                            (
                                                r["last_successful_discovery"].isoformat()
                                                for r in coverage
                                                if r["last_successful_discovery"]
                                            ),
                                            default=None,
                                        ),
                                        "rows": len(coverage),
                                        "estado": "READY"
                                        if all(r["estado"] == "READY" for r in coverage)
                                        else "WAITING_DEPENDENCY",
                                    }
                                ],
                            )
                            break
                        except CommitConflict:
                            if _attempt == 4:
                                raise
                    state.update(status="ACKNOWLEDGED", publication_id=result["publication_id"])
                    state["incremental"] = statistics
                    atomic_json(state_path, state)
                    _remember_ar(output_root, statistics)
                    _remember_copied(output_root, statistics)
                    if lane == "live" and statistics.get("horizon_complete"):
                        _remember_live(
                            output_root,
                            snap.publication_id,
                            result["publication_id"],
                            mia_reference_mode=mia_reference_mode,
                            effective_config_id=effective_config_id,
                        )
                    spilled = getattr(tables, "spilled", ())
                    return {
                        **result,
                        "rows": {
                            name: tables.store.count(name) if name in spilled else len(tables[name])
                            for name in tables
                        },
                        "pending_scopes": sum(r["estado"] != "READY" for r in coverage),
                        "incremental": statistics,
                        "source": source,
                        "coverage": coverage_summary(coverage, cutoff),
                    }
            finally:
                tables.close()
    finally:
        if priority is not None:
            priority.release()


def run(input_root, output_root, **kwargs):
    from .operations import observed

    input_root, output_root = Path(input_root).resolve(), Path(output_root).resolve()
    if (
        input_root == output_root
        or input_root.is_relative_to(output_root)
        or output_root.is_relative_to(input_root)
        or (input_root.exists() and output_root.exists() and input_root.samefile(output_root))
    ):
        raise IntegrityError("Gold and its input store must be independent directories")
    return observed(output_root, lambda: _run(input_root, output_root, **kwargs))
