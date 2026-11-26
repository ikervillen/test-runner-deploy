import { useEffect, useState } from "react";
import { api } from "@/services/api";
import type { LiveArchive, LiveResult, LiveScores } from "@/services/api";
import { ArchivePosition, archivePoints } from "./ArchivePosition";
import {
  chartAxis,
  chartBottom,
  chartCeiling,
  chartGrid,
  chartHeight,
  chartInk,
  chartTicks,
  chartTop,
  chartTrace,
  useChartWidth,
} from "./liveChart";

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

// Mesa de control: columns and order of the approved hourly table.
const components = [
  ["MIA", "MIA", "mia_score_details"],
  ["Critico", "Crít.", "aql_score_details"],
  ["Grave", "Grav.", "aql_score_details"],
  ["Mayor", "May.", "aql_score_details"],
  ["Menor", "Men.", "aql_score_details"],
  ["Leve", "Lev.", "aql_score_details"],
  ["DAR", "DAR", "dar_score_details"],
  ["ZC", "ZC", "zc_score_details"],
] as const;

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
  const { ref, width } = useChartWidth(280);
  const left = 1;
  const right = width - 10;
  const top = chartTop;
  const bottom = chartBottom;
  const step = (right - left) / Math.max(hours.length, 1);
  const points = hours.map((hour, index) =>
    hour.state === "closed"
      ? rows[index].filter((row) => row.total_penalty !== null)
      : [],
  );
  const latest = points
    .flat()
    .sort((a, b) =>
      (b.cutoff ?? b.start).localeCompare(a.cutoff ?? a.start),
    )[0];
  const showTick = (i: number) =>
    step >= 42 ||
    i === 0 ||
    i === hours.length - 1 ||
    (step >= 23 && i % 2 === 0 && i < hours.length - 2);
  const x = (index: number) => left + (index + 0.5) * step;
  const y = (value: number) => bottom - (value / ceiling) * (bottom - top);
  return (
    <svg
      ref={ref}
      className="block h-[260px] w-full"
      viewBox={`0 0 ${width} ${chartHeight}`}
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
      {chartTicks(ceiling).map((value) => (
        <line
          key={value}
          x1={left}
          x2={right}
          y1={y(value)}
          y2={y(value)}
          stroke={chartGrid}
        />
      ))}
      <path
        d={`M${left},${top}V${bottom}H${right}V${top}`}
        fill="none"
        stroke={chartAxis}
      />
      <text
        x={right}
        y={top - 10}
        textAnchor="end"
        fill={chartTrace}
        fontSize="12"
        fontWeight="600"
      >
        Actual
      </text>
      {hours.map((hour, i) => (
        <g key={hour.start}>
          {showTick(i) && (
            <text
              x={x(i)}
              y={bottom + 21}
              textAnchor="middle"
              fill={chartInk}
              fontSize="12"
            >
              {stamp(hour.end)}
            </text>
          )}
          {points[i].length === 1 &&
            i > 0 &&
            points[i - 1].length === 1 &&
            points[i][0].production === points[i - 1][0].production && (
              <line
                x1={x(i - 1)}
                y1={y(points[i - 1][0].total_penalty!)}
                x2={x(i)}
                y2={y(points[i][0].total_penalty!)}
                stroke={chartTrace}
                strokeWidth="2.3"
              />
            )}
        </g>
      ))}
      {points.map((entries, i) =>
        entries.map((row, j) => {
          const cx = x(i) + (j - (entries.length - 1) / 2) * 8;
          const cy = y(row.total_penalty!);
          const highlighted = selected
            ? selected === row.result_id
            : row === latest;
          return (
            <g
              key={row.result_id ?? row.production}
              className="cursor-pointer"
              onClick={() => onSelect(row)}
            >
              {highlighted && (
                <circle
                  cx={cx}
                  cy={cy}
                  r={8.2}
                  fill="none"
                  stroke={chartTrace}
                  strokeWidth="1.4"
                />
              )}
              <circle
                data-result={row.result_id ?? undefined}
                cx={cx}
                cy={cy}
                r={highlighted ? 5.2 : 3.7}
                fill={classification(row)[1]}
                stroke="white"
                strokeWidth="1.5"
              />
              <title>
                {stamp(hours[i].end)} · {row.production} ·{" "}
                {number.format(row.total_penalty!)}
              </title>
            </g>
          );
        }),
      )}
    </svg>
  );
}

export function LiveView({ historyOnly = false }: { historyOnly?: boolean }) {
  const [archive, setArchive] = useState<LiveArchive | null>(null);
  const [archiveError, setArchiveError] = useState(false);
  const [before, setBefore] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refreshArchive() {
      try {
        const data = await api.getLiveArchive(before, controller.signal);
        if (!controller.signal.aborted) {
          setArchive(data);
          setArchiveError(false);
        }
      } catch {
        if (!controller.signal.aborted) setArchiveError(true);
      } finally {
        if (!controller.signal.aborted && !before)
          timer = setTimeout(refreshArchive, 300000);
      }
    }
    void refreshArchive();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [before]);
  const [view, setView] = useState<LiveScores | null>(null);
  const [error, setError] = useState(false);
  const [chosen, setChosen] = useState<{
    row: LiveResult;
    publication: string;
    component?: string;
  } | null>(null);
  const [detail, setDetail] = useState<Record<
    string,
    Record<string, unknown>[]
  > | null>(null);
  const [detailError, setDetailError] = useState(false);
  useEffect(() => {
    if (historyOnly) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const data = await api.getLiveScores(controller.signal);
        if (!controller.signal.aborted) {
          setView(data);
          setChosen((current) => {
            if (!current || current.publication === data.publication_id)
              return current;
            const row = data.hours
              .flatMap((hour) => hour.results)
              .find((row) => row.result_id === current.row.result_id);
            return row
              ? { ...current, row, publication: data.publication_id }
              : null;
          });
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
  }, [historyOnly]);
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
  const ceiling = chartCeiling(
    Math.max(
      maximum,
      ...positions.flatMap(([line, machine]) =>
        archivePoints(archive, line, machine).map((point) => point.value ?? 0),
      ),
    ),
  );
  return (
    <main className="bg-white px-5 py-6 text-[#182c25] sm:px-8">
      <div className="flex flex-wrap items-end justify-between gap-2 border-b border-[#dce4e0] pb-5">
        <div>
          <p className="text-xs uppercase tracking-wider text-[#53645d]">
            Visión de planta
          </p>
          <h1 className="text-3xl font-semibold tracking-tight">
            {historyOnly
              ? "Histórico acumulado de Gold Live"
              : "Estado de las líneas"}
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
      {!historyOnly && !view && !error && (
        <p role="status">Cargando Gold Live…</p>
      )}
      {historyOnly && (
        <nav aria-label="Turnos archivados" className="my-4 flex gap-4">
          <button
            type="button"
            disabled={!archive?.next_before}
            onClick={() => setBefore(archive?.next_before ?? null)}
          >
            Turnos anteriores
          </button>
          {before && (
            <button type="button" onClick={() => setBefore(null)}>
              Volver a los últimos turnos
            </button>
          )}
        </nav>
      )}
      {historyOnly &&
        positions.map(([line, machine, label]) => (
          <section key={label} className="border-b border-[#dce4e0] py-5">
            <h2 className="text-3xl font-semibold">{label}</h2>
            <ArchivePosition
              archive={archive}
              error={archiveError}
              line={line}
              machine={machine}
              components={components}
            />
          </section>
        ))}
      {view &&
        !historyOnly &&
        positions.map(([line, machine, label]) => {
          const active = view.active_productions?.find(
            (item) => item.line === line && item.machine === machine,
          );
          const modelLabel = view.stale
            ? "Modelo sin actualizar"
            : active?.state === "ambiguous"
              ? "Producción sin confirmar"
              : active?.model
                ? `Modelo ${active.model}`
                : active || !view.active_productions
                  ? "Modelo no disponible"
                  : "Sin producción activa acreditada";
          const rows = view.hours.map((hour) =>
            hour.state === "open"
              ? []
              : hour.results.filter(
                  (row) => row.line === line && row.machine === machine,
                ),
          );
          const headline = rows
            .slice()
            .reverse()
            .flatMap((entries) =>
              entries
                .filter((row) => row.total_penalty !== null)
                .sort((a, b) =>
                  (b.cutoff ?? b.start).localeCompare(a.cutoff ?? a.start),
                ),
            )[0];
          const select = (row: LiveResult, component?: string) => {
            if (row.result_id)
              setChosen({ row, publication: view.publication_id, component });
          };
          return (
            <section
              key={label}
              aria-label={label}
              className="border-b border-[#dce4e0] py-5"
            >
              <div className="mb-3 flex items-baseline justify-between gap-3">
                <h2 className="flex min-w-0 items-baseline gap-3">
                  <span className="shrink-0 text-4xl font-semibold tracking-tight md:text-[44px]">
                    {label}
                  </span>
                  <span
                    className="truncate text-sm font-medium text-[#496357] md:text-base"
                    title={modelLabel}
                  >
                    {modelLabel}
                  </span>
                  <span className="sr-only">Máquina {machine}</span>
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
                  {headline?.model &&
                    active?.production &&
                    headline.production !== active.production && (
                      <span
                        className="ml-2 inline-block max-w-32 truncate align-middle text-xs font-medium"
                        title={`Score del modelo ${headline.model}`}
                      >
                        Modelo {headline.model}
                      </span>
                    )}
                  {headline && (
                    <span className="sr-only">
                      {" "}
                      · {classification(headline)[0]} ·{" "}
                      {stamp(headline.cutoff ?? headline.start)}
                    </span>
                  )}
                </p>
              </div>
              <div className="grid items-start gap-[18px] lg:grid-cols-[minmax(0,2.05fr)_minmax(408px,1fr)]">
                <div className="grid min-w-0 items-start gap-3 sm:grid-cols-[minmax(0,69fr)_minmax(0,31fr)] sm:gap-6">
                  <ArchivePosition
                    archive={archive}
                    error={archiveError}
                    line={line}
                    machine={machine}
                    components={components}
                    ceiling={ceiling}
                    compact
                  />
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
                <div className="overflow-x-auto pt-3">
                  <table className="w-full min-w-[408px] text-left text-sm tabular-nums">
                    <caption className="sr-only">
                      Desglose horario de {label}
                    </caption>
                    <thead>
                      <tr className="text-[#53645d]">
                        <th scope="col" className="px-1 text-xs font-medium">
                          Hora
                        </th>
                        {components.map(([code, title]) => (
                          <th
                            key={code}
                            scope="col"
                            className="px-1 text-right text-xs font-medium"
                          >
                            {title}
                          </th>
                        ))}
                        <th
                          scope="col"
                          className="border-l border-[#dce4e0] px-1 text-right text-xs font-medium"
                        >
                          Total
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {view.hours.map((hour, i) => {
                        const entries = rows[i];
                        if (!entries.length)
                          return (
                            <tr
                              key={hour.start}
                              className={`h-[26px] border-t border-[#dce4e0] ${hour.state === "open" ? "bg-[#eaf2f6]" : ""}`}
                            >
                              <th
                                scope="row"
                                className="px-1 font-normal"
                                title={hour.start}
                              >
                                {stamp(hour.end)}
                                <span className="block text-[11px] leading-tight text-[#53645d]">
                                  {labels[hour.state] ?? "Sin producción"}
                                </span>
                              </th>
                              {components.map(([code]) => (
                                <td
                                  key={code}
                                  className="px-1 text-right text-[#53645d]"
                                >
                                  —
                                </td>
                              ))}
                              <td className="border-l border-[#dce4e0] px-1 text-right text-[#53645d]">
                                —
                              </td>
                            </tr>
                          );
                        return entries
                          .slice()
                          .sort((a, b) => a.start.localeCompare(b.start))
                          .map((row) => {
                            const selected =
                              chosen?.publication === view.publication_id &&
                              chosen.row.result_id !== null &&
                              chosen.row.result_id === row.result_id;
                            const [status, color] = classification(row);
                            const state = labels[row.state];
                            return (
                              <tr
                                key={row.production + row.start}
                                className={`h-[26px] border-t border-[#dce4e0] ${selected ? "bg-[#eaf2ef]" : ""}`}
                              >
                                <th
                                  scope="row"
                                  className="px-1 font-normal"
                                  title={row.reason ?? row.production}
                                >
                                  {stamp(hour.end)}
                                  {entries.length > 1 && (
                                    <span className="block text-[11px] leading-tight text-[#53645d]">
                                      {row.production.slice(
                                        row.production.lastIndexOf(":") + 1,
                                      )}{" "}
                                      · {stamp(row.start)}–
                                      {stamp(row.cutoff ?? hour.end)}
                                    </span>
                                  )}
                                  {state && (
                                    <span className="block text-[11px] leading-tight text-[#53645d]">
                                      {state}
                                    </span>
                                  )}
                                </th>
                                {components.map(([code, title, detail]) => {
                                  const value = row.contributions?.[code];
                                  return (
                                    <td key={code} className="px-1 text-right">
                                      {value === undefined || !row.result_id ? (
                                        "—"
                                      ) : (
                                        <button
                                          type="button"
                                          className="hover:underline focus-visible:underline"
                                          aria-label={`Detalle ${title} · ${label} · ${stamp(hour.end)} · ${number.format(value)}`}
                                          onClick={() => select(row, detail)}
                                        >
                                          {number.format(value)}
                                        </button>
                                      )}
                                    </td>
                                  );
                                })}
                                <td className="border-l border-[#dce4e0] px-1 text-right">
                                  {row.result_id &&
                                  row.total_penalty !== null ? (
                                    <button
                                      type="button"
                                      className="font-semibold hover:underline focus-visible:underline"
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
                          });
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
              {chosen?.row.line === line && chosen.row.machine === machine && (
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
                    <p role="alert">
                      Detalle no disponible para esta publicación.
                    </p>
                  )}
                  {!detail && !detailError && (
                    <p role="status">Cargando detalle…</p>
                  )}
                  {detail &&
                    Object.entries(detail).map(([name, rows]) => (
                      <details
                        className="mt-2 border-t pt-2"
                        key={`${name}:${chosen.component}`}
                        open={chosen.component === name || undefined}
                      >
                        <summary>{details[name] ?? name}</summary>
                        {rows.length ? (
                          <ul className="space-y-1 text-sm">
                            {rows.map((row) => (
                              <li key={JSON.stringify(row)}>
                                {Object.entries(row)
                                  .filter(
                                    ([field, value]) =>
                                      field !== "id_resultado" &&
                                      value !== null,
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
            </section>
          );
        })}
    </main>
  );
}
