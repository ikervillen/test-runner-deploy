import { useEffect, useState } from "react";
import { api, type LiveArchive, type LiveResult } from "@/services/api";

const number = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 4 });
const hourLabel = (stamp: string) => stamp.slice(11, 16);
const shiftLabel = (stamp: string) =>
  stamp.slice(8, 10) + "/" + stamp.slice(5, 7);
const names: Record<string, string> = {
  Manana: "Mañana",
  Tarde: "Tarde",
  Noche: "Noche",
};
const status: Record<string, string> = {
  pending: "Pendiente",
  partial: "Parcial",
  complete: "Completo",
  unavailable: "Falta el score acreditado",
};

export function ArchivePosition({
  archive,
  error,
  line,
  machine,
  components,
}: {
  archive: LiveArchive | null;
  error: boolean;
  line: string;
  machine: number;
  components: ReadonlyArray<readonly [string, string, string]>;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<LiveResult | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [detailError, setDetailError] = useState(false);
  const publication = archive?.publication;
  useEffect(() => {
    if (!publication || !result?.result_id) return;
    let active = true;
    void api
      .getArchiveDetail(publication, result.result_id)
      .then((data) => {
        if (active) setDetail(data);
      })
      .catch(() => {
        if (active) setDetailError(true);
      });
    return () => {
      active = false;
    };
  }, [publication, result]);
  const points = (archive?.shifts ?? []).map((shift) => {
    const hours = shift.hours.map((hour) => ({
      ...hour,
      results: hour.results.filter(
        (row) => row.line === line && row.machine === machine,
      ),
    }));
    // Handovers are weighted by the duration of each official result. The resulting
    // indicator is descriptive and must never acquire an official classification.
    let weighted = 0;
    let duration = 0;
    for (const h of hours) {
      for (const row of h.results) {
        if (row.total_penalty === null || !row.cutoff) continue;
        const elapsed =
          Date.parse(row.cutoff + "Z") - Date.parse(row.start + "Z");
        if (elapsed > 0) {
          weighted += row.total_penalty * elapsed;
          duration += elapsed;
        }
      }
    }
    return { ...shift, hours, value: duration ? weighted / duration : null };
  });
  const current = points.find((point) => point.start === selected);
  const ymax = Math.max(1, ...points.map((p) => p.value ?? 0));
  const x = (i: number) => 40 + (i * 440) / Math.max(1, points.length - 1);
  const y = (value: number) => 175 - (value * 150) / ymax;
  const pick = (start: string) => {
    setSelected(start);
    setResult(null);
    setDetail(null);
    setDetailError(false);
  };
  return (
    <div className="min-w-0">
      <h3 className="text-sm font-medium">Evolución por turnos</h3>
      {error && (
        <p className="text-sm text-[#53645d]">
          Histórico acumulado no disponible
        </p>
      )}
      {archive && points.every((p) => p.value === null) && (
        <p className="text-sm text-[#53645d]">
          Sin scores archivados para esta posición
        </p>
      )}
      {points.length > 0 && (
        <>
          <p className="text-xs text-[#53645d]">
            Media horaria ponderada por duración · indicador provisional
          </p>
          <svg
            className="h-[185px] w-full"
            viewBox="0 0 520 208"
            role="img"
            aria-label="Tendencia provisional por turno"
          >
            {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
              <g key={tick}>
                <line
                  x1="37"
                  x2="500"
                  y1={y(tick * ymax)}
                  y2={y(tick * ymax)}
                  stroke="#dce4e0"
                />
                <text
                  x="34"
                  y={y(tick * ymax) + 4}
                  textAnchor="end"
                  fill="#53645d"
                  fontSize="10"
                >
                  {number.format(tick * ymax)}
                </text>
              </g>
            ))}
            {points.map((point, i) => {
              const previous = points[i - 1]?.value;
              return (
                <g key={point.start}>
                  {point.value !== null && previous != null && (
                    <line
                      x1={x(i - 1)}
                      x2={x(i)}
                      y1={y(previous)}
                      y2={y(point.value)}
                      stroke="#157054"
                      strokeWidth="2"
                    />
                  )}
                  {point.value !== null && (
                    <circle
                      cx={x(i)}
                      cy={y(point.value)}
                      r={point.start === selected ? 6 : 4}
                      fill="#157054"
                      className="cursor-pointer"
                      onClick={() => pick(point.start)}
                    >
                      <title>
                        {shiftLabel(point.start)} · {names[point.name]} ·{" "}
                        {number.format(point.value)}
                      </title>
                    </circle>
                  )}
                  <text
                    x={x(i)}
                    y="195"
                    textAnchor="middle"
                    fill="#53645d"
                    fontSize="11"
                  >
                    {shiftLabel(point.start)} {point.name[0]}
                  </text>
                </g>
              );
            })}
          </svg>
          <div className="flex flex-wrap gap-2 text-xs">
            {points.map((point) => (
              <button
                type="button"
                key={point.start}
                className="rounded border border-[#dce4e0] px-2 py-1 hover:bg-[#eaf2ef]"
                aria-pressed={point.start === selected}
                onClick={() => pick(point.start)}
              >
                {shiftLabel(point.start)} · {names[point.name]}
                {point.partial_t0 && " · Parcial por T0"}
              </button>
            ))}
          </div>
        </>
      )}
      {current && (
        <div className="mt-3 overflow-x-auto">
          <h4 className="font-semibold">
            {shiftLabel(current.start)} · {names[current.name]}
            {current.partial_t0 ? " · Parcial por T0" : ""}
            {current.accruing && " · Aún incorporando horas"}
          </h4>
          <p className="text-xs text-[#53645d]">
            {current.hours.filter((h) => h.state !== "before_t0").length}/8
            horas elegibles ·{" "}
            {
              current.hours.filter(
                (h) =>
                  h.results.length &&
                  h.results.every((r) => r.state === "complete"),
              ).length
            }{" "}
            completas ·{" "}
            {
              current.hours.filter((h) =>
                h.results.some((r) => r.state === "partial"),
              ).length
            }{" "}
            con parciales ·{" "}
            {
              current.hours.filter((h) =>
                h.results.some((r) => r.state === "pending"),
              ).length
            }{" "}
            con pendientes ·{" "}
            {
              current.hours.filter((h) =>
                h.results.some((r) => r.state === "unavailable"),
              ).length
            }{" "}
            con cierres sin score
          </p>
          <table className="mt-2 w-full min-w-[430px] text-xs tabular-nums">
            <caption className="sr-only">
              Scores horarios oficiales del turno
            </caption>
            <thead>
              <tr>
                <th scope="col">Hora y producción</th>
                {components.map(([code, label]) => (
                  <th scope="col" key={code}>
                    {label}
                  </th>
                ))}
                <th scope="col">Total</th>
              </tr>
            </thead>
            <tbody>
              {current.hours.flatMap((h) =>
                h.results.length
                  ? h.results.map((row) => (
                      <tr
                        key={h.start + row.production}
                        className="border-t text-right"
                      >
                        <th scope="row" className="text-left font-normal">
                          {hourLabel(h.end)} · {row.model ?? row.production} ·{" "}
                          {status[row.state]}
                        </th>
                        {components.map(([code]) => (
                          <td key={code}>
                            {row.contributions[code] === undefined
                              ? "—"
                              : number.format(row.contributions[code]!)}
                          </td>
                        ))}
                        <td>
                          {row.total_penalty === null ? (
                            "—"
                          ) : row.result_id ? (
                            <button
                              type="button"
                              className="underline"
                              onClick={() => {
                                setDetail(null);
                                setDetailError(false);
                                setResult(row);
                              }}
                            >
                              {number.format(row.total_penalty)}
                            </button>
                          ) : (
                            number.format(row.total_penalty)
                          )}
                        </td>
                      </tr>
                    ))
                  : [
                      <tr key={h.start} className="border-t">
                        <th scope="row" className="text-left font-normal">
                          {hourLabel(h.end)}
                        </th>
                        <td colSpan={9}>
                          {h.state === "before_t0"
                            ? "Anterior a T0"
                            : "Sin producción"}
                        </td>
                      </tr>,
                    ],
              )}
            </tbody>
          </table>
          {result && (
            <div className="mt-2 text-xs">
              <p>
                {result.production} · {result.model} · {line} / máquina{" "}
                {machine}
              </p>
              {detailError && (
                <p role="alert">Detalle de archivo no disponible.</p>
              )}
              {!detail && !detailError && (
                <p role="status">Cargando detalle…</p>
              )}
              {detail &&
                Object.entries(detail).map(([key, value]) => (
                  <details key={key} className="border-t py-1">
                    <summary>
                      {{
                        score: "Score y fórmula",
                        coverage: "Cobertura",
                        configuration: "Configuración efectiva",
                        mia_references: "Referencias MIA",
                        details: "Detalle de componentes",
                        missing: "Trazabilidad no disponible",
                      }[key] ?? key}
                    </summary>
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap">
                      {value === null
                        ? "No disponible en la publicación"
                        : JSON.stringify(value, null, 2)}
                    </pre>
                  </details>
                ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
