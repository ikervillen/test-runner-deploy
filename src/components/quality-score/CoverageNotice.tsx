import type { GoldCoverage } from "@/types/api";

const FAMILY_LABELS: Record<string, string> = {
  LISTA_DEFECTOS: "defectos MIA",
  LISTA_RECH: "rechazos",
  LISTA_AR: "avisos/rechazos IFA",
  CONT_ZC: "contadores ZC",
};

export function familyLabel(code: string): string {
  return FAMILY_LABELS[code] ?? code;
}

export function formatCalculatedUntil(value: string | null): string {
  if (!value) return "sin ningún corte completo todavía";
  const [day, time] = value.split("T");
  const [year, month, dayOfMonth] = day.split("-");
  return `calculado hasta ${dayOfMonth}/${month}/${year} ${time.slice(0, 5)}`;
}

/** Per selected production: how far Gold has calculated it and which source is missing. */
export function CoverageNotice({
  coverage,
  productionIds,
}: {
  coverage: GoldCoverage[];
  productionIds: string[];
}) {
  const selected = new Set(productionIds);
  const behind = coverage.filter(
    (row) => selected.has(row.id_produccion) && row.estado !== "READY",
  );
  if (behind.length === 0) return null;
  return (
    <section
      role="status"
      className="rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900"
    >
      <p className="font-semibold">
        Producciones calculadas hasta el último corte acreditado por los datos
      </p>
      {behind.map((row) => (
        <p
          key={row.id_produccion}
          data-testid={`coverage-${row.id_produccion}`}
        >
          {row.id_produccion}: {formatCalculatedUntil(row.calculated_until)}
          {row.missing.length > 0
            ? `; falta ${row.missing.map(familyLabel).join(", ")}`
            : "; esperando datos del siguiente intervalo"}
          {row.estado === "INCOMPLETE"
            ? " (más de 24 h sin cobertura: ventana incompleta)"
            : ""}
        </p>
      ))}
    </section>
  );
}
