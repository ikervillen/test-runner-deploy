from __future__ import annotations

from collections import defaultdict

from app.domain.customer_quality import (
    ComplaintRecord,
    ComplaintTreatment,
    CustomerQualityAnalysis,
    CustomerQualityLabel,
    CustomerQualityValidationRow,
    ProductionCustomerQuality,
)
from app.domain.models import Production


class StaticCustomerQualityRepository:
    """Seeded customer-complaint catalog until the structured source is available."""

    def __init__(self) -> None:
        self._records = _seed_records()

    def analyze(
        self,
        productions: tuple[Production, ...],
    ) -> CustomerQualityAnalysis:
        by_production: dict[str, list[ComplaintRecord]] = defaultdict(list)
        for record in self._records:
            by_production[record.production_id].append(record)

        rows = tuple(
            _quality_row(production.id, tuple(by_production.get(production.id, ())))
            for production in productions
        )
        return CustomerQualityAnalysis(
            rows=rows,
            validation_rows=tuple(_validation_row(row) for row in rows),
            warnings=(
                "Catalogo de reclamaciones cargado como semilla manual desde la tabla "
                "validada visualmente; sustituir por fuente estructurada cuando exista.",
            ),
        )


def _quality_row(
    production_id: str,
    complaints: tuple[ComplaintRecord, ...],
) -> ProductionCustomerQuality:
    relevant = tuple(item for item in complaints if item.treatment == ComplaintTreatment.RELEVANT)
    observed = tuple(item for item in complaints if item.treatment == ComplaintTreatment.OBSERVED)
    excluded = tuple(item for item in complaints if item.treatment == ComplaintTreatment.EXCLUDED)
    label = _label_for(relevant, observed)
    worst_reason = _worst_reason(relevant, observed)
    return ProductionCustomerQuality(
        production_id=production_id,
        label=label,
        relevant_count=len(relevant),
        observed_count=len(observed),
        excluded_count=len(excluded),
        worst_reason=worst_reason,
        explanation=_explanation(label, relevant, observed, excluded),
        complaints=complaints,
    )


def _label_for(
    relevant: tuple[ComplaintRecord, ...],
    observed: tuple[ComplaintRecord, ...],
) -> CustomerQualityLabel:
    if len(relevant) >= 3:
        return CustomerQualityLabel.CRITICAL
    if len(relevant) >= 2:
        return CustomerQualityLabel.BAD
    if len(relevant) == 1:
        return CustomerQualityLabel.BAD
    if observed:
        return CustomerQualityLabel.OBSERVED
    return CustomerQualityLabel.GOOD


def _worst_reason(
    relevant: tuple[ComplaintRecord, ...],
    observed: tuple[ComplaintRecord, ...],
) -> str | None:
    if relevant:
        return relevant[0].defect_description
    if observed:
        return observed[0].defect_description
    return None


def _explanation(
    label: CustomerQualityLabel,
    relevant: tuple[ComplaintRecord, ...],
    observed: tuple[ComplaintRecord, ...],
    excluded: tuple[ComplaintRecord, ...],
) -> str:
    if label == CustomerQualityLabel.CRITICAL:
        return "Varias reclamaciones relevantes de producto; produccion prioritaria."
    if label == CustomerQualityLabel.BAD:
        return "Existe al menos una reclamacion relevante de producto."
    if label == CustomerQualityLabel.OBSERVED:
        return (
            "Reclamacion observada sin fallo confirmado frente a especificacion; "
            "conviene revisarla contra metricas internas."
        )
    if excluded:
        return "Sin reclamaciones imputables a fabricacion; existen incidencias excluidas."
    return "Sin reclamaciones relevantes ni observadas."


def _validation_row(row: ProductionCustomerQuality) -> CustomerQualityValidationRow:
    if row.label in {CustomerQualityLabel.BAD, CustomerQualityLabel.CRITICAL}:
        validation_case = "reclamada"
        interpretation = "Usar como caso positivo para contrastar metricas internas."
    elif row.label == CustomerQualityLabel.OBSERVED:
        validation_case = "observada"
        interpretation = "Revisar sin tratarla como mala confirmada."
    else:
        validation_case = "sin_reclamacion_relevante"
        interpretation = "Usar como referencia buena salvo evidencia interna fuerte."
    return CustomerQualityValidationRow(
        production_id=row.production_id,
        customer_label=row.label,
        relevant_count=row.relevant_count,
        observed_count=row.observed_count,
        excluded_count=row.excluded_count,
        worst_reason=row.worst_reason,
        internal_risk_flag=False,
        validation_case=validation_case,
        interpretation=interpretation,
    )


def _seed_records() -> tuple[ComplaintRecord, ...]:
    return (
        ComplaintRecord(
            production_id="AV32_PROD_005",
            complaint_id="CDAV-25/29",
            defect_description="Espesor fuera de normas: talon",
            customer_group="Domaine Lafage SARL",
            treatment=ComplaintTreatment.RELEVANT,
            reason="Defecto de producto; incluido como reclamacion relevante.",
        ),
        ComplaintRecord(
            production_id="AV32_PROD_005",
            complaint_id="CDAV-25/30",
            defect_description="Fisura en acuerdo-talon",
            customer_group="Goanvi Bottling, LDA",
            treatment=ComplaintTreatment.RELEVANT,
            reason="Defecto de producto; incluido como reclamacion relevante.",
        ),
        ComplaintRecord(
            production_id="AV32_PROD_005",
            complaint_id="CDAV-25/36",
            defect_description="Fisura fondo",
            customer_group="Grupo Chevallier + VDS",
            treatment=ComplaintTreatment.RELEVANT,
            reason="Defecto de producto; incluido como reclamacion relevante.",
        ),
        ComplaintRecord(
            production_id="AV32_PROD_005",
            complaint_id="CDAV-25/45",
            defect_description="Infundido < 1,0 mm",
            customer_group="Vinhos Vitor Matos II, SA",
            treatment=ComplaintTreatment.RELEVANT,
            reason="Defecto de producto; incluido como reclamacion relevante.",
        ),
        ComplaintRecord(
            production_id="AV32_PROD_008",
            complaint_id="CDAV-26/11",
            defect_description="Rompe a impacto ZCI",
            customer_group="Boutin Distribution",
            treatment=ComplaintTreatment.RELEVANT,
            reason="Defecto mayor de producto; incluido como reclamacion relevante.",
        ),
        ComplaintRecord(
            production_id="AV32_PROD_006",
            complaint_id="CDAV-25/50",
            defect_description="Cumple especificacion (indeterminado/cumple)",
            customer_group="Grupo Inveravante",
            treatment=ComplaintTreatment.OBSERVED,
            reason=(
                "No confirma mala fabricacion, pero se conserva como produccion "
                "observada para revisar criterios y metricas."
            ),
        ),
        ComplaintRecord(
            production_id="AV23_PROD_002",
            complaint_id="CDAV-24/41",
            defect_description="Etiqueta equivocada",
            customer_group="Verreries de Bourgogne",
            treatment=ComplaintTreatment.EXCLUDED,
            reason="Incidencia de etiqueta/embalaje; excluida de calidad de fabricacion.",
        ),
    )
