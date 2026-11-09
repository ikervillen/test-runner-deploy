import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CoverageNotice, formatCalculatedUntil } from "./CoverageNotice";
import type { GoldCoverage } from "@/types/api";

const coverage: GoldCoverage[] = [
  {
    id_produccion: "p1",
    origin: "AV23",
    estado: "READY",
    calculated_until: "2026-11-01T13:00:00",
    missing: [],
  },
  {
    id_produccion: "p2",
    origin: "AV23",
    estado: "WAITING_DEPENDENCY",
    calculated_until: "2026-11-01T11:50:00",
    missing: ["CONT_ZC"],
  },
  {
    id_produccion: "p3",
    origin: "AV23",
    estado: "INCOMPLETE",
    calculated_until: null,
    missing: ["LISTA_RECH", "LISTA_AR"],
  },
];

describe("CoverageNotice", () => {
  it("says nothing when every selected production is up to date", () => {
    const { container } = render(
      <CoverageNotice coverage={coverage} productionIds={["p1"]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("tells how far each selected production is calculated and what is missing", () => {
    render(<CoverageNotice coverage={coverage} productionIds={["p2", "p3"]} />);
    expect(screen.getByTestId("coverage-p2")).toHaveTextContent(
      "p2: calculado hasta 01/11/2026 11:50; falta contadores ZC",
    );
    expect(screen.getByTestId("coverage-p3")).toHaveTextContent(
      "p3: sin ningún corte completo todavía; falta rechazos, avisos/rechazos IFA (más de 24 h sin cobertura: ventana incompleta)",
    );
    expect(screen.queryByTestId("coverage-p1")).toBeNull();
  });

  it("formats the cutoff in plant wall-clock time", () => {
    expect(formatCalculatedUntil("2026-11-06T15:40:00")).toBe(
      "calculado hasta 06/11/2026 15:40",
    );
  });
});
