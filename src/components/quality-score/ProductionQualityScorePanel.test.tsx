import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProductionQualityScorePanel } from "./ProductionQualityScorePanel";
import type { ProductionQualityScoreResponse, TrendQuery } from "@/types/api";

describe("Gold availability", () => {
  it("shows pending dependencies and never presents missing scores as zero", () => {
    const result: ProductionQualityScoreResponse = {
      status: "pending",
      productions: [],
      warnings: [],
      pending_productions: [
        { production_id: "p1", reasons: ["LISTA_RECH_BASE_MISSING"] },
      ],
      meta: {
        query_time_ms: 1,
        config_version: 1,
        files_read: 1,
        rows_scanned: 1,
        effective_config: {},
        source: "gold",
        publication_id: "one",
      },
    };
    const selection: TrendQuery = {
      production_ids: ["p1"],
      branches: [],
      metric: "rejected_count",
      window: "1h",
      filters: {
        defect_codes: [],
        cavities: [],
        colors: [],
        sections: [],
        machine_codes: [],
      },
    };
    render(
      <ProductionQualityScorePanel
        error={null}
        isLoading={false}
        isStale={false}
        result={result}
        selection={selection}
        productions={[]}
        onCalculate={vi.fn()}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Falta una base válida de botellas inspeccionadas",
    );
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });
});
