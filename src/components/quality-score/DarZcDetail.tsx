import type { DarScore, ZcScore } from "@/types/api";
import { formatNumber } from "@/lib/format";

import type { ScoreLike } from "./meta";

export function DarZcDetail({ row }: { row: ScoreLike }) {
  if (!row.dar && !row.zc) return null;
  return (
    <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">
      {row.dar && <DarCard dar={row.dar} />}
      {row.zc && <ZcCard zc={row.zc} />}
    </div>
  );
}

function DarCard({ dar }: { dar: DarScore }) {
  return (
    <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2 text-xs md:col-span-2">
      <p className="font-semibold text-gray-800">DAR</p>
      <p className="mt-1 text-gray-600">
        {dar.event_count} eventos - {dar.exposure_percent.toFixed(3)} %
        exposicion - score {dar.score.toFixed(2)}
      </p>
      <p className="mt-1 text-gray-500">
        Exposicion = minutos-cavidad DAR / minutos-cavidad totales de la
        produccion. Regla DAR progresiva: 0.10 % equivale a score 5 y 0.40 %
        equivale a score 10.
      </p>
      {dar.breakdown.length > 0 && <DarBreakdownTable dar={dar} />}
    </div>
  );
}

function DarBreakdownTable({ dar }: { dar: DarScore }) {
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full min-w-[680px] text-left text-[11px]">
        <thead className="bg-white text-gray-500">
          <tr>
            <th className="px-2 py-1">Defecto DAR</th>
            <th className="px-2 py-1">Codigos</th>
            <th className="px-2 py-1 text-right">Eventos</th>
            <th className="px-2 py-1 text-right">Min-cavidad</th>
            <th className="px-2 py-1 text-right">% del DAR</th>
            <th className="px-2 py-1 text-right">% produccion</th>
            <th className="px-2 py-1 text-right">Score</th>
            <th className="px-2 py-1">Umbral score</th>
          </tr>
        </thead>
        <tbody>
          {dar.breakdown.map((item) => (
            <tr key={item.group} className="border-t border-gray-100">
              <td className="max-w-[220px] px-2 py-1 font-medium text-gray-700 break-words">
                {item.group}
              </td>
              <td className="px-2 py-1 text-gray-600">
                {item.codes.length > 0 ? item.codes.join(", ") : "-"}
              </td>
              <td className="px-2 py-1 text-right tabular-nums">
                {item.event_count}
              </td>
              <td className="px-2 py-1 text-right tabular-nums">
                {formatNumber(item.affected_minutes)}
              </td>
              <td className="px-2 py-1 text-right tabular-nums">
                {item.share_percent.toFixed(1)} %
              </td>
              <td className="px-2 py-1 text-right tabular-nums">
                {item.exposure_percent.toFixed(3)} %
              </td>
              <td className="px-2 py-1 text-right tabular-nums">
                {item.score.toFixed(2)}
              </td>
              <td className="max-w-[170px] px-2 py-1 text-gray-600 break-words">
                {item.threshold_label}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ZcCard({ zc }: { zc: ZcScore }) {
  return (
    <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2 text-xs">
      <p className="font-semibold text-gray-800">Rendimiento ZC</p>
      <p className="mt-1 text-gray-600">
        {zc.actual_percent?.toFixed(2) ?? "-"} % real vs{" "}
        {zc.target_percent.toFixed(2)} % objetivo - score {zc.score.toFixed(2)}
      </p>
    </div>
  );
}
