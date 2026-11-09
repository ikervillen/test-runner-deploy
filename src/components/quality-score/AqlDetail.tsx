import type { AqlCategoryScore } from "@/types/api";

import { formatAqlThresholds } from "./model";

export function AqlDetail({ rows }: { rows: AqlCategoryScore[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="mt-4">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        AQL por categoria
      </h4>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-[11px]">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-2 py-1.5">Categoria</th>
              <th className="px-2 py-1.5 text-right">Exposicion</th>
              <th className="px-2 py-1.5 text-right">AQL</th>
              <th className="px-2 py-1.5">Umbrales score</th>
              <th className="px-2 py-1.5 text-right">Score</th>
              <th className="px-2 py-1.5">Defecto principal</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.category} className="border-t border-gray-100">
                <td className="px-2 py-1.5 font-medium text-gray-700">
                  {row.category}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {row.exposure_percent.toFixed(2)} %
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {row.aql_percent != null
                    ? `${row.aql_percent.toFixed(2)} %`
                    : "-"}
                </td>
                <td className="px-2 py-1.5 text-gray-600">
                  {formatAqlThresholds(row)}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {row.score.toFixed(2)}
                </td>
                <td className="max-w-[220px] px-2 py-1.5 text-gray-600 break-words">
                  {row.top_defect_code != null
                    ? `${row.top_defect_code} - ${row.top_defect_label ?? ""}`
                    : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
