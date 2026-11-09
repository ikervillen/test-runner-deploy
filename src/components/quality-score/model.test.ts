import { describe, expect, it } from "vitest";

import { valueTicks } from "@/lib/statistics";

import {
  pendingReason,
  scaledBubbleSizes,
  temporalLineSegments,
  toggleContiguousDayRange,
} from "./model";

const DAYS = ["2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16"];

describe("toggleContiguousDayRange", () => {
  it("extends the selection to a contiguous range", () => {
    expect(
      toggleContiguousDayRange(DAYS, ["2024-02-13"], "2024-02-15"),
    ).toEqual(["2024-02-13", "2024-02-14", "2024-02-15"]);
  });

  it("shrinks from the clicked edge and collapses on an inner day", () => {
    const range = ["2024-02-13", "2024-02-14", "2024-02-15"];
    expect(toggleContiguousDayRange(DAYS, range, "2024-02-13")).toEqual([
      "2024-02-14",
      "2024-02-15",
    ]);
    expect(toggleContiguousDayRange(DAYS, range, "2024-02-15")).toEqual([
      "2024-02-13",
      "2024-02-14",
    ]);
    expect(toggleContiguousDayRange(DAYS, range, "2024-02-14")).toEqual([
      "2024-02-14",
    ]);
  });

  it("clears a single selected day", () => {
    expect(
      toggleContiguousDayRange(DAYS, ["2024-02-14"], "2024-02-14"),
    ).toEqual([]);
  });
});

describe("temporalLineSegments", () => {
  it("breaks the line when consecutive points are more than 12h apart", () => {
    const hour = 60 * 60 * 1000;
    const points = [0, 8 * hour, 16 * hour, 40 * hour].map((timestamp) => ({
      timestamp,
    }));
    expect(
      temporalLineSegments(points).map((segment) => segment.length),
    ).toEqual([3, 1]);
  });
});

describe("scaledBubbleSizes", () => {
  it("maps durations to a bounded diameter range", () => {
    expect(scaledBubbleSizes([1, 3, 5])).toEqual([18, 35, 52]);
    expect(scaledBubbleSizes([2, 2])).toEqual([26, 26]);
  });
});

describe("pendingReason", () => {
  it("explains known reasons and the MIA reference defect", () => {
    expect(pendingReason("MIA_REFERENCE_PENDING:17")).toBe(
      "Histórico insuficiente para el defecto 17",
    );
    expect(pendingReason("UNKNOWN_CODE")).toBe(
      "Cálculo pendiente de completar sus datos",
    );
  });
});

describe("valueTicks", () => {
  it("produces at most six nice ticks covering the range", () => {
    const ticks = valueTicks(0, 37);
    expect(ticks.length).toBeLessThanOrEqual(6);
    expect(ticks[0]).toBe(0);
    expect(ticks[ticks.length - 1]).toBeGreaterThanOrEqual(30);
  });
});
