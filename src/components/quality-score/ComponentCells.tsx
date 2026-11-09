import { formatNumber } from "@/lib/format";

import { COMPONENT_COLUMNS, type ScoreLike } from "./meta";
import { componentContribution } from "./model";

export function ComponentHeaders() {
  return (
    <>
      {COMPONENT_COLUMNS.map((column) => (
        <th key={column.id} className="px-3 py-2 text-right">
          {column.header}
        </th>
      ))}
    </>
  );
}

export function ComponentCells({ row }: { row: ScoreLike }) {
  return (
    <>
      {COMPONENT_COLUMNS.map((column) => (
        <td key={column.id} className="px-3 py-2 text-right tabular-nums">
          {formatNumber(componentContribution(row, column.id))}
        </td>
      ))}
    </>
  );
}
