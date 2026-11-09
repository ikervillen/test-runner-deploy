import { useState } from "react";

import type { ProductionCustomerScore, ShiftCustomerScore } from "@/types/api";
import { formatDay, formatNumber, formatTime } from "@/lib/format";

import { ComponentCells, ComponentHeaders } from "./ComponentCells";
import { DaySelector } from "./DaySelector";
import { CloseButton, Modal } from "./Modal";
import { formatMainCauses } from "./model";
import { ShiftDetailModal } from "./ShiftDetailModal";
import { ShiftPenaltyTimeline } from "./ShiftPenaltyTimeline";
import { StatusBadge } from "./StatusBadge";

const WORST_SHIFTS = 3;

export function ShiftScoreModal({
  row,
  onClose,
}: {
  row: ProductionCustomerScore;
  onClose: () => void;
}) {
  const [selectedDays, setSelectedDays] = useState<string[]>([]);
  const [detailedShift, setDetailedShift] = useState<ShiftCustomerScore | null>(
    null,
  );
  const days = Array.from(
    new Set(row.shift_scores.map((shift) => shift.operational_day)),
  ).sort();
  const visibleShifts =
    selectedDays.length > 0
      ? row.shift_scores
          .filter((shift) => selectedDays.includes(shift.operational_day))
          .sort((left, right) => left.start.localeCompare(right.start))
      : [...row.shift_scores]
          .sort((left, right) => right.total_penalty - left.total_penalty)
          .slice(0, WORST_SHIFTS);

  return (
    <Modal
      layer="primary"
      onClose={onClose}
      title={
        <>
          <div>
            <h3 className="text-base font-semibold text-gray-900">
              {row.production_id} - desglose por turno
            </h3>
            <p className="mt-1 text-xs text-gray-500">
              Por defecto se muestran los 3 peores turnos individuales. Puedes
              seleccionar uno o varios dias para ver todos sus turnos.
            </p>
          </div>
          <CloseButton onClick={onClose} />
        </>
      }
    >
      <div className="p-5">
        {row.shift_scores.length === 0 ? (
          <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            No hay datos suficientes para calcular el desglose por turno.
          </p>
        ) : (
          <>
            <DaySelector
              days={days}
              selectedDays={selectedDays}
              onChange={setSelectedDays}
            />
            {selectedDays.length > 0 && (
              <ShiftPenaltyTimeline
                shifts={row.shift_scores}
                selectedDays={selectedDays}
              />
            )}
            <ShiftTable shifts={visibleShifts} onSelect={setDetailedShift} />
            <p className="mt-3 text-xs text-gray-500">
              Pulsa sobre un turno para abrir su desglose completo de MIA, AQL,
              DAR y rendimiento ZC.
            </p>
            {detailedShift && (
              <ShiftDetailModal
                productionId={row.production_id}
                shift={detailedShift}
                onClose={() => setDetailedShift(null)}
              />
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

function ShiftTable({
  shifts,
  onSelect,
}: {
  shifts: ShiftCustomerScore[];
  onSelect: (shift: ShiftCustomerScore) => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-[1080px] text-left text-xs">
        <thead className="bg-gray-50 text-gray-500">
          <tr>
            <th className="px-3 py-2">Dia</th>
            <th className="px-3 py-2">Turno</th>
            <th className="px-3 py-2">Horario</th>
            <th className="px-3 py-2 text-right">Penalizacion</th>
            <th className="px-3 py-2">Estado</th>
            <ComponentHeaders />
            <th className="px-3 py-2">Causa principal</th>
            <th className="px-3 py-2">Detalle</th>
          </tr>
        </thead>
        <tbody>
          {shifts.map((shift) => (
            <tr key={shift.shift} className="border-t border-gray-100">
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">
                {formatDay(shift.operational_day)}
              </td>
              <td className="px-3 py-2">
                <button
                  type="button"
                  className="font-semibold text-primary underline-offset-2 hover:underline"
                  onClick={() => onSelect(shift)}
                >
                  {shift.shift_name}
                </button>
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-gray-600">
                {formatTime(shift.start)}-{formatTime(shift.end)}
              </td>
              <td className="px-3 py-2 text-right font-semibold tabular-nums">
                {formatNumber(shift.total_penalty)}
              </td>
              <td className="px-3 py-2">
                <StatusBadge status={shift.status} />
              </td>
              <ComponentCells row={shift} />
              <td className="max-w-xs px-3 py-2 text-gray-700 break-words">
                {formatMainCauses(shift.main_causes)}
              </td>
              <td className="px-3 py-2">
                <button
                  type="button"
                  className="rounded-md border border-gray-200 px-2 py-1 text-[11px] font-semibold text-gray-600 hover:bg-gray-50"
                  onClick={() => onSelect(shift)}
                >
                  Ver detalle
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
