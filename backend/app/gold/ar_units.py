"""Physical identity for AR exposure; source and attribution rows stay intact."""

from collections import defaultdict
from itertools import pairwise

from .common import PendingBasis

AR_UNIT_RULE = "fenix-99z-by-mould-v1"


def mould_unit(event, production):
    plant = production.get("planta")
    origin = event.get("origen_fenix")
    model = production.get("codigo_modelo_botella")
    mould = event.get("molde")
    if (
        plant
        and origin
        and production.get("origen_fenix") == origin
        and model
        and type(mould) is int
        and mould > 0
        and event.get("zona") in {"ZF", "ZC"}
    ):
        return ("mould", plant, origin, model, mould)
    return None


def episode_key(event, production):
    unit = mould_unit(event, production)
    if unit is None:
        return None
    return (unit, event["codigo_defecto"], event["fecha_on"], event.get("zona"))


def continuity(events, production):
    """Accredit bridges only for a matching episode and while its physical row applies."""
    detached, physical = set(), defaultdict(list)
    for event in events:
        key = episode_key(event, production)
        if key is None:
            continue
        cavity = event["cavidad"]
        if cavity == "99Z":
            detached.add(key)
        elif cavity not in {None, "", "-"}:
            physical[key].append((cavity, event["fecha_on"], event["fecha_off"]))
    bridges = defaultdict(list)
    for key in detached:
        bridges[key[0]].extend(physical.get(key, ()))
    return bridges


def affected_intervals(event, production, bridges, conflicting_events, left, right):
    cavity = event["cavidad"]
    if cavity in {None, "", "-"}:
        raise PendingBasis("AR_CAVITY_UNRESOLVED", "AR event has no identified physical cavity")
    key = episode_key(event, production) if cavity == "99Z" or conflicting_events else None
    if key in conflicting_events:
        raise PendingBasis("AR_MOULD_ATTRIBUTION_PENDING", "Conflicting accredited AR attributions")
    if cavity != "99Z":
        return [(("cavity", cavity), left, right)]
    if key is None:
        raise PendingBasis("AR_MOULD_IDENTITY_PENDING", "99Z has no accredited mould identity")
    unit = key[0]
    segments = [
        (c, max(start, left), min(end or right, right))
        for c, start, end in bridges.get(unit, ())
        if start < right and (end is None or end > left)
    ]
    breaks = sorted({left, right, *(t for _, start, end in segments for t in (start, end))})
    result = []
    for start, end in pairwise(breaks):
        cavities = {c for c, a, b in segments if a <= start and end <= b}
        if len(cavities) > 1:
            raise PendingBasis("AR_MOULD_ATTRIBUTION_PENDING", "One mould has multiple cavities")
        affected = ("cavity", next(iter(cavities))) if cavities else unit
        result.append((affected, start, end))
    return result


def record_episode(episodes, event, production):
    key = episode_key(event, production)
    if key is None:
        return
    owners, has_detached = episodes.get(key, (set(), False))
    owners.add(event["id_produccion"])
    episodes[key] = owners, has_detached or event["cavidad"] == "99Z"


def conflicts(episodes):
    return {key for key, (owners, detached) in episodes.items() if detached and len(owners) > 1}
