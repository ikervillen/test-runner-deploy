import { useEffect, useState } from "react";
import { api } from "@/services/api";
import type { LiveResult, LiveScores } from "@/services/api";

const labels: Record<string, string> = {
  partial: "Parcial",
  pending: "Pendiente",
  before_t0: "Anterior a T0",
  open: "En curso",
};

const details: Record<string, string> = {
  quality_score_components: "Componentes",
  mia_score_details: "MIA",
  aql_score_details: "AQL",
  dar_score_details: "DAR",
  zc_score_details: "Rendimiento ZC",
};

const number = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 4 });
const stamp = (value: string) => value.slice(11, 16);
const positions = [
  ["AV21", 4, "AV21"],
  ["AV22", 5, "AV22"],
  ["AV23", 6, "AV23"],
  ["AV31", 7, "AV31A"],
  ["AV31", 8, "AV31B"],
  ["AV32", 9, "AV32A"],
  ["AV32", 10, "AV32B"],
] as const;

const classification = (row: LiveResult) =>
  ({
    good: ["Bueno", "#157054"],
    observed: ["Observado", "#9c740c"],
    bad: ["Malo", "#bc4438"],
    critical: ["Crítico", "#a82824"],
  })[row.classification?.toLowerCase() ?? ""] ?? [
    "Sin clasificación",
    "#55645f",
  ];

function HourlyTrend({
  hours,
  rows,
  ceiling,
  selected,
  onSelect,
}: {
  hours: LiveScores["hours"];
  rows: LiveResult[][];
  ceiling: number;
  selected?: string | null;
  onSelect: (row: LiveResult) => void;
}) {
  const width = 360;
  const left = 38;
  const top = 12;
  const bottom = 218;
  const step = (width - left - 8) / Math.max(hours.length, 1);
  const points = hours.map((hour, index) =>
    hour.state === "closed"
      ? rows[index].filter((row) => row.total_penalty !== null)
      : [],
  );
  const x = (index: number) => left + (index + 0.5) * step;
  const y = (value: number) => bottom - (value / ceiling) * (bottom - top);
  return (
    <svg
      className="h-[248px] w-full"
      viewBox="0 0 360 248"
      role="img"
      aria-label="Evolución horaria de la penalización total"
    >
      {hours.map((hour, i) => (
        <rect
          key={hour.start}
          x={left + i * step}
          y={top}
          width={step}
          height={bottom - top}
          fill={hour.state === "open" ? "#eaf2f6" : i % 2 ? "#f4f6f5" : "#fff"}
          stroke="#dce4e0"
          strokeWidth=".5"
        />
      ))}
      {[0, 1, 2, 3, 4].map((tick) => {
        const value = (tick * ceiling) / 4;
        return (
          <g key={tick}>
            <line
              x1={left}
              x2={width - 8}
              y1={y(value)}
              y2={y(value)}
              stroke="#dce4e0"
            />
            <text
              x={left - 6}
              y={y(value) + 4}
              textAnchor="end"
              fill="#53645d"
              fontSize="11"
            >
              {number.format(value)}
            </text>
          </g>
        );
      })}
      {hours.map((hour, i) => (
        <g key={hour.start}>
          <text
            x={x(i)}
            y={237}
            textAnchor="middle"
            fill="#53645d"
            fontSize="11"
          >
            {stamp(hour.end)}
          </text>
          {points[i].length === 1 &&
            i > 0 &&
            points[i - 1].length === 1 &&
            points[i][0].production === points[i - 1][0].production && (
              <line
                x1={x(i - 1)}
                y1={y(points[i - 1][0].total_penalty!)}
                x2={x(i)}
                y2={y(points[i][0].total_penalty!)}
                stroke="#157054"
                strokeWidth="2"
              />
            )}
        </g>
      ))}
      {points.map((entries, i) =>
        entries.map((row, j) => (
          <circle
            key={row.result_id ?? row.production}
            data-result={row.result_id ?? undefined}
            cx={x(i) + (j - (entries.length - 1) / 2) * 8}
            cy={y(row.total_penalty!)}
            r={selected === row.result_id ? 6 : 4}
            fill={classification(row)[1]}
            stroke={selected === row.result_id ? "#172a24" : "#fff"}
            strokeWidth="1.5"
            className="cursor-pointer"
            onClick={() => onSelect(row)}
          >
            <title>
              {stamp(hours[i].end)} · {row.production} ·{" "}
              {number.format(row.total_penalty!)}
            </title>
          </circle>
        )),
      )}
    </svg>
  );
}

export function LiveView() {
  const [view, setView] = useState<LiveScores | null>(null);
  const [error, setError] = useState(false);
  const [chosen, setChosen] = useState<{
    row: LiveResult;
    publication: string;
  } | null>(null);
  const [detail, setDetail] = useState<Record<
    string,
    Record<string, unknown>[]
  > | null>(null);
  const [detailError, setDetailError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const data = await api.getLiveScores(controller.signal);
        if (!controller.signal.aborted) {
          setView(data);
          setError(false);
        }
      } catch {
        if (!controller.signal.aborted) setError(true);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 30000);
      }
    }
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    let active = true;
    setDetail(null);
    setDetailError(false);
    if (chosen?.row.result_id) {
      void api
        .getLiveDetail(chosen.publication, chosen.row.result_id)
        .then((data) => {
          if (active) setDetail(data);
        })
        .catch(() => {
          if (active) setDetailError(true);
        });
    }
    return () => {
      active = false;
    };
  }, [chosen]);
  const maximum = Math.max(
    0,
    ...(view?.hours.flatMap((hour) =>
      hour.state === "closed"
        ? hour.results.map((row) => row.total_penalty ?? 0)
        : [],
    ) ?? []),
  );
  const ceiling = Math.max(4, Math.ceil(maximum / 4) * 4);
  return (
    <main className="bg-white px-5 py-6 text-[#182c25] sm:px-8">
      <div className="flex flex-wrap items-end justify-between gap-2 border-b border-[#dce4e0] pb-5">
        <div>
          <p className="text-xs uppercase tracking-wider text-[#53645d]">
            Visión de planta
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">
            Estado de las líneas
          </h1>
        </div>
        {view && (
          <p className="text-sm text-[#53645d]">
            Publicación Gold {view.published_at}
            {view.mia_provisional && " · Criterio MIA provisional"}
          </p>
        )}
      </div>
      {error && (
        <p role="alert">
          {view
            ? "Conexión interrumpida. Se conserva la última consulta."
            : "Gold Live no disponible."}
        </p>
      )}
      {view?.stale && (
        <p role="status">La publicación Gold está desactualizada.</p>
      )}
      {!view && !error && <p role="status">Cargando Gold Live…</p>}
      {view &&
        positions.map(([line, machine, label]) => {
          const rows = view.hours.map((hour) =>
            hour.state === "open"
              ? []
              : hour.results.filter(
                  (row) => row.line === line && row.machine === machine,
                ),
          );
          const last =
            rows
              .slice()
              .reverse()
              .find((entries) => entries.length > 0) ?? [];
          const headline =
            last.length === 1 && last[0].total_penalty !== null
              ? last[0]
              : null;
          const select = (row: LiveResult) => {
            if (row.result_id)
              setChosen({ row, publication: view.publication_id });
          };
          return (
            <section
              key={label}
              aria-label={label}
              className="border-b border-[#dce4e0] py-5"
            >
              <div className="mb-3 flex items-baseline justify-between gap-3">
                <h2 className="text-4xl font-semibold tracking-tight md:text-[44px]">
                  {label}
                  <span className="sr-only"> · Máquina {machine}</span>
                </h2>
                <p
                  className="text-4xl font-semibold tabular-nums md:text-[44px]"
                  style={{
                    color: headline ? classification(headline)[1] : undefined,
                  }}
                >
                  <span className="sr-only">
                    Penalización total publicada:{" "}
                  </span>
                  {headline ? number.format(headline.total_penalty!) : "—"}
                </p>
              </div>
              <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1.1fr)]">
                <div>
                  <h3 className="text-sm font-medium">Evolución por turnos</h3>
                  <div className="mt-2 grid h-[206px] place-items-center border border-[#dce4e0] bg-[#f7f9f8] px-3 text-center text-sm text-[#53645d]">
                    Histórico no disponible
                  </div>
                </div>
                <div>
                  <h3 className="text-sm font-medium">Últimas horas</h3>
                  <HourlyTrend
                    hours={view.hours}
                    rows={rows}
                    ceiling={ceiling}
                    selected={
                      chosen?.publication === view.publication_id
                        ? chosen.row.result_id
                        : null
                    }
                    onSelect={select}
                  />
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm tabular-nums">
                    <caption className="sr-only">
                      Desglose horario de {label}
                    </caption>
                    <thead>
                      <tr className="text-[#53645d]">
                        <th scope="col">Hora</th>
                        <th scope="col">Clasificación</th>
                        <th scope="col" className="text-right">
                          Total
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {view.hours.map((hour, i) =>
                        rows[i].length ? (
                          rows[i].map((row) => {
                            const selected =
                              chosen?.publication === view.publication_id &&
                              chosen.row.result_id !== null &&
                              chosen.row.result_id === row.result_id;
                            const [status, color] = classification(row);
                            return (
                              <tr
                                key={hour.start + row.production}
                                className={
                                  selected
                                    ? "border-t border-[#dce4e0] bg-[#eaf2ef]"
                                    : "border-t border-[#dce4e0]"
                                }
                              >
                                <th
                                  scope="row"
                                  className="py-1 pr-2 font-normal"
                                  title={hour.start}
                                >
                                  {stamp(hour.end)}
                                </th>
                                <td
                                  className="py-1 pr-2"
                                  title={row.reason ?? row.production}
                                >
                                  {row.state === "partial"
                                    ? "Parcial"
                                    : row.total_penalty === null
                                      ? (labels[row.state] ?? row.state)
                                      : status}
                                  {rows[i].length > 1 && (
                                    <span className="block text-xs text-[#53645d]">
                                      {row.production.slice(
                                        row.production.lastIndexOf(":") + 1,
                                      )}
                                    </span>
                                  )}
                                </td>
                                <td className="py-1 text-right">
                                  {row.result_id &&
                                  row.total_penalty !== null ? (
                                    <button
                                      type="button"
                                      className="font-semibold underline-offset-4 hover:underline focus-visible:underline"
                                      aria-pressed={selected}
                                      aria-label={`Ver desglose de ${label} · ${stamp(hour.end)} · ${number.format(row.total_penalty)} · ${status}`}
                                      style={{ color }}
                                      onClick={() => select(row)}
                                    >
                                      {number.format(row.total_penalty)}
                                    </button>
                                  ) : (
                                    "—"
                                  )}
                                </td>
                              </tr>
                            );
                          })
                        ) : (
                          <tr
                            key={hour.start}
                            className={
                              hour.state === "open"
                                ? "border-t bg-[#eaf2f6]"
                                : "border-t border-[#dce4e0]"
                            }
                          >
                            <th
                              scope="row"
                              className="py-1 pr-2 font-normal"
                              title={hour.start}
                            >
                              {stamp(hour.end)}
                            </th>
                            <td className="py-1 pr-2 text-[#53645d]">
                              {labels[hour.state] ?? "Sin resultado"}
                            </td>
                            <td className="py-1 text-right">—</td>
                          </tr>
                        ),
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
              {chosen?.row.line === line &&
                chosen.row.machine === machine &&
                detail?.quality_score_components && (
                  <div
                    className="mt-2 overflow-x-auto"
                    aria-label="Contribuciones de la hora seleccionada"
                  >
                    <table className="w-full text-sm tabular-nums">
                      <caption className="text-left font-medium">
                        Desglose de las{" "}
                        {chosen.row.cutoff && stamp(chosen.row.cutoff)}
                      </caption>
                      <thead>
                        <tr>
                          {[
                            "MIA",
                            "Crítico",
                            "Grave",
                            "Mayor",
                            "Menor",
                            "Leve",
                            "DAR",
                            "ZC",
                            "Total",
                          ].map((name) => (
                            <th
                              key={name}
                              scope="col"
                              className="px-2 text-right"
                            >
                              {name}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          {[
                            "mia",
                            "critico",
                            "grave",
                            "mayor",
                            "menor",
                            "leve",
                            "dar",
                            "zc",
                          ].map((name) => {
                            const entry = detail.quality_score_components.find(
                              (item) =>
                                String(item.componente)
                                  .normalize("NFD")
                                  .replace(/[\u0300-\u036f]/g, "")
                                  .toLowerCase() === name,
                            );
                            return (
                              <td key={name} className="px-2 text-right">
                                {typeof entry?.contribucion === "number"
                                  ? number.format(entry.contribucion)
                                  : "—"}
                              </td>
                            );
                          })}
                          <td className="px-2 text-right font-semibold">
                            {chosen.row.total_penalty === null
                              ? "—"
                              : number.format(chosen.row.total_penalty)}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                )}
            </section>
          );
        })}
      {chosen && (
        <section className="rounded-lg border bg-white p-4">
          <div className="flex justify-between">
            <h2 className="font-semibold">
              {chosen.row.production} · {stamp(chosen.row.start)}–
              {chosen.row.cutoff && stamp(chosen.row.cutoff)}
            </h2>
            <button type="button" onClick={() => setChosen(null)}>
              Cerrar detalle
            </button>
          </div>
          <p>
            Score normalizado{" "}
            {chosen.row.normalized_score === null
              ? "—"
              : number.format(chosen.row.normalized_score)}
          </p>
          {detailError && (
            <p role="alert">Detalle no disponible para esta publicación.</p>
          )}
          {!detail && !detailError && <p role="status">Cargando detalle…</p>}
          {detail &&
            Object.entries(detail).map(([name, rows]) => (
              <details className="mt-2 border-t pt-2" key={name}>
                <summary>{details[name] ?? name}</summary>
                {rows.length ? (
                  <ul className="space-y-1 text-sm">
                    {rows.map((row) => (
                      <li key={JSON.stringify(row)}>
                        {Object.entries(row)
                          .filter(
                            ([field, value]) =>
                              field !== "id_resultado" && value !== null,
                          )
                          .map(
                            ([field, value]) =>
                              `${field.replace(/_/g, " ")} ${typeof value === "number" ? number.format(value) : String(value)}`,
                          )
                          .join(" · ")}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>Sin detalles publicados.</p>
                )}
              </details>
            ))}
        </section>
      )}
    </main>
  );
}
