"""Consume producer-confirmed attribution decisions, including transition exceptions."""

from __future__ import annotations

from .common import IntegrityError, digest
from .context import stamp

KEY_FIELDS = {
    "LISTA_DEFECTOS": ("FECHA", "MOLDE", "LINEA", "COD_MAQ_ZF", "COD_DEF", "INSPECC", "CAVIDAD"),
    "LISTA_RECH": ("FECHA", "MOLDE", "LINEA", "COD_MAQ_ZF", "INSPECC", "CAVIDAD"),
    "LISTA_AR": (
        "COD_AR",
        "FECHA_ON",
        "MAQ_IS",
        "CAVIDAD",
        "STATUS",
        "TIPO",
        "MODELO",
        "COLOR",
        "PROCESO",
        "MOLDE",
        "ID_Agrupación",
    ),
    "CONT_ZC": ("MAQ_IS", "FECHA"),
}
ACCEPTED = {
    "ASSIGNED",
    "OLD_PLUS_PREPARATION",
    "NEW_PLUS_PREPARATION",
    "SHARED_MACHINE",
    "TRANSITION_TO_OUTGOING",
}


def source_id(family: str, row: dict) -> str:
    # Same public identity contract as the producer, with local ISO timestamps.
    key = [row["origen_fenix"], *(row[f] for f in KEY_FIELDS[family])]
    return digest([family, key])


class Attribution:
    def __init__(self, decisions: list[dict], productions: list[dict]):
        self.productions = {p["id_produccion"]: p for p in productions}
        self.decisions: dict[tuple[str, str], dict] = {}
        for d in decisions:
            key = (d["source_family"], d["source_id"])
            if key in self.decisions:
                raise IntegrityError("An observation has multiple attribution decisions")
            self.decisions[key] = d

    def targets(self, family: str, row: dict) -> tuple[list[dict], dict]:
        """Return (target productions, decision). Pending rows carry a synthetic decision."""
        identity = source_id(family, row)
        decision = self.decisions.get((family, identity))
        if decision is None or decision["status"] not in ACCEPTED:
            return [], {
                "source": family,
                "source_id": identity,
                "estado": "WAITING_DEPENDENCY",
                "reason_code": decision["status"] if decision else "ATTRIBUTION_MISSING",
            }
        targets = decision["productions"]
        if len(targets) > 1 and family != "CONT_ZC":
            raise IntegrityError("Only shared ZC physical measurements may have multiple targets")
        if not targets or any(target not in self.productions for target in targets):
            raise IntegrityError("Confirmed attribution refers to no/missing production")
        return [self.productions[t] for t in targets], decision


def interval(row: dict, minutes=10):
    from datetime import timedelta

    end = stamp(row["FECHA"])
    return end - timedelta(minutes=minutes), end
