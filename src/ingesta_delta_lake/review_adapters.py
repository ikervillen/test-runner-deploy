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
ADMIT = "ACEPTAR_CANDIDATO"
VERSION = "1.0.0"
ADMISSION_VERSION = "1.1.0"
REASONS = {
    "ASSOCIATION_CONFLICT",
    "CONFLICT",
    "REVIEW_REQUIRED",
    "ROW_CONFLICT",
    "CLOSURE_CONFLICT",
    "COUNTER_REVISION",
    "INSPECTION_GROUP_CONFLICT",
    "RELATION_CONFLICT",
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
    """Describe a concrete choice, never infer a winner from evidence order."""
    reason = payload.get("reason", "UNKNOWN")
    dependency = reason.endswith("_PENDING") or reason.endswith("_DEPENDENCY")
    code = "DEPENDENCY_PENDING" if dependency else "ACTION_NOT_IMPLEMENTED"
    unavailable = {
        "version": VERSION,
        "fields": config.contract.schema.names,
        "actions": [],
        "category": "DEPENDENCY" if dependency else "CONTRACT_REVIEW",
        "blockers": [
            {
                "code": code,
                "requirement": reason,
                "owner": "responsable de datos" if dependency else "equipo técnico",
                "next_step": "Incorporar la dependencia y reevaluar la incidencia."
                if dependency
                else "Corregir el origen o implementar el contrato indicado.",
            }
        ],
        "message": f"{reason}: "
        + (
            "falta una dependencia de origen; no se puede forzar su aceptación."
            if dependency
            else "este motivo requiere validación de su contrato de resolución."
        ),
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
            if len(accepted) > 1:
                return {
                    **unavailable,
                    "message": "Existen varios registros aceptados; "
                    "debe resolverse la ambigüedad de identidad.",
                    "blockers": [
                        {
                            "code": "IDENTITY_AMBIGUOUS",
                            "next_step": "Revisar la integridad de las identidades aceptadas.",
                            "owner": "equipo técnico",
                        }
                    ],
                }
            accepted = accepted[0] if accepted else None
        old = a.project(accepted) if isinstance(accepted, dict) else None
        candidates = payload.get("candidates", [payload.get("candidate", payload)])
        variants = {a.signature(r): a.project(r) for r in candidates}
        if not variants:
            return unavailable
        if old is not None:
            variants.pop(a.signature(old), None)
        if not variants:
            return unavailable
        target = old if old is not None else next(iter(variants.values()))
        same_key = all(a.key(r) == a.key(target) for r in variants.values())
        if old is None and not same_key:
            return {
                **unavailable,
                "message": "Los candidatos pertenecen a identidades distintas; "
                "no se puede admitir uno como homólogo.",
                "blockers": [
                    {
                        "code": "IDENTITY_AMBIGUOUS",
                        "next_step": "Separar las identidades antes de decidir.",
                        "owner": "responsable de datos",
                    }
                ],
            }
        actions = [KEEP] if old is not None else [ADMIT]
        if old is not None and same_key:
            actions.append(REPLACE)
        return {
            "version": VERSION if old is not None else ADMISSION_VERSION,
            "fields": config.contract.schema.names,
            "actions": actions,
            "category": "ACCEPTED_CONFLICT" if old is not None else "CANDIDATE_SELECTION",
            "identity": digest(a.key(target)),
            "blockers": [],
            "action_blockers": (
                {
                    REPLACE: [
                        {
                            "code": "IDENTITY_CHANGE",
                            "message": "La alternativa cambia la clave; "
                            "no es una sustitución del mismo registro.",
                            "next_step": "Mantener el dato aceptado o corregir "
                            "la identidad en el origen.",
                            "owner": "responsable de datos",
                        }
                    ]
                }
                if old is not None and not same_key
                else {}
            ),
            "issue_hash": digest(payload),
            "accepted": old,
            "accepted_hash": a.signature(old) if old is not None else None,
            "candidates": [{"hash": h, "row": variants[h]} for h in sorted(variants)],
        }
    except (KeyError, TypeError, IntegrityError, ValueError):
        return {
            **unavailable,
            "message": "La evidencia no contiene una fila completa compatible con el contrato.",
            "blockers": [
                {
                    "code": "EVIDENCE_INCOMPLETE",
                    "next_step": "Obtener la evidencia íntegra desde el productor.",
                    "owner": "equipo técnico",
                }
            ],
        }


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


def contextual_offers(config, payloads):
    """Union complete candidate rows for the same object and expected accepted state.

    Some detectors emit one issue per candidate. A choice must see all related
    alternatives before it can reject them, and bind that coverage into its hash.
    This index is built once per bounded export or writer decision, not per row.
    """
    offers, buckets = {}, {}
    for payload in payloads:
        identity = digest(payload)
        choice = offer(config, payload)
        offers[identity] = choice
        if choice["actions"]:
            bucket = (choice["identity"], choice["accepted_hash"])
            target = buckets.setdefault(bucket, {})
            target.update({r["hash"]: r["row"] for r in choice["candidates"]})
    for choice in offers.values():
        if not choice["actions"]:
            continue
        variants = buckets[(choice["identity"], choice["accepted_hash"])]
        if set(variants) != {r["hash"] for r in choice["candidates"]}:
            choice["candidates"] = [{"hash": h, "row": variants[h]} for h in sorted(variants)]
            choice["issue_hash"] = digest([choice["issue_hash"], sorted(variants)])
        choice["coverage_complete"] = True
    return offers
