import { useEffect, useRef, useState } from "react";

export const chartHeight = 260;
export const chartTop = 30;
export const chartBottom = chartHeight - 46;
export const chartGrid = "#dce4e0";
export const chartAxis = "#aebbb6";
export const chartInk = "#53645d";
export const chartTrace = "#157054";

// Draw in CSS pixels like the original desk, without shrinking the plot to
// preserve a fixed viewBox aspect ratio when its container grows.
export function useChartWidth(initial: number) {
  const ref = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(initial);
  useEffect(() => {
    const chart = ref.current;
    if (!chart || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const next = Math.round(entry.contentRect.width);
      if (next > 50) setWidth(next);
    });
    observer.observe(chart);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

export function chartCeiling(maximum: number) {
  return maximum > 0 ? Math.ceil((maximum * 1.08) / 5) * 5 : 1;
}

export function chartTicks(maximum: number) {
  const raw = maximum / 4;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 3, 4, 5, 10]
    .map((n) => n * magnitude)
    .reduce((best, next) =>
      Math.abs(next - raw) < Math.abs(best - raw) ? next : best,
    );
  const ticks = [0];
  for (let value = step; value < maximum; value += step)
    ticks.push(Number(value.toFixed(3)));
  if (ticks.length > 1 && maximum - ticks[ticks.length - 1] < step * 0.75)
    ticks.pop();
  return [...ticks, maximum];
}
