import type {
  ApiErrorResponse,
  Catalog,
  CustomerQualityQuery,
  CustomerQualityResponse,
  GoldStatus,
  ProductionQualityScoreQuery,
  ProductionQualityScoreResponse,
} from "@/types/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ApiErrorResponse;
    const requestId = body.error?.request_id
      ? ` (request_id: ${body.error.request_id})`
      : "";
    throw new Error(
      `${body.error?.message ?? `Error HTTP ${response.status}`}${requestId}`,
    );
  }
  return (await response.json()) as T;
}

export type LiveResult = {
  production: string;
  line: string;
  machine: number | null;
  start: string;
  cutoff: string | null;
  state: string;
  reason: string | null;
  result_id: string | null;
  total_penalty: number | null;
  normalized_score: number | null;
  classification: string | null;
  partial_subtotal?: number | null;
  evaluated_weight?: number | null;
  component_states?: Partial<
    Record<string, "READY" | "WAITING_DEPENDENCY" | "NOT_APPLICABLE">
  >;
  model?: string | null;
  contributions: Partial<Record<string, number>>;
};

export type LiveScores = {
  publication_id: string;
  published_at: string;
  stale: boolean;
  mia_provisional: boolean;
  hours: { start: string; end: string; state: string; results: LiveResult[] }[];
};

export type ArchivedShift = {
  start: string;
  end: string;
  name: string;
  partial_t0: boolean;
  accruing: boolean;
  hours: LiveScores["hours"];
};

export type LiveArchive = {
  t0: string;
  eligible_until: string;
  publication: string;
  shifts: ArchivedShift[];
  next_before: string | null;
};

export const api = {
  getLiveArchive: (before: string | null, signal: AbortSignal) =>
    request<LiveArchive>(
      `/api/live/archive${before ? `?before=${encodeURIComponent(before)}` : ""}`,
      { signal },
    ),
  getArchiveDetail: (publication: string, result: string) =>
    request<Record<string, unknown>>(
      `/api/live/archive/${encodeURIComponent(publication)}/${encodeURIComponent(result)}`,
    ),

  getHistoryCatalog: () => request<Catalog>("/api/history/catalog"),
  getLiveScores: (signal: AbortSignal) =>
    request<LiveScores>("/api/live/scores", { signal }),
  getLiveDetail: (publication: string, result: string) =>
    request<Record<string, Record<string, unknown>[]>>(
      `/api/live/scores/${encodeURIComponent(publication)}/${encodeURIComponent(result)}`,
    ),
  queryHistoryScore: (payload: ProductionQualityScoreQuery) =>
    request<ProductionQualityScoreResponse>(
      "/api/history/customer-score/query",
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  queryHistoryQuality: (payload: CustomerQualityQuery) =>
    request<CustomerQualityResponse>("/api/history/customer-quality/query", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getCatalog: () => request<Catalog>("/api/catalog"),
  getGoldStatus: () => request<GoldStatus>("/api/gold/status"),
  queryCustomerQuality: (payload: CustomerQualityQuery) =>
    request<CustomerQualityResponse>("/api/customer-quality/query", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  queryProductionQualityScore: (payload: ProductionQualityScoreQuery) =>
    request<ProductionQualityScoreResponse>("/api/customer-score/query", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};
