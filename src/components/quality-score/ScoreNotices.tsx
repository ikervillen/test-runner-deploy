import type { ProductionQualityScoreResponse } from "@/types/api";

import { pendingReason } from "./model";

/** Gold publication footer: cutoff and provisional DAR notice. */
export function GoldSourceNote({
  result,
}: {
  result: ProductionQualityScoreResponse;
}) {
  if (result.meta.source !== "gold") return null;
  const provisionalDar = result.productions.some((row) =>
    row.warnings.some((warning) => warning.includes("DAR provisional")),
  );
  return (
    <p className="text-xs text-gray-600">
      Datos publicados
      {result.meta.cutoff
        ? ` hasta ${result.meta.cutoff.replace("T", " ")}`
        : ""}
      .
      {provisionalDar
        ? " Configuración DAR provisional, pendiente de aprobación por segmento."
        : ""}
    </p>
  );
}

export function PendingProductions({
  result,
}: {
  result: ProductionQualityScoreResponse;
}) {
  const pending = result.pending_productions ?? [];
  if (pending.length === 0) return null;
  return (
    <section
      role="status"
      className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
    >
      <p className="font-semibold">
        Producciones pendientes de datos suficientes
      </p>
      {pending.map((row) => (
        <p key={row.production_id}>
          {row.production_id}: {row.reasons.map(pendingReason).join("; ")}
        </p>
      ))}
    </section>
  );
}

export function ScoreWarnings({
  result,
}: {
  result: ProductionQualityScoreResponse;
}) {
  const productionWarnings = result.productions.flatMap((row) =>
    row.warnings.map((warning) => ({ id: row.production_id, warning })),
  );
  if (result.warnings.length === 0 && productionWarnings.length === 0) {
    return null;
  }
  return (
    <section className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
      {result.warnings.map((warning) => (
        <p key={warning}>{warning}</p>
      ))}
      {productionWarnings.map(({ id, warning }) => (
        <p key={`${id}-${warning}`}>
          {id}: {warning}
        </p>
      ))}
    </section>
  );
}
