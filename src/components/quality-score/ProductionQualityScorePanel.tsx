import { useState } from "react";

import type {
  ProductionCatalog,
  ProductionQualityScoreResponse,
  TrendQuery,
} from "@/types/api";

import type { CustomerLabel } from "./meta";
import { ScoreDetails } from "./ScoreDetails";
import {
  GoldSourceNote,
  PendingProductions,
  ScoreWarnings,
} from "./ScoreNotices";
import { ScoreSummary } from "./ScoreSummary";
import { ScoreTable } from "./ScoreTable";
import { ShiftScoreModal } from "./ShiftScoreModal";

export function ProductionQualityScorePanel({
  error,
  isLoading,
  isStale,
  result,
  selection,
  productions,
  customerQualityLabels = {},
  onCalculate,
}: {
  error: string | null;
  isLoading: boolean;
  isStale: boolean;
  result: ProductionQualityScoreResponse | null;
  selection: TrendQuery;
  productions: ProductionCatalog[];
  customerQualityLabels?: Record<string, CustomerLabel>;
  onCalculate: () => void;
}) {
  const [selectedProductionId, setSelectedProductionId] = useState<
    string | null
  >(null);
  const selectedProductions = productions.filter((production) =>
    selection.production_ids.includes(production.id),
  );
  const selectedScore =
    result?.productions.find(
      (row) => row.production_id === selectedProductionId,
    ) ?? null;

  return (
    <section className="space-y-4">
      <PanelHeader
        disabled={isLoading || selection.production_ids.length === 0}
        isLoading={isLoading}
        onCalculate={onCalculate}
      />
      {error && (
        <section className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </section>
      )}
      {isStale && result && (
        <section className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
          La selección o los datos publicados han cambiado. Recalcula calidad de
          producción para actualizar el ranking.
        </section>
      )}
      {!result ? (
        <section className="flex min-h-64 items-center justify-center rounded-lg border border-gray-200 bg-white shadow-sm">
          <p className="text-sm text-gray-500">
            Selecciona producciones y pulsa Calcular calidad.
          </p>
        </section>
      ) : (
        <>
          <ScoreSummary result={result} />
          <GoldSourceNote result={result} />
          <PendingProductions result={result} />
          <ScoreTable
            result={result}
            productions={selectedProductions}
            customerQualityLabels={customerQualityLabels}
            onSelectProduction={setSelectedProductionId}
          />
          <ScoreDetails result={result} />
          {selectedScore && (
            <ShiftScoreModal
              row={selectedScore}
              onClose={() => setSelectedProductionId(null)}
            />
          )}
          <ScoreWarnings result={result} />
        </>
      )}
    </section>
  );
}

function PanelHeader({
  disabled,
  isLoading,
  onCalculate,
}: {
  disabled: boolean;
  isLoading: boolean;
  onCalculate: () => void;
}) {
  return (
    <header className="rounded-lg border border-gray-200 bg-white px-4 py-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900">
            Calidad de produccion
          </h2>
          <p className="mt-1 max-w-5xl text-sm text-gray-600">
            Score paralelo de calidad: penalizacion total para ranking y estado,
            score normalizado 0-10 como referencia secundaria y causa principal
            por contribucion ponderada.
          </p>
        </div>
        <button
          type="button"
          disabled={disabled}
          onClick={onCalculate}
          className="rounded-md bg-primary px-5 py-2 text-sm font-semibold text-white hover:bg-primary-light disabled:bg-gray-300"
        >
          {isLoading ? "Calculando..." : "Calcular calidad"}
        </button>
      </div>
    </header>
  );
}
