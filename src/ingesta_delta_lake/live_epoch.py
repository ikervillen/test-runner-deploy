"""A prospective Live generation uses the industrial clock declared by Fenix."""

import os
from datetime import datetime
from functools import lru_cache

from .common import IntegrityError

BOOTSTRAP = frozenset(
    {
        "MP9_CAMBIO_MODELO",
        "MP9_PARAM_MODELO",
        "SIP_MODELO",
        "SIP_DEFECTO",
        "SIP_DEFECTO_STATUS",
        "SIP_DEFECTO_AR",
        "SIP_MAQUINA_MOLDEO",
        "SIP_LINEA_ZF",
        "SIP_NUMERO_GOTAS_SIMULTANEAS",
    }
)


@lru_cache(maxsize=4)
def _parse(stamp):
    value = datetime.fromisoformat(stamp)
    if value.tzinfo or value.second or value.microsecond or value.minute % 10:
        raise IntegrityError("Live T0 must be an industrial ten-minute boundary")
    return value


def epoch():
    value = os.environ.get("FENIX_LIVE_T0")
    return _parse(value) if value else None


def before_epoch(raw, index, floor):
    if floor is None or len(raw) <= index:
        return False
    from .mp9 import timestamp

    try:
        return datetime.fromisoformat(timestamp(raw[index])) < floor
    except ValueError:
        return False  # Existing validation still accounts for invalid records.


def retain_mp9_context(parsed, existing, floor):
    from collections import defaultdict

    variants = defaultdict(list)
    for entry in parsed.entries:
        variants[entry[:2]].append(entry)
    keep = set(existing)
    for identity, entries in variants.items():
        closures = {entry[4] for entry in entries if entry[4] is not None}
        if len(closures) != 1 or next(iter(closures)) >= floor:
            keep.add(identity)
    pending = list(keep)
    while pending:
        identity = pending.pop()
        for entry in variants.get(identity, []):
            if entry[7] is not None:
                parent = (entry[0], entry[7])
                if parent not in keep:
                    keep.add(parent)
                    pending.append(parent)
    parsed.entries = {entry: refs for entry, refs in parsed.entries.items() if entry[:2] in keep}
