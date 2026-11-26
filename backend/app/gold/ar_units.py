"""Physical identity for AR exposure; source and attribution rows stay intact."""

from collections import defaultdict

from .common import PendingBasis


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
    """Only join representations of the same episode with one accredited cavity."""
    detached, physical = set(), defaultdict(set)
    for event in events:
        key = episode_key(event, production)
        if key is None:
            continue
        cavity = event["cavidad"]
        if cavity == "99Z":
            detached.add(key)
        elif cavity not in {None, "", "-"}:
            physical[key].add(cavity)
    return {
        key: next(iter(physical[key])) if len(physical[key]) == 1 else None
        for key in detached
        if physical[key]
    }


def affected_unit(event, production, joined, conflicting_events):
    cavity = event["cavidad"]
    if cavity in {None, "", "-"}:
        raise PendingBasis("AR_CAVITY_UNRESOLVED", "AR event has no identified physical cavity")
    key = (
        episode_key(event, production) if cavity == "99Z" or joined or conflicting_events else None
    )
    if key in conflicting_events or (key in joined and joined[key] is None):
        raise PendingBasis("AR_MOULD_ATTRIBUTION_PENDING", "Conflicting accredited AR attributions")
    if cavity == "99Z":
        if key is None:
            raise PendingBasis("AR_MOULD_IDENTITY_PENDING", "99Z has no accredited mould identity")
        return key[0]
    if key in joined and joined[key] == cavity:
        return key[0]
    return ("cavity", cavity)


def record_episode(episodes, event, production):
    key = episode_key(event, production)
    if key is None:
        return
    owners, has_detached = episodes.get(key, (set(), False))
    owners.add(event["id_produccion"])
    episodes[key] = owners, has_detached or event["cavidad"] == "99Z"


def conflicts(episodes):
    return {key for key, (owners, detached) in episodes.items() if detached and len(owners) > 1}
