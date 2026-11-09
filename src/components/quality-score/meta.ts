import type {
  CustomerQualityLabel,
  ProductionCustomerScore,
  ShiftCustomerScore,
} from "@/types/api";

/** A production or a single shift: both expose the same score shape. */
export type ScoreLike = ProductionCustomerScore | ShiftCustomerScore;

export type CustomerLabel = CustomerQualityLabel | "unknown";

export const STATUS_META: Record<
  CustomerQualityLabel,
  { label: string; className: string }
> = {
  good: {
    label: "Bueno",
    className: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200",
  },
  observed: {
    label: "Observado",
    className: "bg-amber-50 text-amber-800 ring-1 ring-amber-200",
  },
  bad: {
    label: "Malo",
    className: "bg-orange-50 text-orange-700 ring-1 ring-orange-200",
  },
  critical: {
    label: "Critico",
    className: "bg-red-50 text-red-700 ring-1 ring-red-200",
  },
};

export const CUSTOMER_QUALITY_META: Record<
  CustomerLabel,
  { label: string; color: string; order: number }
> = {
  good: { label: "Buena", color: "#10b981", order: 1 },
  observed: { label: "Observada", color: "#f59e0b", order: 2 },
  bad: { label: "Mala", color: "#f97316", order: 3 },
  critical: { label: "Critica", color: "#dc2626", order: 4 },
  unknown: { label: "Sin etiqueta cliente", color: "#94a3b8", order: 5 },
};

/** Component columns shown in ranking tables, in display order. */
export const COMPONENT_COLUMNS: ReadonlyArray<{ id: string; header: string }> =
  [
    { id: "MIA", header: "MIA" },
    { id: "Critico", header: "Criticos" },
    { id: "Grave", header: "Graves" },
    { id: "Mayor", header: "Mayores" },
    { id: "Menor", header: "Menores" },
    { id: "Leve", header: "Leves" },
    { id: "DAR", header: "DAR" },
    { id: "ZC", header: "ZC" },
  ];
