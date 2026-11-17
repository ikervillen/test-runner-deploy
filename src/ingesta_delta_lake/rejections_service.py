"""Adaptación acotada del protocolo MIA a LISTA_RECH, con control y Delta separados."""

from . import lista_rech as rech
from .defects_service import inspection_bases as read_bases
from .defects_service import process as ingest_observations
from .rejections_storage import RejectionsStore


def process(config, control, attempt, *, stop_requested=None, lane="all"):
    return ingest_observations(
        config,
        control,
        attempt,
        model=rech,
        store_class=RejectionsStore,
        operation_kind="lista_rech_insert_v1",
        stop_requested=stop_requested,
        lane=lane,
    )


def inspection_bases(config, origin=None):
    return read_bases(
        config,
        origin,
        model=rech,
        store_class=RejectionsStore,
        family="LISTA_RECH",
        denominator_source="LISTA_RECH",
    )
