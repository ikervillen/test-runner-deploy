import { formatDay } from "@/lib/format";

import { toggleContiguousDayRange } from "./model";

const CHIP = "rounded-full border px-3 py-1 text-xs font-semibold";
const ACTIVE = "border-primary bg-primary text-white";
const INACTIVE = "border-gray-200 text-gray-600 hover:bg-gray-50";

/** "Worst shifts" chip plus one chip per operational day (contiguous range). */
export function DaySelector({
  days,
  selectedDays,
  onChange,
}: {
  days: string[];
  selectedDays: string[];
  onChange: (days: string[]) => void;
}) {
  return (
    <div className="mb-4 flex flex-wrap gap-2">
      <button
        type="button"
        className={`${CHIP} ${selectedDays.length === 0 ? ACTIVE : INACTIVE}`}
        onClick={() => onChange([])}
      >
        3 peores turnos
      </button>
      {days.map((day) => (
        <button
          type="button"
          key={day}
          className={`${CHIP} ${selectedDays.includes(day) ? ACTIVE : INACTIVE}`}
          onClick={() =>
            onChange(toggleContiguousDayRange(days, selectedDays, day))
          }
        >
          {formatDay(day)}
        </button>
      ))}
    </div>
  );
}
