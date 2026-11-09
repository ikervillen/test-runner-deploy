import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/services/api";

describe("api", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("loads the production catalog", async () => {
    const response = { lines: ["AV23"], productions: [] };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => response }),
    );

    await expect(api.getCatalog()).resolves.toEqual(response);
  });

  it("uses the unified API error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        json: async () => ({
          error: { message: "Seleccion invalida", request_id: "abc" },
        }),
      }),
    );

    await expect(api.getCatalog()).rejects.toThrow(
      "Seleccion invalida (request_id: abc)",
    );
  });

  it("posts production quality score requests to the dedicated endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ productions: [] }) });
    vi.stubGlobal("fetch", fetchMock);

    await api.queryProductionQualityScore({ selection: selection() });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/customer-score/query",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

function selection() {
  return {
    production_ids: ["AV23_PROD_001"],
    branches: ["AV23-1"],
    metric: "rejected_count",
    window: "10m",
    filters: {
      defect_codes: [],
      cavities: [],
      colors: [],
      sections: [],
      machine_codes: [],
    },
  };
}
