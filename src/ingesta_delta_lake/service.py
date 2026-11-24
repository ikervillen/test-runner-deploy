"""Orquestación y reconciliación de fuentes y catálogo, con control por contrato."""

import hashlib
import json
from contextlib import ExitStack, closing
from functools import lru_cache
from pathlib import Path

import portalocker

from . import __version__
from .common import IngestaError, IntegrityError, digest
from .config import Config
from .control import Control
from .delta import DeltaStore
from .sources import capture_inputs


def _source_state(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@lru_cache(maxsize=128)
def _verify_immutable_source(path, state, expected):
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if state != _source_state(path) or actual != expected:
        raise IntegrityError("La evidencia de la operación pendiente ha cambiado")


def _check_evidence(config: Config, prepared: dict) -> None:
    if (
        prepared["contract"] != config.contract.name
        or prepared["transformer_version"] != __version__
    ):
        raise IntegrityError("La operación pendiente utiliza otro contrato o versión de código")
    if config.family == "ASIGNACION_PRODUCCION_RAMAL":
        from .association_storage import verify_artifacts

        verify_artifacts(config, prepared)
    if config.family in {"CATALOGO_PRODUCCIONES", "LISTA_AR", "SIP_LINEA_ZF"}:
        from .catalog_storage import verify_manifest

        verify_manifest(config, prepared["source_manifest"])
    for snapshot in prepared["snapshots"]:
        path = Path(snapshot["bronze_path"]).resolve()
        if not path.is_relative_to(config.bronze_root):
            raise IntegrityError("La entrada pendiente está fuera de Bronze")
        if prepared.get("inspection_plan"):
            _verify_immutable_source(path, _source_state(path), snapshot["sha256"])
        elif hashlib.sha256(path.read_bytes()).hexdigest() != snapshot["sha256"]:
            raise IntegrityError("La evidencia de la operación pendiente ha cambiado")


def _apply(
    config: Config, control: Control, store: DeltaStore, batch: dict
) -> tuple[int | None, bool]:
    previous_context = store.read_context
    previous_zc_scope = store.scope if config.family == "CONT_ZC" else None
    try:
        return _apply_pinned(config, control, store, batch)
    finally:
        store.read_context = previous_context
        if config.family == "CONT_ZC":
            store.set_scope(previous_zc_scope)


def _confirm_prepared(control, batch_id, version, prepared, rows):
    if prepared.get("inspection_groups_from_confirmed_rows"):
        control.confirm(batch_id, version, confirmed_rows=rows)
    else:
        control.confirm(batch_id, version)


def _apply_pinned(config, control, store, batch):
    prepared = control.prepared(batch)
    store.read_context = prepared.get("archive_context")
    scoped = "read_scope" in prepared
    scoped_zc = scoped and config.family == "CONT_ZC"
    if config.family == "CONT_ZC":
        store.set_scope(prepared.get("read_scope"))
    elif scoped:
        if config.family not in {"LISTA_RECH", "LISTA_DEFECTOS"}:
            raise IntegrityError("Unsupported scoped intention")
        store.set_scope(prepared["read_scope"])
    scoped_mia = scoped and not scoped_zc
    content_hash = config.contract.content_hash
    operation = batch["operation_id"]
    _check_evidence(config, prepared)
    table = store.table()
    if scoped_mia:
        from .mia_scope import verify_files
    version, rows = store.read(table)
    latest_only = scoped_zc and batch["state"] == "PREPARED"
    history = (table.history(1) if latest_only else table.history()) if table else []
    commits = [h for h in history if h.get("ingesta_operation_id") == operation]
    if len(commits) > 1:
        raise IntegrityError("La operación aparece en más de un commit")
    if commits:
        committed = commits[0]["version"]
        expected_version = (
            0 if prepared["before_version"] is None else prepared["before_version"] + 1
        )
        if (
            committed != expected_version
            or version != committed
            or content_hash(rows) != prepared["after_hash"]
        ):
            raise IntegrityError("No se puede reconciliar el resultado del commit pendiente")
        if scoped_mia:
            verify_files(table, prepared, after=True)
        elif scoped_zc:
            store.verify_commit(table, table.version(), prepared)
        _confirm_prepared(control, batch["batch_id"], version, prepared, rows)
        return version, False
    if version != prepared["before_version"] or content_hash(rows) != prepared["before_hash"]:
        raise IntegrityError(
            "Resultado incierto: cambió el destino sin evidencia de esta operación"
        )
    if scoped_mia:
        verify_files(table, prepared, after=False)
    if not prepared["inserts"] and not prepared.get("updates"):
        _confirm_prepared(control, batch["batch_id"], version, prepared, rows)
        return version, False
    del rows  # Do not retain a second historical snapshot during append/confirmation.
    control.mark(batch["batch_id"], "WRITING")
    try:
        if config.family in {
            "MP9_CAMBIO_MODELO",
            "CATALOGO_PRODUCCIONES",
            "LISTA_AR",
            "ASIGNACION_PRODUCCION_RAMAL",
            "SIP_MAQUINA_MOLDEO",
            "SIP_LINEA_ZF",
        }:
            store.apply_changes(prepared["inserts"], prepared["updates"], operation, version)
        else:
            store.insert(prepared["inserts"], operation, version)
        table = store.table()
        after_version, after = store.read(table)
        expected = 0 if version is None else version + 1
        history = (table.history(1) if scoped_zc else table.history()) if table else []
        if (
            after_version != expected
            or content_hash(after) != prepared["after_hash"]
            or not any(h.get("ingesta_operation_id") == operation for h in history)
        ):
            raise IntegrityError("La confirmación Delta no corresponde a la intención preparada")
        if scoped_mia:
            verify_files(table, prepared, after=True)
        elif scoped_zc:
            store.verify_commit(table, table.version(), prepared)
        _confirm_prepared(control, batch["batch_id"], after_version, prepared, after)
        return after_version, True
    except Exception as exc:
        # También se usa UNKNOWN si el cliente lanza después de confirmar Delta.
        # El próximo arranque reconciliará antes de volver a escribir.
        control.mark(batch["batch_id"], "UNKNOWN")
        raise IngestaError(f"Operación pendiente de reconciliación: {operation}: {exc}") from exc


def _process(
    config: Config, control: Control, attempt: str, stop_requested=None, lane="all"
) -> dict:
    if config.family in {"SIP_MAQUINA_MOLDEO", "SIP_LINEA_ZF"}:
        from .topology_service import process

        return process(config, control, attempt)
    if config.family == "ASIGNACION_PRODUCCION_RAMAL":
        from .association_service import process

        return process(config, control, attempt)
    if config.family == "CONT_ZC":
        from .zc_service import process

        return process(config, control, attempt, lane=lane)
    if config.family == "LISTA_AR":
        from .ar_service import process

        return process(config, control, attempt)
    if config.family == "LISTA_RECH":
        from .rejections_service import process

        return process(config, control, attempt, stop_requested=stop_requested, lane=lane)
    if config.family == "LISTA_DEFECTOS":
        from .defects_service import process

        return process(config, control, attempt, stop_requested=stop_requested, lane=lane)
    if config.family == "CATALOGO_PRODUCCIONES":
        from .catalog_service import process

        return process(config, control, attempt)
    if config.family == "MP9_CAMBIO_MODELO":
        from .mp9_service import process

        return process(config, control, attempt)
    contract = config.contract
    store = DeltaStore(config, control.control_id)
    recovered = []
    inserted_recovery = 0
    for pending in control.pending():
        version, wrote = _apply(config, control, store, pending)
        count = len(control.prepared(pending)["inserts"])
        if wrote:
            inserted_recovery += count
        recovered.append(
            {
                "operation_id": pending["operation_id"],
                "delta_version": version,
                "confirmed_rows": count,
                "wrote_this_attempt": wrote,
            }
        )
    version, existing = store.read()
    recorded = control.setting("last_version")
    if recorded is not None and recorded != str(version):
        raise IntegrityError("El snapshot Delta no coincide con la última confirmación del control")
    snapshots = capture_inputs(config, control, attempt)
    common = {
        "attempt_id": attempt,
        "contract": contract.name,
        "plant": config.plant,
        "source_files": len(snapshots),
        "table_path": str(config.table_path),
        "control_path": str(config.control_path),
        "delta_version": version,
        "recovered_operations": recovered,
        "inserted_this_attempt": inserted_recovery,
    }
    if not snapshots:
        reviews = control.db.execute(
            "SELECT count(*) FROM issues WHERE status!='RESOLVED'"
        ).fetchone()[0]
        return {
            **common,
            "status": "NO_INPUTS",
            "batch_id": None,
            "open_reviews": reviews,
            "quality_complete": not reviews,
        }
    batch_id = digest(
        [
            contract.name,
            __version__,
            config.plant,
            config.encoding,
            sorted({(s.origin, s.sha256) for s in snapshots}),
        ]
    )
    if control.setting("review_epoch"):
        batch_id = digest([batch_id, control.setting("review_epoch")])
    prior = control.batch(batch_id)
    if prior and prior["state"] == "COMPLETED":
        original = control.prepared(prior)
        return {
            **common,
            "batch_id": batch_id,
            "status": "NO_CHANGE",
            **original.get(
                "stats",
                {"rows_read": original["rows_read"], "unique_pairs": original["unique_pairs"]},
            ),
            "new_entries": 0,
            **(
                {"matched_entries": original["unique_pairs"], "conflict_entries": 0}
                if config.family in {"SIP_DEFECTO", "SIP_DEFECTO_STATUS", "SIP_DEFECTO_AR"}
                else {}
            ),
            "issues": 0,
        }
    parsed = contract.parse(snapshots, config.encoding)
    from .review_adapters import filter_rejected

    filter_rejected(control, parsed)
    inserts, issues = contract.decide(parsed, existing)
    stats = contract.statistics(parsed)
    decision_stats = {"new_entries": len(inserts)}
    if config.family in {"SIP_DEFECTO", "SIP_DEFECTO_STATUS", "SIP_DEFECTO_AR"}:
        decision_stats.update(
            matched_entries=sum(existing.get(key[0]) == key[1:] for key in parsed.pairs),
            conflict_entries=sum(i["reason"] == "ASSOCIATION_CONFLICT" for i in issues),
        )
    if config.family == "SIP_DEFECTO":
        control.exclusions(batch_id, parsed.exclusions, config.plant)
    merged = {**existing, **{r[contract.key]: contract.value(r) for r in inserts}}
    prepared = {
        "contract": contract.name,
        "transformer_version": __version__,
        "encoding": config.encoding,
        "snapshots": [s.descriptor() for s in snapshots],
        "before_version": version,
        "before_hash": contract.content_hash(existing),
        "after_hash": contract.content_hash(merged),
        "inserts": inserts,
        **stats,
        "stats": stats,
        "decision_stats": decision_stats,
    }
    common.update(batch_id=batch_id, **stats, **decision_stats, issues=len(issues))
    if issues:
        # Primer incremento conservador: ningún dato de este lote se escribe si hay incidencias.
        control.rejected(batch_id, prepared, issues, config.plant)
        return {**common, "status": "BLOCKED"}
    if prior:
        raise IntegrityError("Estado previo de lote incompatible con su reevaluación")
    operation = digest([batch_id, f"{config.family.lower()}_insert_v1"])
    control.prepare(batch_id, operation, prepared)
    version, wrote = _apply(config, control, store, control.batch(batch_id))
    return {
        **common,
        "status": "APPLIED" if wrote else "NO_CHANGE",
        "delta_version": version,
        "inserted_this_attempt": inserted_recovery + (len(inserts) if wrote else 0),
    }


def control_type(config):
    control_class = Control
    if config.family in {"SIP_MAQUINA_MOLDEO", "SIP_LINEA_ZF"}:
        from .topology_storage import TopologyControl

        control_class = TopologyControl
    elif config.family == "MP9_CAMBIO_MODELO":
        from .mp9_control import Mp9Control

        control_class = Mp9Control
    elif config.family == "CATALOGO_PRODUCCIONES":
        from .catalog_control import CatalogControl

        control_class = CatalogControl
    elif config.family == "ASIGNACION_PRODUCCION_RAMAL":
        from .association_storage import AssociationControl

        control_class = AssociationControl
    elif config.family == "CONT_ZC":
        from .zc_storage import ZcControl

        control_class = ZcControl
    elif config.family == "LISTA_AR":
        from .ar_control import ArControl

        control_class = ArControl
    elif config.family == "LISTA_RECH":
        from .rejections_storage import RejectionsControl

        control_class = RejectionsControl
    elif config.family == "LISTA_DEFECTOS":
        from .defects_control import DefectsControl

        control_class = DefectsControl
    return control_class


def run(
    config: Config,
    *,
    runtime_owner: bool = False,
    stop_requested=None,
    reviews_only=False,
    lane="all",
) -> dict:
    config.validate()
    for path in (config.bronze_root, config.silver_root, config.control_root):
        path.mkdir(parents=True, exist_ok=True)
    control_class = control_type(config)
    # El bloqueo sigue al destino Silver, no a un nombre de fichero de entrada.
    # Dos configuraciones con controles distintos tampoco pueden escribir a la vez.
    try:
        with ExitStack() as gates:
            if not runtime_owner:
                gates.enter_context(
                    portalocker.Lock(str(config.silver_root / ".runtime.lock"), mode="a", timeout=0)
                )
                gates.enter_context(
                    portalocker.Lock(str(config.silver_root / ".writer.lock"), mode="a", timeout=0)
                )
            gates.enter_context(
                portalocker.Lock(
                    str(config.silver_root / f".writer-{config.plant}-{config.family}.lock"),
                    mode="a",
                    timeout=0,
                )
            )
            with closing(control_class(config)) as control:
                from .pipeline_retirement import recover

                recover(config, control)
                from . import retry_control

                if reviews_only:
                    from .reviews import process as process_reviews
                    from .supervision_export import export

                    if control.pending():
                        result = {"status": "REVIEW_WAITING_RECOVERY", "attempted": False}
                    else:
                        process_reviews(config, control)
                        result = {"status": "REVIEWS_CHECKED", "attempted": False}
                    export(control, config, result, force=True)
                    return result
                if control.compact and runtime_owner and not config.scheduled_lot_attempt:
                    waiting = retry_control.ready(control)
                    if waiting:
                        from .supervision_export import export

                        export(control, config, waiting)
                        return waiting
                attempt = control.start_attempt()
                try:
                    from .reviews import process as process_reviews

                    process_reviews(config, control)
                    result = _process(
                        config, control, attempt, stop_requested=stop_requested, lane=lane
                    )
                    from .repeated_hour import process as resolve_repeated_hour

                    try:
                        result = resolve_repeated_hour(config, control, result)
                    except OSError as exc:
                        raise IngestaError("Repeated-hour policy requires recovery") from exc
                    if hasattr(control, "input_scan_performed"):
                        result["input_scan_performed"] = control.input_scan_performed
                        result["input_scan_interval_seconds"] = config.input_scan_interval_seconds
                        scan = json.loads(control.setting("last_input_scan") or "null")
                        result["next_input_scan_at"] = (
                            scan["started"] + config.input_scan_interval_seconds
                            if scan and config.input_scan_interval_seconds
                            else None
                        )
                except Exception as exc:
                    if control.compact:
                        retry_control.failed(control, exc)
                    control.finish_attempt(attempt, {"status": "FAILED", "error": str(exc)})
                    raise
                if control.compact:
                    retry_control.succeeded(control)
                control.finish_attempt(attempt, result)
                return result
    except portalocker.exceptions.LockException as exc:
        raise IngestaError("Ya existe otro escritor para este destino Silver") from exc
