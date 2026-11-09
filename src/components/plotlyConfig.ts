import type { Config } from "plotly.js";

export const INTERACTIVE_PLOT_CONFIG = {
  displayModeBar: "hover",
  responsive: true,
  doubleClick: "reset",
  modeBarButtonsToRemove: ["select2d", "lasso2d"],
  displaylogo: false,
} satisfies Partial<Config>;
