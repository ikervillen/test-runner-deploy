import type {
  ProductionCatalog,
  ProductionQualityScoreResponse,
} from "@/types/api";
import { formatNumber } from "@/lib/format";

import { CUSTOMER_QUALITY_META, type CustomerLabel } from "./meta";
import { productionDurationDays, scaledBubbleSizes } from "./model";

// Horizontal axis geometry in percent of the container width / px from bottom.
const AXIS_START = 5;
const AXIS_WIDTH = 90;
const AXIS_BOTTOM = 92;

export function PenaltyBubbleChart({
  result,
  productions,
  customerQualityLabels,
  onSelectProduction,
}: {
  result: ProductionQualityScoreResponse;
  productions: ProductionCatalog[];
  customerQualityLabels: Record<string, CustomerLabel>;
  onSelectProduction: (productionId: string) => void;
}) {
  const productionById = new Map(
    productions.map((production) => [production.id, production]),
  );
  const points = result.productions.map((row) => {
    const production = productionById.get(row.production_id);
    return {
      row,
      days: production ? productionDurationDays(production) : 1,
      customerLabel: customerQualityLabels[row.production_id] ?? "unknown",
    };
  });
  const sizes = scaledBubbleSizes(points.map((point) => point.days));
  const penalties = points.map((point) => point.row.total_penalty);
  const minPenalty = Math.min(...penalties);
  const maxPenalty = Math.max(...penalties);
  const span = Math.max(maxPenalty - minPenalty, 1);
  const presentLabels = (
    Object.keys(CUSTOMER_QUALITY_META) as CustomerLabel[]
  ).filter((label) => points.some((point) => point.customerLabel === label));

  return (
    <div className="mt-4 border-t border-gray-100 pt-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        Mapa de penalizacion
      </h4>
      <p className="mt-1 text-xs text-gray-500">
        Eje: penalizacion. Tamano: dias de produccion. Color: semaforo de
        reclamaciones.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-gray-600">
        <span className="font-semibold text-gray-700">Calidad cliente</span>
        {presentLabels.map((label) => (
          <span key={label} className="inline-flex items-center gap-1.5">
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: CUSTOMER_QUALITY_META[label].color }}
            />
            {CUSTOMER_QUALITY_META[label].label}
          </span>
        ))}
      </div>
      <div className="relative mt-2 h-56 overflow-visible">
        <div
          className="absolute h-0.5 bg-gray-950"
          style={{
            left: `${AXIS_START}%`,
            right: `${100 - AXIS_START - AXIS_WIDTH}%`,
            bottom: AXIS_BOTTOM,
          }}
        />
        <div
          className="absolute h-0 w-0 border-y-[6px] border-l-[10px] border-y-transparent border-l-gray-950"
          style={{
            left: `${AXIS_START + AXIS_WIDTH}%`,
            bottom: AXIS_BOTTOM - 5,
          }}
        />
        {points.map((point, index) => {
          const size = sizes[index];
          const position =
            AXIS_START +
            ((point.row.total_penalty - minPenalty) / span) * AXIS_WIDTH;
          const meta = CUSTOMER_QUALITY_META[point.customerLabel];
          return (
            <button
              type="button"
              key={point.row.production_id}
              className="absolute rounded-full border-2 border-white shadow-sm transition hover:scale-105 focus:outline-none focus:ring-2 focus:ring-primary"
              style={{
                left: `${position}%`,
                bottom: AXIS_BOTTOM - size / 2,
                width: size,
                height: size,
                backgroundColor: meta.color,
                transform: "translateX(-50%)",
              }}
              title={`${point.row.production_id} - penalizacion ${formatNumber(
                point.row.total_penalty,
              )} - ${point.days.toFixed(1)} dias - ${meta.label}`}
              onClick={() => onSelectProduction(point.row.production_id)}
            >
              <span
                className="absolute left-1/2 whitespace-nowrap text-[10px] font-medium text-gray-800"
                style={{ bottom: size + 4, transform: "translateX(-50%)" }}
              >
                {point.row.production_id}
              </span>
            </button>
          );
        })}
        <div
          className="absolute text-xs text-gray-700"
          style={{ left: `${AXIS_START}%`, bottom: AXIS_BOTTOM - 34 }}
        >
          {formatNumber(minPenalty)}
        </div>
        <div
          className="absolute text-xs text-gray-700"
          style={{
            left: `${AXIS_START + AXIS_WIDTH}%`,
            bottom: AXIS_BOTTOM - 34,
            transform: "translateX(-100%)",
          }}
        >
          {formatNumber(maxPenalty)}
        </div>
        <div
          className="absolute left-1/2 -translate-x-1/2 text-xs font-medium text-gray-800"
          style={{ bottom: AXIS_BOTTOM - 58 }}
        >
          Penalizacion total de calidad
        </div>
      </div>
    </div>
  );
}
