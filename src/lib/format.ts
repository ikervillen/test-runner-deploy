const NUMBER_FORMAT = new Intl.NumberFormat("es-ES", {
  maximumFractionDigits: 2,
  minimumFractionDigits: 0,
});

const DAY_FORMAT = new Intl.DateTimeFormat("es-ES", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const TIME_FORMAT = new Intl.DateTimeFormat("es-ES", {
  hour: "2-digit",
  minute: "2-digit",
});

export function formatNumber(value: number): string {
  return NUMBER_FORMAT.format(value);
}

export function formatDay(value: string): string {
  return DAY_FORMAT.format(new Date(value));
}

export function formatTime(value: string): string {
  return TIME_FORMAT.format(new Date(value));
}
