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

export const api = {
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
