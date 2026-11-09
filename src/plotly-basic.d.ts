declare module "plotly.js-basic-dist-min" {
  import type * as Plotly from "plotly.js";

  const plotly: typeof Plotly;
  export default plotly;
}

declare module "react-plotly.js/factory" {
  import type { ComponentType } from "react";
  import type { PlotParams } from "react-plotly.js";
  import type * as Plotly from "plotly.js";

  export default function createPlotlyComponent(
    plotly: typeof Plotly,
  ): ComponentType<PlotParams>;
}
