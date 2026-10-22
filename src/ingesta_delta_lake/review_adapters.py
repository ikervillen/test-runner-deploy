"""Explicit correction adapters. No free SQL, key edits or dependency overrides."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pyarrow as pa
import pyarrow.dataset as ds

from .common import IntegrityError, digest
from .contracts import DictionaryContract

KEEP = "MANTENER_PRIMER_DATO"
REPLACE = "SOBRESCRIBIR_CON_SEGUNDO"
VERSION = "1.0.0"
REASONS = {
    "ASSOCIATION_CONFLICT",
    "CONFLICT",
    "REVIEW_REQUIRED",
    "ROW_CONFLICT",
    "CLOSURE_CONFLICT",
    "COUNTER_REVISION",
    "INSPECTION_GROUP_CONFLICT",
}


@dataclass
class Adapter:
    config: object
    store: object
    key: object
    model: object = None

    def project(self, row):
        return {f: row[f] for f in self.config.contract.schema.names}

    def signature(self, row):
        return digest(self.project(row))

    def rows(self, rows):
        contract = self.config.contract
        if isinstance(contract, DictionaryContract):
            return {
                k: dict(
                    zip(
                        contract.schema.names,
                        (k, *((v,) if isinstance(v, str) else v)),
                        strict=True,
                    )
                )
                for k, v in rows.items()
            }
        return rows

    def arrow(self, rows):
        values = []
        for row in rows:
            item = self.project(row)
            for field in self.config.contract.schema:
                if item[field.name] is not None:
                    if pa.types.is_timestamp(field.type):
                        item[field.name] = datetime.fromisoformat(item[field.name])
                    elif pa.types.is_decimal(field.type):
                        item[field.name] = Decimal(item[field.name])
            values.append(item)
        return pa.Table.from_pylist(values, schema=self.config.contract.schema)

    def validate(self, rows):
        """Reuse published-read validators before committing any correction."""
        arrow = self.arrow(rows.values())

        class Snapshot:
            def to_pyarrow_table(self):
                return arrow

            def to_pyarrow_dataset(self, **kwargs):
                return ds.dataset(arrow)

            def version(self):
                return 0

        self.store.read(Snapshot())
        family = self.config.family
        if family == "MP9_CAMBIO_MODELO":
            from .mp9 import Parsed, decide
            from .mp9_storage import load_models

            models, _ = load_models(self.config)
            parsed = Parsed()
            for row in rows.values():
                parsed.add(row, [])
            if decide(parsed, rows, set(models), set())[2]:
                raise IntegrityError("Correction violates MP9 model/ZC/ZF relationships")
        elif family in {"LISTA_RECH", "LISTA_DEFECTOS"}:
            indexes = self.model.indexes(list(rows.values()))
            if any(
                len({r["INSPECC"] for r in values}) > 1
                for index in indexes[1:]
                for values in index.values()
            ):
                raise IntegrityError("Correction creates inconsistent inspection denominators")
        elif family == "SIP_LINEA_ZF":
            from . import topology
            from .topology_storage import snapshot

            machines, _, cases = snapshot(self.config, topology.MACHINES)
            codes = dict(self.config.line_code_by_origin)
            for row in rows.values():
                machine = topology.machine_for_period(
                    machines,
                    cases,
                    row["id_maquina_fenix"],
                    row["vigente_desde"],
                    row["vigente_hasta"],
                )
                if machine is None or (
                    row["origen_fenix"] in codes
                    and machine["codigo_linea_zc"] != codes[row["origen_fenix"]]
                ):
                    raise IntegrityError("Correction violates the branch/machine relationship")


def adapter(config, control_id):
    family = config.family
    if isinstance(config.contract, DictionaryContract):
        from .delta import DeltaStore

        return Adapter(config, DeltaStore(config, control_id), lambda r: r[config.contract.key])
    if family == "MP9_CAMBIO_MODELO":
        from . import mp9 as model
        from .mp9_storage import Mp9Store as Store
    elif family == "LISTA_AR":
        from . import lista_ar as model
        from .ar_storage import ArStore as Store
    elif family == "CONT_ZC":
        from . import cont_zc as model
        from .zc_storage import ZcStore as Store
    elif family == "LISTA_DEFECTOS":
        from . import lista_defectos as model
        from .defects_storage import DefectsStore as Store
    elif family == "LISTA_RECH":
        from . import lista_rech as model
        from .rejections_storage import RejectionsStore as Store
    elif family in {"SIP_MAQUINA_MOLDEO", "SIP_LINEA_ZF"}:
        from . import topology as model
        from .topology_storage import TopologyStore

        return Adapter(
            config, TopologyStore(config, control_id), lambda r: model.key(family, r), model
        )
    else:
        raise IntegrityError("Derived data must be regenerated, not overwritten manually")
    return Adapter(config, Store(config, control_id), model.key, model)


def offer(config, payload):
    """A decision requires one existing homologous row and explicit alternatives."""
    unavailable = {
        "version": VERSION,
        "fields": config.contract.schema.names,
        "actions": [],
        "message": "Este caso requiere información o resolver una dependencia; no tiene un par "
        "único de registros homólogos que pueda corregirse con estas acciones.",
    }
    if payload.get("reason") not in REASONS:
        return unavailable
    try:
        a = adapter(config, "projection-only")
        accepted = payload.get("accepted")
        if config.family == "SIP_MODELO" and payload.get("accepted_code") is not None:
            accepted = {
                config.contract.key: payload[config.contract.key],
                "codigo_modelo_botella": payload["accepted_code"],
            }
        if isinstance(accepted, list):
            accepted = accepted[0] if len(accepted) == 1 else None
        if not isinstance(accepted, dict):
            return unavailable
        old = a.project(accepted)
        candidates = payload.get("candidates", [payload.get("candidate", payload)])
        variants = {a.signature(r): a.project(r) for r in candidates}
        if not variants:
            return unavailable
        variants.pop(a.signature(old), None)
        if not variants:
            return unavailable
        actions = [KEEP]
        if all(a.key(r) == a.key(old) for r in variants.values()):
            actions.append(REPLACE)
        return {
            "version": VERSION,
            "fields": config.contract.schema.names,
            "actions": actions,
            "issue_hash": digest(payload),
            "accepted": old,
            "accepted_hash": a.signature(old),
            "candidates": [{"hash": h, "row": variants[h]} for h in sorted(variants)],
        }
    except (KeyError, TypeError, IntegrityError):
        return unavailable


def filter_rejected(control, parsed):
    """Reject only exact authorized variants; unfamiliar values retain normal checks."""
    rejected = {r[0] for r in control.db.execute("SELECT row_hash FROM review_rejections")}
    if not rejected:
        return
    config = control.config
    a = adapter(config, control.control_id)
    if isinstance(config.contract, DictionaryContract):
        parsed.pairs = {
            entry: refs
            for entry, refs in parsed.pairs.items()
            if a.signature(dict(zip(config.contract.schema.names, entry, strict=True)))
            not in rejected
        }
    elif config.family in {"SIP_MAQUINA_MOLDEO", "SIP_LINEA_ZF"}:
        parsed.entries = {
            k: item for k, item in parsed.entries.items() if a.signature(item[0]) not in rejected
        }
    else:
        parsed.entries = {
            entry: refs
            for entry, refs in parsed.entries.items()
            if a.signature(
                {"origen_fenix": entry[0], **dict(zip(a.model.FIELDS, entry[1:], strict=True))}
            )
            not in rejected
        }
