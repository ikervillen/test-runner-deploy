"""Local Gold orchestration, with explicit input version, resource budgets and cutoff."""

from __future__ import annotations

import platform
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from time import perf_counter
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
    references,
)
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
from .fingerprint import source_fingerprint
from .freshness import source_freshness
from .incremental import Prepared
from .metrics import Window, ar_bases, live_windows, production_windows, zc_bases
from .publications import Publisher, Snapshot
from .schemas import KEYS, make_table
from .scoring import ScoreResult, calculate, validate_config


def _unique(name, rows):
    result = {}
    for r in rows:
        key = tuple(r[k] for k in KEYS[name])
        if key in result and result[key] != r:
            raise IntegrityError(f"{name}: different values for one output key")
        result[key] = r
    return [r for _, r in sorted(result.items(), key=lambda kv: repr(kv[0]))]


def _coverage_ready(snapshot, p, window, pending, required=None):
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
            (r.get("covered_from") is None or context.stamp(r["covered_from"]) <= window.start)
            and (
                r.get("covered_until") is None or window.cutoff <= context.stamp(r["covered_until"])
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
LIVE_FAMILIES = {"LISTA_RECH", "LISTA_DEFECTOS", "LISTA_AR", "CONT_ZC"}
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

    @contextmanager
    def stage(self, name: str):
        started = perf_counter()
        try:
            yield
        finally:
            self.stages[name] = round(self.stages.get(name, 0.0) + perf_counter() - started, 6)


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

    def rows(self, name: str, optional: bool = False) -> list[dict]:
        if name not in self._cache:
            if name not in self._names and optional:
                return []
            # One Silver table is not a query scope: the total budget is the guard.
            self._cache[name] = self.snapshot.rows(name, max_rows=self.max_total_rows)
            self.count(len(self._cache[name]))
        return self._cache[name]

    def batches(self, name: str):
        for batch in self.snapshot.batches(name, max_rows=self.max_total_rows):
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
        reader.rows("MP9_CAMBIO_MODELO"),
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


def _fact_tables(reader, tables, attribution, pending):
    """Accepted observations attributed to productions, sorted for deterministic hashing."""
    for target, family in (("mia_rejections", "LISTA_DEFECTOS"), ("mia_inspections", "LISTA_RECH")):
        tables[target] = []
        for batch in reader.batches(family):
            rows, issues = facts.mia(family, batch.to_pylist(), attribution)
            tables[target].extend(rows)
            pending.extend(issues)
    tables["ar_intervals"], issues = facts.ar(
        reader.rows("LISTA_AR"),
        attribution,
        tables["defect_catalog"],
        tables["ar_status_catalog"],
        tables["ar_type_catalog"],
        tables["dar_profile_members"],
        tables["model_segment_catalog"],
    )
    pending.extend(issues)
    tables["zc_observations"] = facts.zc(reader.rows("CONT_ZC"))
    assigned = {r["id_asignacion"]: r["id_produccion"] for r in tables["production_assignments"]}
    for name in ("mia_rejections", "mia_inspections", "ar_intervals"):
        for row in tables[name]:
            identity = row["id_asignacion"]
            if identity is not None and assigned.get(identity) != row["id_produccion"]:
                raise IntegrityError(
                    f"{name}: attribution does not reference its accepted production assignment"
                )
    for name in ("mia_rejections", "mia_inspections", "ar_intervals", "zc_observations"):
        tables[name].sort(key=lambda r: r["id_observacion"])


def _zc_links(decisions, tables):
    """Producer-accepted counter links; never inferred from code coincidence."""
    by_zc = defaultdict(set)
    tables.setdefault("zc_production_links", [])
    for d in decisions:
        if d["source_family"] != "CONT_ZC" or d["status"] not in ZC_LINK_STATUSES:
            continue
        for identity in d["productions"]:
            by_zc[identity].add(d["source_id"])
            tables["zc_production_links"].append(
                {
                    "id_produccion": identity,
                    "id_observacion": d["source_id"],
                    "regla_atribucion": d["rule"],
                }
            )
    return by_zc


def _previous_tables(previous, max_rows_per_source, names):
    if previous is None:
        return {}
    available = {r["name"] for r in previous.manifest["tables"]}
    return {
        name: previous.rows(name, max_rows=max_rows_per_source)
        for name in names
        if name in available
    }


def _closed_rates(snapshot, prods, prepared, cutoff, pending):
    """MIA rates of closed productions with covered sources; the historical population input."""
    rates = {}
    for p in prods:
        windows = production_windows(p, cutoff)
        if windows and p["estado_produccion"] == "CLOSED":
            base = prepared.mia(windows[0])
            if base["exposure"]["inspeccionadas"] > 0 and _coverage_ready(
                snapshot, p, windows[0], pending, {"LISTA_DEFECTOS", "LISTA_RECH"}
            ):
                rates[p["id_produccion"]] = base["rates"]
    return rates


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
    config,
    config_id,
    cutoff,
    calculation_time,
    pending,
    preserve_closed,
    previous_scores,
    old_tables,
    last_discovery,
):
    """Window bases and scores for one production; returns its scope status rows."""
    scope_rows = []
    windows = _production_windows(p, cutoff)
    if not windows:
        pending.append(context.issue("quality_scores", "NO_OPERATIVE_WINDOW", p["id_produccion"]))
    segment = segments.get(p["codigo_modelo_botella"], {})
    for w in windows:
        m = prepared.mia(w)
        tables.setdefault("inspection_bases", []).extend(m["inspection_bases"])
        tables.setdefault("mia_window_exposures", []).append(m["exposure"])
        tables.setdefault("mia_window_bases", []).extend(m["amounts"])
        ar_pending_reason = "ASSIGNED_CAPACITY_PENDING"
        try:
            a = ar_bases(tables["ar_intervals"], capacities, w)
        except PendingBasis as exc:
            a = None
            ar_pending_reason = exc.code
        if a:
            tables.setdefault("ar_window_exposures", []).append(a["exposure"])
            tables.setdefault("ar_window_bases", []).extend(a["amounts"])
        z = zc_bases(tables["zc_observations"], w, by_zc[p["id_produccion"]])
        tables.setdefault("zc_window_bases", []).extend(z)
        ready = _coverage_ready(snapshot, p, w, pending)
        if (
            preserve_closed
            and w.identity in previous_scores
            and ready
            and all(previous_scores[w.identity][k] == v for k, v in w.key.items())
        ):
            result = _preserved_result(w, previous_scores, old_tables)
        else:
            result = calculate(
                w,
                mia=m,
                ar=a,
                zc=z,
                reference=ref,
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
            )
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
) -> tuple[dict[str, list[dict]], list[dict]]:
    """A bounded local scope. Larger snapshots must be split, never silently truncated."""
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
    with clock.stage("facts"):
        _fact_tables(reader, tables, Attribution(decisions, prods), pending)
        by_zc = _zc_links(decisions, tables)
    with clock.stage("previous"):
        old_tables = _previous_tables(previous, max_total_rows, REUSED_TABLES)
        for name in REFERENCE_TABLES:
            tables[name] = list(old_tables.get(name, [])) if previous else tables.get(name, [])
        previous_states = {
            r["id_produccion"]: r for r in old_tables.get("production_calculation_state", [])
        }
        previous_scores = {r["id_resultado"]: r for r in old_tables.get("quality_scores", [])}
        previous_coverage = coverage.published_until(
            _previous_tables(previous, max_total_rows, ("publication_scope_status",)).get(
                "publication_scope_status", []
            )
        )
        prepared = Prepared(prods, tables, capacities, by_zc, previous_states, old_tables)
    with clock.stage("references"):
        rates = _closed_rates(snapshot, prods, prepared, cutoff, pending)
    segments = {r["codigo_modelo_botella"]: r for r in tables["model_segment_catalog"]}
    config_id = digest([config, bundle])
    scope_rows = []
    with clock.stage("scores"):
        for p in prods:
            identity = p["id_produccion"]
            raw_hash = prepared.raw_hashes[identity]
            preserve_closed = (
                p["estado_produccion"] == "CLOSED"
                and previous_states.get(identity, {}).get("raw_inputs_hash") == raw_hash
                and identity not in recalculate_closed_ids
            )
            tables.setdefault("production_calculation_state", []).append(
                {"id_produccion": identity, "raw_inputs_hash": raw_hash, "config_id": config_id}
            )
            ref = references.build(
                p,
                prods,
                segments,
                rates,
                input_version=snapshot.publication_id,
                catalog_version=digest(catalog_bundle),
                minimum=int(config["mia"]["historical_scope"]["min_samples"]),
            )
            for key, name in zip(("sets", "members", "values"), REFERENCE_TABLES, strict=True):
                tables.setdefault(name, []).extend(ref[key])
            # The data decide how far this production is calculated; the moment is a ceiling.
            scope_row, production_cutoff = coverage.production_scope(
                p,
                snapshot.manifest["coverage"],
                ceiling=cutoff,
                previous=previous_coverage.get(identity),
                families=LIVE_FAMILIES,
                evidence=snapshot.publication_id,
                seen=last_discovery,
            )
            scope_rows.append(scope_row)
            if production_cutoff is None:
                continue
            scope_rows.extend(
                _score_production(
                    p,
                    snapshot=snapshot,
                    tables=tables,
                    prepared=prepared,
                    capacities=capacities,
                    by_zc=by_zc,
                    segments=segments,
                    ref=ref,
                    config=config,
                    config_id=config_id,
                    cutoff=production_cutoff,
                    calculation_time=calculation_time,
                    pending=pending,
                    preserve_closed=preserve_closed,
                    previous_scores=previous_scores,
                    old_tables=old_tables,
                    last_discovery=last_discovery,
                )
            )
    scope_rows.extend(_pending_scope_rows(pending, snapshot, last_discovery))
    tables["publication_scope_status"] = scope_rows
    for name in (*SCORE_TABLES, *contracts.CONTRACTS):
        tables.setdefault(name, [])
    with clock.stage("dimensions"):
        dimensional.materialize(
            tables,
            plant=snapshot.manifest["plant"],
            machines=reader.rows("SIP_MAQUINA_MOLDEO"),
            recorded_at=calculation_time,
            previous=_previous_tables(previous, max_total_rows, DIMENSION_TABLES),
        )
    with clock.stage("validate"):
        # One representation per logical key, even when hours/turns share base samples.
        for name, rows in tables.items():
            tables[name] = _unique(name, rows)
            make_table(name, tables[name])
        contracts.validate(tables, complete=True)
    if statistics is not None:
        statistics.update(prepared.stats)
        statistics["source_rows_read"] = reader.seen_rows
        statistics["stage_seconds"] = clock.stages
    return tables, tables["publication_scope_status"]


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
    with portalocker.Lock(output_root / "pipeline.lock", timeout=0):
        snap = Snapshot.open(input_root)
        code_id = digest([source_fingerprint(__name__), ("python", platform.python_version())])
        immutable_json(
            output_root / "configs" / f"{digest([config, bundle])}.json",
            {"schema_id": "quality-effective-config-v1", "score": config, "catalogs": bundle},
        )
        work_id = digest([snap.publication_id, bundle, config, cutoff, code_id, recalculation])
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
            }
            atomic_json(state_path, state)
        source = source_freshness(snap.manifest, LIVE_FAMILIES)
        if state["status"] == "ACKNOWLEDGED":
            Snapshot.open(
                output_root, publication_id=state["publication_id"], require_gold_contract=True
            )
            return {
                "status": "UNCHANGED",
                "publication_id": state["publication_id"],
                "source": source,
            }
        calculation_time = datetime.fromisoformat(state["calculation_time"]).replace(tzinfo=None)
        previous = Snapshot.open(output_root) if (output_root / "current.json").exists() else None
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
        )
        # Bounded first snapshot; the writer also supports explicit per-scope replacement.
        publication_config = digest([bundle, config, code_id, recalculation])
        publication_id = digest(
            ["gold-v1", snap.publication_id, publication_config, cutoff.isoformat(), {}]
        )
        for row in tables["quality_scores"]:
            if row.get("basis_publication_id") is None:
                row["basis_publication_id"] = publication_id
        result = Publisher(
            output_root, environment=environment, plant=plant, failpoint=failpoint
        ).publish(
            snap,
            tables,
            config_id=publication_config,
            cutoff=cutoff.isoformat(),
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
        state.update(status="ACKNOWLEDGED", publication_id=result["publication_id"])
        state["incremental"] = statistics
        atomic_json(state_path, state)
        return {
            **result,
            "rows": {k: len(v) for k, v in tables.items()},
            "pending_scopes": sum(r["estado"] != "READY" for r in coverage),
            "incremental": statistics,
            "source": source,
            "coverage": coverage_summary(coverage, cutoff),
        }


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
