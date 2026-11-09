import type { ProductionQualityScoreResponse } from "@/types/api";
import { formatNumber } from "@/lib/format";

import { AqlDetail } from "./AqlDetail";
import { ComponentBreakdown } from "./ComponentBreakdown";
import { DarZcDetail } from "./DarZcDetail";
import { MiaDetail } from "./MiaDetail";
import { StatusBadge } from "./StatusBadge";

export function ScoreDetails({
  result,
}: {
  result: ProductionQualityScoreResponse;
}) {
  return (
    <section className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      {result.productions.map((row) => (
        <article
          key={row.production_id}
          className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-900">
                {row.production_id}
              </h3>
              <p className="mt-1 text-xs text-gray-500">
                Penalizacion {formatNumber(row.total_penalty)} - Score norm.{" "}
                {row.normalized_score.toFixed(2)} - Peso{" "}
                {formatNumber(row.evaluated_weight)}
              </p>
            </div>
            <StatusBadge status={row.status} />
          </div>
          <ComponentBreakdown row={row} />
          <AqlDetail rows={row.aql_categories} />
          <MiaDetail rows={row.mia_details} />
          <DarZcDetail row={row} />
        </article>
      ))}
    </section>
  );
}
