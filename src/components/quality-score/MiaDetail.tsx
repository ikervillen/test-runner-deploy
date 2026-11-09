import type { MiaScoreDetail } from "@/types/api";
import { formatNumber } from "@/lib/format";

const MAX_ROWS = 8;

export function MiaDetail({ rows }: { rows: MiaScoreDetail[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="mt-4">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        MIA individual
      </h4>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[680px] text-left text-[11px]">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-2 py-1.5">Defecto</th>
              <th className="px-2 py-1.5 text-right">Rechazos MIA</th>
              <th className="px-2 py-1.5 text-right">Inspeccionadas reales</th>
              <th className="px-2 py-1.5 text-right">% actual</th>
              <th className="px-2 py-1.5 text-right">Media hist.</th>
              <th className="px-2 py-1.5 text-right">z</th>
              <th className="px-2 py-1.5 text-right">Score</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, MAX_ROWS).map((row) => (
              <tr key={row.defect_code} className="border-t border-gray-100">
                <td className="max-w-[230px] px-2 py-1.5 font-medium text-gray-700 break-words">
                  {row.defect_code} - {row.defect_label}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {formatNumber(row.rejected_count)}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {formatNumber(row.inspected_count)}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {row.current_percent.toFixed(3)} %
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {row.historical_mean_percent?.toFixed(3) ?? "-"} %
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {row.z_score?.toFixed(2) ?? "-"}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums">
                  {row.score.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
