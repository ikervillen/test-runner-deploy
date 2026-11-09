import type { ProductionQualityScoreResponse } from "@/types/api";
import { formatNumber } from "@/lib/format";

import { SummaryCard } from "./SummaryCard";

export function ScoreSummary({
  result,
}: {
  result: ProductionQualityScoreResponse;
}) {
  const worst = result.productions[0] ?? null;
  const count = result.productions.length;
  const average = count
    ? result.productions.reduce(
        (total, row) => total + row.normalized_score,
        0,
      ) / count
    : 0;
  return (
    <section className="grid grid-cols-1 gap-3 md:grid-cols-4">
      <SummaryCard label="Producciones" value={count.toString()} />
      <SummaryCard
        label="Peor penalizacion"
        value={worst ? formatNumber(worst.total_penalty) : "-"}
      />
      <SummaryCard
        label="Score medio 0-10"
        value={count ? average.toFixed(2) : "-"}
      />
      <SummaryCard
        label="Config"
        value={`v${result.meta.config_version}`}
        detail={`${result.meta.files_read} archivos leidos`}
      />
    </section>
  );
}
