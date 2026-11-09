import type { ShiftCustomerScore } from "@/types/api";
import { formatDay, formatNumber } from "@/lib/format";
import { medianValue, sampleSigma, valueTicks } from "@/lib/statistics";

import {
  formatCompactShiftLabel,
  selectedDayRange,
  temporalLineSegments,
} from "./model";

const WIDTH = 980;
const HEIGHT = 260;
const MARGIN = { top: 26, right: 28, bottom: 58, left: 56 } as const;
const PLOT_WIDTH = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_HEIGHT = HEIGHT - MARGIN.top - MARGIN.bottom;
const LINE_COLOR = "#005b2e";

type TimelinePoint = {
  shift: ShiftCustomerScore;
  timestamp: number;
  isSelected: boolean;
};

/** Penalty per shift over time for the selected days plus the previous 24h. */
export function ShiftPenaltyTimeline({
  shifts,
  selectedDays,
}: {
  shifts: ShiftCustomerScore[];
  selectedDays: string[];
}) {
  const points = timelinePoints(shifts, selectedDays);
  if (points.length === 0) return null;

  const penalties = shifts.map((shift) => shift.total_penalty);
  const median = medianValue(penalties);
  const sigma = sampleSigma(penalties);
  const bandLow = Math.max(0, median - sigma);
  const bandHigh = median + sigma;
  const scale = axisScales(points, [bandLow, bandHigh, median]);
  const previous = points.filter((point) => !point.isSelected);
  const previousBand =
    previous.length > 0
      ? {
          start: scale.x(Math.min(...previous.map((p) => p.timestamp))),
          end: scale.x(Math.max(...previous.map((p) => p.timestamp))),
        }
      : null;
  const firstDay = formatDay(selectedDays[0]);
  const lastDay = formatDay(selectedDays[selectedDays.length - 1]);

  return (
    <section className="mb-4 rounded-lg border border-gray-200 bg-white p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold text-gray-900">
            Evolucion temporal de penalizacion por turno
          </h4>
          <p className="mt-1 text-xs text-gray-500">
            Incluye las 24h previas a {firstDay}
            {firstDay !== lastDay ? ` y la seleccion hasta ${lastDay}` : ""}.
          </p>
        </div>
        <Legend />
      </div>
      <svg
        className="mt-3 h-[260px] w-full"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="Evolucion temporal de penalizacion por turno"
      >
        {previousBand && (
          <rect
            x={Math.max(MARGIN.left, previousBand.start - 12)}
            y={MARGIN.top}
            width={Math.max(previousBand.end - previousBand.start + 24, 1)}
            height={PLOT_HEIGHT}
            fill="#e5e7eb"
            opacity="0.42"
          />
        )}
        <rect
          x={MARGIN.left}
          y={scale.y(bandHigh)}
          width={PLOT_WIDTH}
          height={Math.max(scale.y(bandLow) - scale.y(bandHigh), 1)}
          fill="#fef3c7"
          opacity="0.32"
        />
        {valueTicks(scale.yMin, scale.yMax).map((tick) => (
          <g key={tick}>
            <line
              x1={MARGIN.left}
              x2={WIDTH - MARGIN.right}
              y1={scale.y(tick)}
              y2={scale.y(tick)}
              stroke="#e5e7eb"
              strokeWidth="1"
            />
            <text
              x={MARGIN.left - 10}
              y={scale.y(tick) + 4}
              textAnchor="end"
              className="fill-gray-500 text-[10px]"
            >
              {formatNumber(tick)}
            </text>
          </g>
        ))}
        <line
          x1={MARGIN.left}
          x2={WIDTH - MARGIN.right}
          y1={scale.y(median)}
          y2={scale.y(median)}
          stroke="#4b5563"
          strokeWidth="1.4"
          strokeDasharray="6 5"
        />
        {temporalLineSegments(points).map((segment, index) => (
          <path
            key={`segment-${index}`}
            d={segment
              .map(
                (point, i) =>
                  `${i === 0 ? "M" : "L"} ${scale.x(point.timestamp)} ${scale.y(
                    point.shift.total_penalty,
                  )}`,
              )
              .join(" ")}
            fill="none"
            stroke={LINE_COLOR}
            strokeWidth="2"
            strokeLinecap="round"
          />
        ))}
        {points.map((point) => (
          <g key={point.shift.shift}>
            <circle
              cx={scale.x(point.timestamp)}
              cy={scale.y(point.shift.total_penalty)}
              r={point.isSelected ? 4.5 : 3.5}
              fill={point.isSelected ? LINE_COLOR : "#9ca3af"}
              opacity={point.isSelected ? "1" : "0.7"}
              stroke="#ffffff"
              strokeWidth="1.5"
            />
            <title>
              {formatDay(point.shift.operational_day)} {point.shift.shift_name}{" "}
              - {formatNumber(point.shift.total_penalty)}
            </title>
          </g>
        ))}
        {points.map((point, index) => {
          const shouldLabel =
            index === 0 ||
            index === points.length - 1 ||
            point.shift.shift_name === "Manana";
          if (!shouldLabel) return null;
          return (
            <text
              key={`${point.shift.shift}-label`}
              x={scale.x(point.timestamp)}
              y={HEIGHT - 24}
              textAnchor="middle"
              className="fill-gray-500 text-[10px]"
            >
              {formatCompactShiftLabel(point.shift)}
            </text>
          );
        })}
        <line
          x1={MARGIN.left}
          x2={MARGIN.left}
          y1={MARGIN.top}
          y2={HEIGHT - MARGIN.bottom}
          stroke="#9ca3af"
          strokeWidth="1"
        />
        <line
          x1={MARGIN.left}
          x2={WIDTH - MARGIN.right}
          y1={HEIGHT - MARGIN.bottom}
          y2={HEIGHT - MARGIN.bottom}
          stroke="#9ca3af"
          strokeWidth="1"
        />
        <text
          x={MARGIN.left}
          y={HEIGHT - 4}
          textAnchor="start"
          className="fill-gray-500 text-[10px]"
        >
          24h previas
        </text>
        <text
          x={WIDTH - MARGIN.right}
          y={HEIGHT - 4}
          textAnchor="end"
          className="fill-gray-500 text-[10px]"
        >
          dias seleccionados
        </text>
      </svg>
      <p className="text-xs text-gray-500">
        Referencia calculada con todos los turnos de la produccion: mediana{" "}
        {formatNumber(median)}; banda +/- 1 sigma ({formatNumber(sigma)}).
      </p>
    </section>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-3 text-[11px] text-gray-600">
      <span className="inline-flex items-center gap-1.5">
        <span className="h-2.5 w-5 rounded-sm bg-gray-200 ring-1 ring-gray-300" />
        Franja 24h previas
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="h-0.5 w-5 bg-primary" />
        Penalizacion por turno
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="h-0.5 w-5 border-t border-dashed border-gray-500" />
        Mediana produccion
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span className="h-2.5 w-5 rounded-sm bg-amber-100 ring-1 ring-amber-200" />
        Mediana +/- 1 sigma
      </span>
    </div>
  );
}

function timelinePoints(
  shifts: ShiftCustomerScore[],
  selectedDays: string[],
): TimelinePoint[] {
  const range = selectedDayRange(selectedDays);
  return shifts
    .map((shift) => ({
      shift,
      timestamp: new Date(shift.start).getTime(),
      isSelected: selectedDays.includes(shift.operational_day),
    }))
    .filter(
      (point) =>
        point.isSelected ||
        (Number.isFinite(point.timestamp) &&
          point.timestamp >= range.previousStart &&
          point.timestamp < range.selectedStart),
    )
    .sort((left, right) => left.timestamp - right.timestamp);
}

function axisScales(points: TimelinePoint[], references: number[]) {
  const values = [
    ...points.map((point) => point.shift.total_penalty),
    ...references,
  ];
  const maxValue = Math.max(...values, 1);
  const minValue = Math.min(0, ...values);
  const padding = maxValue * 0.08;
  const yMin = Math.max(0, minValue - padding);
  const yMax = maxValue + padding;
  const timestamps = points.map((point) => point.timestamp);
  const timeMin = Math.min(...timestamps);
  const timeSpan = Math.max(Math.max(...timestamps) - timeMin, 1);
  return {
    yMin,
    yMax,
    x: (timestamp: number) =>
      MARGIN.left + ((timestamp - timeMin) / timeSpan) * PLOT_WIDTH,
    y: (value: number) =>
      MARGIN.top +
      (1 - (value - yMin) / Math.max(yMax - yMin, 1)) * PLOT_HEIGHT,
  };
}
