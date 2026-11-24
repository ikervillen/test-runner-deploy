import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { LiveView } from "./LiveView";
import { api, type LiveResult } from "@/services/api";

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
    total_penalty: 0,
    normalized_score: 0,
    classification: "GOOD",
  };
  vi.spyOn(api, "getLiveScores").mockResolvedValue({
    publication_id: "publication",
    published_at: "2026-11-24T14:01:00Z",
    stale: false,
    mia_provisional: true,
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
  const detail = vi.spyOn(api, "getLiveDetail").mockResolvedValue({});
  render(<LiveView />);
  const result = await screen.findByRole("button", { name: /0 GOOD/ });
  expect(screen.queryByText(/999/)).not.toBeInTheDocument();
  fireEvent.click(result);
  expect(await screen.findByText(/Score normalizado 0/)).toBeInTheDocument();
  expect(detail).toHaveBeenCalledWith("publication", "result");
  vi.restoreAllMocks();
});
