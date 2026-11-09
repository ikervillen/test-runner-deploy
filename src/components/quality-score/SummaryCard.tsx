const VARIANTS = {
  panel: {
    article: "rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-sm",
    label: "text-xs font-semibold uppercase tracking-wide text-gray-400",
    value: "mt-1 text-2xl font-bold text-gray-900",
    detail: "mt-1 text-xs text-gray-500",
  },
  compact: {
    article: "min-w-0 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2",
    label: "text-[11px] font-semibold uppercase tracking-wide text-gray-400",
    value: "mt-1 break-words text-sm font-semibold text-gray-900",
    detail: "mt-1 break-words text-xs text-gray-500",
  },
} as const;

export function SummaryCard({
  label,
  value,
  detail,
  variant = "panel",
}: {
  label: string;
  value: string;
  detail?: string;
  variant?: keyof typeof VARIANTS;
}) {
  const classes = VARIANTS[variant];
  return (
    <article className={classes.article}>
      <p className={classes.label}>{label}</p>
      <p className={classes.value}>{value}</p>
      {detail && <p className={classes.detail}>{detail}</p>}
    </article>
  );
}
