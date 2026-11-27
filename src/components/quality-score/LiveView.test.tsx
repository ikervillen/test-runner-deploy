import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { LiveView } from "./LiveView";
import { api, type LiveResult } from "@/services/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("shows zero for a closed result, excludes the open score and pins detail", async () => {
  const row: LiveResult = {
    production: "p1",
    line: "AV31",
    machine: 7,
    start: "2026-11-24T14:00:00",
    cutoff: "2026-11-24T15:00:00",
    state: "complete",
    reason: null,
    result_id: "result",
    total_penalty: 86.08,
    normalized_score: 0,
    classification: "GOOD",
    contributions: {
      MIA: 0.75,
      Critico: 0,
      Grave: 50,
      Mayor: 30,
      Menor: 0,
      Leve: 5.33,
      DAR: 0,
      ZC: 0,
    },
  };
  vi.spyOn(api, "getLiveArchive").mockRejectedValue(new Error("unavailable"));
  vi.spyOn(api, "getLiveScores").mockResolvedValue({
    publication_id: "publication",
    published_at: "2026-11-24T14:01:00Z",
    stale: false,
    mia_provisional: true,
    active_productions: [
      {
        line: "AV31",
        machine: 7,
        state: "active",
        production: "p2",
        model: "1048-A",
        start: row.cutoff,
      },
      {
        line: "AV31",
        machine: 8,
        state: "active",
        production: "p3",
        model: "2019-B",
        start: row.cutoff,
      },
    ],
    hours: [
      { start: row.start, end: row.cutoff!, state: "closed", results: [row] },
      {
        start: row.cutoff!,
        end: "2026-11-24T16:00:00",
        state: "open",
        results: [{ ...row, total_penalty: 999 }],
      },
    ],
  });
  const detail = vi.spyOn(api, "getLiveDetail").mockResolvedValue({
    quality_score_components: [{ componente: "mia", contribucion: 0 }],
  });
  render(<LiveView />);
  const result = await screen.findByRole("button", {
    name: /AV31A.*15:00.*86,08.*Bueno/,
  });
  expect(
    screen.getByRole("heading", { name: /AV31A.*Modelo 1048-A/ }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: /AV31B.*Modelo 2019-B/ }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: /AV32A.*Sin producción activa/ }),
  ).toBeInTheDocument();
  expect(
    screen.getAllByRole("img", { name: /Evolución horaria/ }),
  ).toHaveLength(7);
  expect(screen.queryByText("Evolución por turnos")).not.toBeInTheDocument();
  expect(screen.queryByText(/Media horaria ponderada/)).not.toBeInTheDocument();
  const charts = screen.getAllByRole("img", {
    name: /Tendencia provisional|Evolución horaria/,
  });
  expect(charts).toHaveLength(14);
  expect(
    charts.every((chart) => chart.getAttribute("viewBox")?.endsWith(" 260")),
  ).toBe(true);
  expect(screen.getAllByText("Actual")).toHaveLength(7);
  expect(screen.getAllByText("Histórico acumulado no disponible")).toHaveLength(
    7,
  );
  expect(document.querySelectorAll("circle[data-result]")).toHaveLength(1);
  expect(screen.getAllByText("En curso")).toHaveLength(7);
  expect(screen.queryByText(/999/)).not.toBeInTheDocument();
  const table = screen.getByRole("table", {
    name: "Desglose horario de AV31A",
  });
  expect(
    within(table)
      .getAllByRole("columnheader")
      .map((cell) => cell.textContent),
  ).toEqual([
    "Hora",
    "MIA",
    "Crít.",
    "Grav.",
    "May.",
    "Men.",
    "Lev.",
    "DAR",
    "ZC",
    "Total",
  ]);
  expect(
    within(table).getByRole("button", { name: /Detalle Crít.*0/ }),
  ).toBeInTheDocument();
  const grave = within(table).getByRole("button", { name: /Detalle Grav.*50/ });
  expect(detail).not.toHaveBeenCalled();
  fireEvent.click(grave);
  expect(await screen.findByText(/Score normalizado 0/)).toBeInTheDocument();
  expect(detail).toHaveBeenCalledWith("publication", "result");
  expect(result).toHaveAttribute("aria-pressed", "true");
  vi.restoreAllMocks();
});

it("shows the selected handover close without presenting partial components as a total", async () => {
  const old: LiveResult = {
    production: "fenix:old",
    line: "AV32",
    machine: 10,
    start: "2026-11-24T14:00:00",
    cutoff: "2026-11-24T14:20:00",
    state: "partial",
    reason: "MIA_REFERENCE_PENDING",
    result_id: "p1",
    total_penalty: null,
    normalized_score: null,
    classification: null,
    partial_subtotal: 4,
    model: "2939/577",
    component_states: { ZC: "NOT_APPLICABLE", MIA: "WAITING_DEPENDENCY" },
    contributions: { Grave: 4 },
  };
  const incoming: LiveResult = {
    ...old,
    production: "fenix:new",
    start: "2026-11-24T14:20:00",
    cutoff: "2026-11-24T15:00:00",
    state: "complete",
    reason: null,
    result_id: "p2",
    total_penalty: 2,
    component_states: {},
    contributions: { MIA: 2 },
  };
  vi.spyOn(api, "getLiveArchive").mockRejectedValue(new Error("unavailable"));
  vi.spyOn(api, "getLiveScores").mockResolvedValue({
    publication_id: "publication",
    published_at: "2026-11-24T15:00:00Z",
    stale: false,
    mia_provisional: true,
    hours: [
      {
        start: "2026-11-24T13:00:00",
        end: old.start,
        state: "before_t0",
        results: [],
      },
      {
        start: old.start,
        end: incoming.cutoff!,
        state: "closed",
        results: [old],
      },
      {
        start: incoming.cutoff!,
        end: "2026-11-24T16:00:00",
        state: "closed",
        results: [
          {
            ...incoming,
            state: "pending",
            result_id: null,
            total_penalty: null,
            contributions: {},
          },
        ],
      },
      {
        start: "2026-11-24T16:00:00",
        end: "2026-11-24T17:00:00",
        state: "open",
        results: [],
      },
    ],
  });
  render(<LiveView />);
  const table = await screen.findByRole("table", {
    name: "Desglose horario de AV32B",
  });
  expect(
    within(table).getAllByRole("rowheader", { name: /^15:00/ }),
  ).toHaveLength(1);
  expect(
    within(table).getByRole("rowheader", { name: /^15:00/ }),
  ).toHaveAttribute(
    "title",
    expect.stringContaining("2939/577 · fenix:old · 14:00–14:20"),
  );
  expect(within(table).getByText("N/A")).toBeInTheDocument();
  expect(
    within(table).getByRole("button", { name: /Detalle Grav./ }),
  ).toHaveTextContent("4");
  expect(
    within(table).queryByRole("button", { name: /Ver desglose/ }),
  ).not.toBeInTheDocument();
  for (const state of ["Anterior a T0", "Parcial", "Pendiente", "En curso"]) {
    expect(within(table).getByText(state)).toBeInTheDocument();
  }
  const other = screen.getByRole("table", {
    name: "Desglose horario de AV32A",
  });
  expect(within(other).getAllByText("Sin producción")).toHaveLength(2);
  vi.restoreAllMocks();
});

it("shows the archived shift and opens the original score on its Gold publication", async () => {
  const result: LiveResult = {
    production: "fenix:series",
    line: "AV21",
    machine: 4,
    model: "model-A",
    start: "2026-11-24T10:20:00",
    cutoff: "2026-11-24T11:00:00",
    state: "complete",
    reason: null,
    result_id: "archived-result",
    total_penalty: 5,
    normalized_score: 1,
    classification: "good",
    contributions: { MIA: 5 },
  };
  vi.spyOn(api, "getLiveArchive").mockResolvedValue({
    t0: result.start,
    eligible_until: "2026-11-24T14:00:00",
    publication: "archived-publication",
    next_before: null,
    shifts: [
      {
        start: "2026-11-24T06:00:00",
        end: "2026-11-24T14:00:00",
        name: "Manana",
        partial_t0: true,
        accruing: false,
        hours: [
          {
            start: "2026-11-24T10:00:00",
            end: result.cutoff!,
            state: "closed",
            results: [result],
          },
        ],
      },
    ],
  });
  const detail = vi
    .spyOn(api, "getArchiveDetail")
    .mockResolvedValue({ configuration: { version: "official" } });
  render(<LiveView historyOnly />);
  const graph = await screen.findAllByRole("img", {
    name: "Tendencia provisional por turno",
  });
  expect(graph).toHaveLength(7);
  const shifts = screen.getAllByRole("button", { name: /24\/11 · Mañana/ });
  fireEvent.click(shifts[0]);
  expect(screen.getByText(/11:00 · model-A · Completo/)).toBeInTheDocument();
  const table = screen.getByRole("table", {
    name: "Scores horarios oficiales del turno",
  });
  fireEvent.click(within(table).getByRole("button", { name: "5" }));
  expect(await screen.findByText("Configuración efectiva")).toBeInTheDocument();
  expect(detail).toHaveBeenCalledWith(
    "archived-publication",
    "archived-result",
  );
});
