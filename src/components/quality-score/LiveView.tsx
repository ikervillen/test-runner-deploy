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
const position = ({ line, machine }: LiveResult) =>
  machine === null ? line : `${line} / máquina ${machine}`;

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
  const positions = [
    ...new Set(view?.hours.flatMap((hour) => hour.results.map(position))),
  ].sort();
  return (
    <main className="space-y-4 px-6 py-4">
      <h1 className="text-xl font-semibold text-gray-900">Calidad en vivo</h1>
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
      {view && (
        <>
          <p className="text-sm text-gray-600">
            Publicación Gold {view.published_at} · Criterio estadístico MIA
            provisional
          </p>
          <div className="overflow-x-auto rounded-lg border bg-white">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-100">
                  <th scope="col" className="p-3 text-left">
                    Posición
                  </th>
                  {view.hours.map((hour) => (
                    <th scope="col" className="min-w-28 p-3" key={hour.start}>
                      {hour.start.slice(0, 10)}
                      <br />
                      {stamp(hour.start)}–{stamp(hour.end)}
                      {hour.state === "open" && (
                        <span className="block font-normal">En curso</span>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(positions.length ? positions : ["Sin resultados"]).map(
                  (key) => (
                    <tr className="border-t" key={key}>
                      <th scope="row" className="p-3 text-left">
                        {key}
                      </th>
                      {view.hours.map((hour) => {
                        const rows =
                          hour.state === "open"
                            ? []
                            : hour.results.filter((r) => position(r) === key);
                        return (
                          <td
                            key={hour.start}
                            className="space-y-2 p-3 align-top"
                          >
                            {rows.length === 0 && (labels[hour.state] ?? "—")}
                            {rows.map((row) => (
                              <div key={row.production}>
                                {row.result_id && row.total_penalty !== null ? (
                                  <button
                                    type="button"
                                    className="text-left font-semibold text-primary"
                                    onClick={() =>
                                      setChosen({
                                        row,
                                        publication: view.publication_id,
                                      })
                                    }
                                  >
                                    {number.format(row.total_penalty)}{" "}
                                    <span className="text-xs">
                                      {row.classification}
                                    </span>
                                  </button>
                                ) : (
                                  <span>{labels[row.state] ?? row.state}</span>
                                )}
                                {row.state === "partial" && (
                                  <span className="block">Parcial</span>
                                )}
                                <span className="block break-all text-xs text-gray-600">
                                  {row.production}
                                </span>
                                {row.reason && (
                                  <span className="block break-all text-xs text-gray-600">
                                    {row.reason}
                                  </span>
                                )}
                              </div>
                            ))}
                          </td>
                        );
                      })}
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
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
