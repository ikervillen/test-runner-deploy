"""How far Silver accredits each production: the data decide the cutoff, not the clock.

A production is calculated up to the last ten-minute cutoff that every required
family covers for its origin. The scheduler's moment is only a ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .context import stamp

STEP = timedelta(minutes=10)
INCOMPLETE_AFTER = timedelta(hours=24)
REQUIRED_FAMILIES = frozenset({"LISTA_DEFECTOS", "LISTA_RECH", "LISTA_AR", "CONT_ZC"})


@dataclass(frozen=True)
class Accreditation:
    until: datetime | None
    missing: tuple[str, ...]
    bounded: bool

    @property
    def reason(self) -> str | None:
        return "COVERAGE:" + ",".join(self.missing) if self.missing else None


def floor_step(moment: datetime) -> datetime:
    minute = moment.minute // 10 * 10
    return moment.replace(minute=minute, second=0, microsecond=0)


LIVE_SLOT_FAMILIES = frozenset({"LISTA_DEFECTOS", "LISTA_RECH", "CONT_ZC"})


def accredited(
    coverage: list[dict],
    origin: str | None,
    families=REQUIRED_FAMILIES,
    until_field: str = "covered_until",
) -> Accreditation:
    """Latest moment every family covers for the origin.

    `covered_until` is the history cursor. A family without it is open.
    `live_until` is the newest ten-minute slot. For the three operational
    families, a missing live slot means the line is not ready.
    """
    bounds: dict[str, datetime | None] = {}
    missing = []
    for family in sorted(families):
        ready = [
            r
            for r in coverage
            if r.get("source") == family
            and r.get("origin") in {None, origin}
            and r.get("estado") == "READY"
        ]
        if not ready:
            missing.append(family)
            continue
        untils = [stamp(r[until_field]) for r in ready if r.get(until_field) is not None]
        live_required = until_field == "live_until" and family in LIVE_SLOT_FAMILIES
        if live_required and len(untils) != len(ready):
            missing.append(family)
            continue
        bounds[family] = max(untils) if len(untils) == len(ready) else None
    if missing:
        return Accreditation(None, tuple(missing), False)
    bounded = [u for u in bounds.values() if u is not None]
    if not bounded:
        return Accreditation(None, (), False)
    return Accreditation(floor_step(min(bounded)), (), True)


def effective_cutoff(
    ceiling: datetime, accreditation: Accreditation, previous: datetime | None = None
) -> datetime | None:
    """Never later than the ceiling, never earlier than what was already published."""
    if accreditation.missing:
        return previous
    until = ceiling
    if accreditation.bounded and accreditation.until is not None:
        until = min(ceiling, accreditation.until)
    return max(until, previous) if previous is not None else until


SCOPE_PREFIX = "coverage:"


def published_until(scope_rows: list[dict]) -> dict[str, datetime | None]:
    """How far each production was calculated in the previous publication."""
    return {
        r["id_produccion"]: stamp(r["period_end"]) if r["period_end"] is not None else None
        for r in scope_rows
        if str(r["scope_id"]).startswith(SCOPE_PREFIX)
    }


def production_scope(
    production: dict,
    coverage: list[dict],
    *,
    ceiling: datetime,
    previous: datetime | None,
    families=REQUIRED_FAMILIES,
    evidence: str,
    seen: datetime | None,
    until_field: str = "covered_until",
) -> tuple[dict, datetime | None]:
    """One availability row per production: calculated until when, and what is missing."""
    identity = production["id_produccion"]
    accreditation = accredited(coverage, production["origen_fenix"], families, until_field)
    target = min(ceiling, production["fecha_fin_zf"] or ceiling)
    cutoff = effective_cutoff(ceiling, accreditation, previous)
    cutoff = min(cutoff, target) if cutoff is not None else None
    estado, reason = coverage_state(ceiling, target, cutoff, accreditation)
    row = {
        "scope_id": SCOPE_PREFIX + identity,
        "id_produccion": identity,
        "source": "coverage",
        "origin": production["origen_fenix"],
        "period_start": production["fecha_inicio_operativo_zc"],
        "period_end": cutoff,
        "estado": estado,
        "reason_code": reason,
        "evidence_reference": evidence,
        "last_successful_discovery": seen,
        "confirmed_source_version": None,
        "pending_files": None,
        "earliest_pending_period": None if estado == "READY" else cutoff,
    }
    return row, cutoff


def summary(scope_rows: list[dict], ceiling: datetime) -> dict:
    """Operational view: how many productions lag the ceiling and by how much at worst."""
    lags = [
        (ceiling - stamp(r["period_end"] or r["period_start"] or ceiling)).total_seconds()
        for r in scope_rows
        if str(r["scope_id"]).startswith(SCOPE_PREFIX) and r["estado"] != "READY"
    ]
    return {
        "ceiling": ceiling.isoformat(),
        "blocked_productions": len(lags),
        "max_lag_seconds": max(lags, default=0.0),
    }


def coverage_status(scope_rows: list[dict]) -> list[dict]:
    """What a consumer needs per production: how far it is calculated and what is missing."""
    return [
        {
            "id_produccion": r["id_produccion"],
            "origin": r["origin"],
            "estado": r["estado"],
            "calculated_until": _iso(r["period_end"]),
            "missing": _missing(r["reason_code"]),
        }
        for r in sorted(scope_rows, key=lambda r: str(r["id_produccion"]))
        if str(r["scope_id"]).startswith(SCOPE_PREFIX)
    ]


def _iso(value) -> str | None:
    return None if value is None else stamp(value).isoformat()


def _missing(reason: str | None) -> list[str]:
    if not reason or not reason.startswith("COVERAGE:"):
        return []
    families = reason.removeprefix("COVERAGE:")
    return [] if families == "INTERVAL" else families.split(",")


def coverage_state(
    ceiling: datetime, target: datetime, cutoff: datetime | None, accreditation: Accreditation
) -> tuple[str, str | None]:
    """READY when the data reach the target; INCOMPLETE after a day without them."""
    if cutoff is not None and cutoff >= target:
        return "READY", None
    reason = accreditation.reason or "COVERAGE:INTERVAL"
    waiting_since = cutoff if cutoff is not None else None
    if waiting_since is not None and ceiling - waiting_since > INCOMPLETE_AFTER:
        return "INCOMPLETE", reason
    return "WAITING_DEPENDENCY", reason
