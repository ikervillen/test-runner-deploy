import type { CustomerQualityLabel } from "@/types/api";

import { STATUS_META } from "./meta";

export function StatusBadge({ status }: { status: CustomerQualityLabel }) {
  const meta = STATUS_META[status] ?? STATUS_META.observed;
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${meta.className}`}
    >
      {meta.label}
    </span>
  );
}
