import type {
  AqlCategoryScore,
  ProductionCatalog,
  ShiftCustomerScore,
} from "@/types/api";
import { formatDay, formatNumber } from "@/lib/format";

import type { ScoreLike } from "./meta";

const DAY_MS = 86_400_000;

export function componentContribution(
  row: ScoreLike,
  component: string,
): number {
  return (
    row.components.find((item) => item.component === component)?.contribution ??
    0
  );
}

export function formatMainCauses(causes: ScoreLike["main_causes"]): string {
  if (causes.length === 0) return "-";
  return causes
    .map(
      (cause) =>
        `${cause.label}: ${formatNumber(cause.contribution)} (${(
          cause.share_of_total_penalty * 100
        ).toFixed(1)} %)`,
    )
    .join(" | ");
}

export function formatAqlThresholds(row: AqlCategoryScore): string {
  if (row.aql_percent == null) return "-";
  if (row.aql_percent <= 0) {
    return row.exposure_percent > 0 ? "Presencia con AQL 0" : "Sin presencia";
  }
  return "Progresivo: 1AQL=5; 2AQL=10";
}

const PENDING_REASON_LABELS: Record<string, string> = {
  SOURCE_COVERAGE_PENDING: "Cobertura de fuentes incompleta",
  LISTA_RECH_BASE_MISSING: "Falta una base válida de botellas inspeccionadas",
  ZC_THEORETICAL_BASE_MISSING: "Faltan contadores teóricos de zona caliente",
  MODEL_SEGMENT_DAR_PROFILE_PENDING: "Perfil DAR del segmento pendiente",
  ASSIGNED_CAPACITY_PENDING: "Capacidad asignada pendiente",
  CAPACITY_COVERAGE_GAP: "Hay huecos en la capacidad disponible",
  AR_CAVITY_UNRESOLVED: "Cavidad de un evento pendiente de identificar",
  AR_CATEGORY_UNRESOLVED: "Criticidad de un evento pendiente",
};

export function pendingReason(reason: string): string {
  if (reason.startsWith("MIA_REFERENCE_PENDING:"))
    return `Histórico insuficiente para el defecto ${reason.split(":")[1]}`;
  return (
    PENDING_REASON_LABELS[reason] ?? "Cálculo pendiente de completar sus datos"
  );
}

export function productionDurationDays(production: ProductionCatalog): number {
  const start = new Date(production.start_day).getTime();
  const end = new Date(production.end_day).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 1;
  return Math.max((end - start) / DAY_MS + 1, 1);
}

/** Bubble diameters in px, linearly scaled between 18 and 52. */
export function scaledBubbleSizes(values: number[]): number[] {
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return values.map(() => 26);
  return values.map((value) => 18 + ((value - min) / (max - min)) * 34);
}

export function selectedDayRange(selectedDays: string[]): {
  previousStart: number;
  selectedStart: number;
} {
  const ordered = [...selectedDays].sort();
  const first = new Date(`${ordered[0]}T00:00:00`).getTime();
  return { previousStart: first - DAY_MS, selectedStart: first };
}

/**
 * Day chips select a contiguous range. Clicking an edge shrinks the range,
 * clicking inside collapses to that day, clicking outside extends it.
 */
export function toggleContiguousDayRange(
  availableDays: string[],
  selectedDays: string[],
  clickedDay: string,
): string[] {
  const orderedSelection = [...selectedDays].sort();
  if (orderedSelection.includes(clickedDay)) {
    if (orderedSelection.length === 1) return [];
    const first = orderedSelection[0];
    const last = orderedSelection[orderedSelection.length - 1];
    if (clickedDay === first) {
      return availableDays.filter((day) => day > clickedDay && day <= last);
    }
    if (clickedDay === last) {
      return availableDays.filter((day) => day >= first && day < clickedDay);
    }
    return [clickedDay];
  }
  const anchors = [...orderedSelection, clickedDay].sort();
  const first = anchors[0];
  const last = anchors[anchors.length - 1];
  return availableDays.filter((day) => day >= first && day <= last);
}

/** Break the line where consecutive shifts are more than 12h apart. */
export function temporalLineSegments<T extends { timestamp: number }>(
  points: T[],
): T[][] {
  if (points.length === 0) return [];
  const maxConnectedGapMs = 12 * 60 * 60 * 1000;
  const segments: T[][] = [[points[0]]];
  for (let index = 1; index < points.length; index += 1) {
    const point = points[index];
    const previous = points[index - 1];
    if (point.timestamp - previous.timestamp > maxConnectedGapMs) {
      segments.push([point]);
    } else {
      segments[segments.length - 1].push(point);
    }
  }
  return segments;
}

export function formatCompactShiftLabel(shift: ShiftCustomerScore): string {
  return `${formatDay(shift.operational_day).slice(0, 5)} ${shift.shift_name.slice(0, 1)}`;
}
