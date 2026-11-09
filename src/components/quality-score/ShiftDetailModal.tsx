import type { ShiftCustomerScore } from "@/types/api";
import { formatDay, formatNumber, formatTime } from "@/lib/format";

import { AqlDetail } from "./AqlDetail";
import { ComponentBreakdown } from "./ComponentBreakdown";
import { DarZcDetail } from "./DarZcDetail";
import { STATUS_META } from "./meta";
import { MiaDetail } from "./MiaDetail";
import { CloseButton, Modal } from "./Modal";
import { StatusBadge } from "./StatusBadge";
import { SummaryCard } from "./SummaryCard";

const TOP_COMPONENTS = 4;

export function ShiftDetailModal({
  productionId,
  shift,
  onClose,
}: {
  productionId: string;
  shift: ShiftCustomerScore;
  onClose: () => void;
}) {
  return (
    <Modal
      layer="nested"
      onClose={onClose}
      title={
        <>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              Detalle de turno
            </p>
            <h3 className="mt-1 text-base font-semibold text-gray-900">
              {productionId} - {formatDay(shift.operational_day)} -{" "}
              {shift.shift_name}
            </h3>
            <p className="mt-1 text-xs text-gray-500">
              {formatTime(shift.start)}-{formatTime(shift.end)} · Penalizacion{" "}
              {formatNumber(shift.total_penalty)} · Score norm.{" "}
              {shift.normalized_score.toFixed(2)} · Peso{" "}
              {formatNumber(shift.evaluated_weight)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge status={shift.status} />
            <CloseButton onClick={onClose} />
          </div>
        </>
      }
    >
      <div className="space-y-4 p-5">
        <ShiftDetailSummary shift={shift} />
        <section className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
          <article className="min-w-0 rounded-lg border border-gray-200 p-4">
            <ComponentBreakdown row={shift} />
            <AqlDetail rows={shift.aql_categories} />
          </article>
          <article className="min-w-0 rounded-lg border border-gray-200 p-4">
            <MiaDetail rows={shift.mia_details} />
            <DarZcDetail row={shift} />
          </article>
        </section>
      </div>
    </Modal>
  );
}

function ShiftDetailSummary({ shift }: { shift: ShiftCustomerScore }) {
  const mainCause = shift.main_causes[0];
  const topComponents = [...shift.components]
    .filter((component) => component.contribution > 0)
    .sort((left, right) => right.contribution - left.contribution)
    .slice(0, TOP_COMPONENTS);
  return (
    <section className="grid grid-cols-1 gap-3 md:grid-cols-4">
      <SummaryCard
        variant="compact"
        label="Penalizacion"
        value={formatNumber(shift.total_penalty)}
      />
      <SummaryCard
        variant="compact"
        label="Estado"
        value={STATUS_META[shift.status]?.label ?? shift.status}
      />
      <SummaryCard
        variant="compact"
        label="Causa principal"
        value={mainCause?.label ?? "-"}
        detail={
          mainCause
            ? `${formatNumber(mainCause.contribution)} puntos`
            : undefined
        }
      />
      <SummaryCard
        variant="compact"
        label="Top componentes"
        value={
          topComponents.length > 0
            ? topComponents
                .map(
                  (component) =>
                    `${component.label} ${formatNumber(component.contribution)}`,
                )
                .join(" · ")
            : "-"
        }
      />
    </section>
  );
}
