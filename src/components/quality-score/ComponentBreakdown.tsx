import { formatNumber } from "@/lib/format";

import type { ScoreLike } from "./meta";

export function ComponentBreakdown({ row }: { row: ScoreLike }) {
  return (
    <div className="mt-4">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        Contribuciones
      </h4>
      <table className="mt-2 w-full text-left text-[11px]">
        <thead className="bg-gray-50 text-gray-500">
          <tr>
            <th className="px-2 py-1.5">Componente</th>
            <th className="px-2 py-1.5 text-right">Lectura</th>
            <th className="px-2 py-1.5 text-right">Base</th>
            <th className="px-2 py-1.5 text-right">Contrib.</th>
          </tr>
        </thead>
        <tbody>
          {row.components.map((component) => (
            <tr key={component.component} className="border-t border-gray-100">
              <td className="px-2 py-1.5 font-medium text-gray-700">
                {component.label}
              </td>
              <td className="px-2 py-1.5 text-right text-gray-600">
                {component.component === "MIA"
                  ? "Suma individual"
                  : `Score ${component.score.toFixed(2)}`}
              </td>
              <td className="px-2 py-1.5 text-right text-gray-600">
                {component.component === "MIA"
                  ? `${formatNumber(component.weight)} defectos`
                  : `peso ${formatNumber(component.weight)}`}
              </td>
              <td className="px-2 py-1.5 text-right font-semibold tabular-nums">
                {formatNumber(component.contribution)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
