import type {
  ProductionCatalog,
  ProductionQualityScoreResponse,
} from "@/types/api";
import { formatDay, formatNumber } from "@/lib/format";

import { ComponentCells, ComponentHeaders } from "./ComponentCells";
import type { CustomerLabel } from "./meta";
import { formatMainCauses } from "./model";
import { PenaltyBubbleChart } from "./PenaltyBubbleChart";
import { StatusBadge } from "./StatusBadge";

export function ScoreTable({
  productions,
  result,
  customerQualityLabels,
  onSelectProduction,
}: {
  productions: ProductionCatalog[];
  result: ProductionQualityScoreResponse;
  customerQualityLabels: Record<string, CustomerLabel>;
  onSelectProduction: (productionId: string) => void;
}) {
  const productionById = new Map(productions.map((item) => [item.id, item]));
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="mb-3">
        <h3 className="text-sm font-semibold text-gray-900">
          Ranking de calidad de produccion
        </h3>
        <p className="mt-1 text-xs text-gray-500">
          Ordenado por penalizacion total descendente. Pulsa una produccion o
          una burbuja para abrir el desglose por turno.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-[1040px] text-left text-xs">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-3 py-2">Produccion</th>
              <th className="px-3 py-2">Fechas</th>
              <th className="px-3 py-2 text-right">Penalizacion</th>
              <th className="px-3 py-2">Estado</th>
              <ComponentHeaders />
              <th className="px-3 py-2">Causa principal</th>
            </tr>
          </thead>
          <tbody>
            {result.productions.map((row) => {
              const production = productionById.get(row.production_id);
              return (
                <tr
                  key={row.production_id}
                  className="border-t border-gray-100"
                >
                  <td className="px-3 py-2 font-semibold text-gray-800">
                    <button
                      type="button"
                      className="font-semibold text-primary underline-offset-2 hover:underline"
                      onClick={() => onSelectProduction(row.production_id)}
                    >
                      {row.production_id}
                    </button>
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 text-gray-600">
                    {production
                      ? `${formatDay(production.start_day)}-${formatDay(production.end_day)}`
                      : "-"}
                  </td>
                  <td className="px-3 py-2 text-right font-semibold tabular-nums">
                    {formatNumber(row.total_penalty)}
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge status={row.status} />
                  </td>
                  <ComponentCells row={row} />
                  <td className="max-w-sm px-3 py-2 text-gray-700">
                    {formatMainCauses(row.main_causes)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <PenaltyBubbleChart
        result={result}
        productions={productions}
        customerQualityLabels={customerQualityLabels}
        onSelectProduction={onSelectProduction}
      />
    </section>
  );
}
