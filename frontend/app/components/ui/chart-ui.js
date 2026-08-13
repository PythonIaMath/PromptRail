"use client";

import * as React from "react";
import * as RechartsPrimitive from "recharts";

function cn(...classes) {
  return classes.filter(Boolean).join(" ");
}

const THEMES = { light: "", dark: ".dark" };
const ChartContext = React.createContext(null);

function useChart() {
  const context = React.useContext(ChartContext);

  if (!context) {
    throw new Error("useChart must be used within a <ChartContainer />");
  }

  return context;
}

function ChartStyle({ id, config }) {
  const colorConfig = Object.entries(config).filter(([, itemConfig]) => itemConfig.theme || itemConfig.color);

  if (!colorConfig.length) {
    return null;
  }

  return (
    <style
      dangerouslySetInnerHTML={{
        __html: Object.entries(THEMES)
          .map(([theme, prefix]) => `
${prefix} [data-chart=${id}] {
${colorConfig
  .map(([key, itemConfig]) => {
    const color = itemConfig.theme?.[theme] || itemConfig.color;
    return color ? `  --color-${key}: ${color};` : null;
  })
  .filter(Boolean)
  .join("\n")}
}
`)
          .join("\n"),
      }}
    />
  );
}

export function ChartContainer({ id, className = "", children, config, ...props }) {
  const uniqueId = React.useId();
  const chartId = `chart-${id || uniqueId.replace(/:/g, "")}`;

  return (
    <ChartContext.Provider value={{ config }}>
      <div className={cn("chart-container", className)} data-chart={chartId} data-slot="chart" {...props}>
        <ChartStyle id={chartId} config={config} />
        <RechartsPrimitive.ResponsiveContainer>
          {children}
        </RechartsPrimitive.ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  );
}

export const ChartTooltip = RechartsPrimitive.Tooltip;

function getPayloadConfig(config, payloadItem) {
  const key = `${payloadItem?.dataKey || payloadItem?.name || ""}`;
  return config[key] || {};
}

export function ChartTooltipContent({
  active,
  payload,
  label,
  className = "",
  indicator = "dot",
  labelFormatter,
  valueFormatter,
}) {
  const { config } = useChart();

  if (!active || !payload?.length) {
    return null;
  }

  const item = payload[0];
  const itemConfig = getPayloadConfig(config, item);
  const data = item.payload || {};
  const displayLabel = labelFormatter ? labelFormatter(label, payload) : data.tooltipLabel || label;
  const displayValue = valueFormatter ? valueFormatter(item.value, data, item) : item.value;
  const color = item.color || itemConfig.color;

  return (
    <div className={cn("chart-tooltip", className)}>
      {displayLabel ? <div className="chart-tooltip-label">{displayLabel}</div> : null}
      <div className="chart-tooltip-row">
        <span
          className={cn("chart-tooltip-indicator", indicator === "line" && "chart-tooltip-indicator-line")}
          style={{ backgroundColor: indicator === "line" ? "transparent" : color, borderColor: color }}
        />
        <span className="chart-tooltip-name">{itemConfig.label || item.name}</span>
        <span className="chart-tooltip-value">{displayValue}</span>
      </div>
    </div>
  );
}
