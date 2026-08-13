"use client";

import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from "./ui/chart-ui.js";

const requestChartConfig = {
  requests: {
    label: "Request",
    color: "#ffbd63",
  },
};

function formatRequestRate(value) {
  const number = Number(value || 0);
  if (number >= 10) {
    return `${number.toFixed(0)}/min`;
  }
  if (number >= 1) {
    return `${number.toFixed(1)}/min`;
  }
  return `${number.toFixed(2)}/min`;
}

export default function RequestTrendChart({ data }) {
  return (
    <ChartContainer
      className="request-trend-chart"
      config={requestChartConfig}
      aria-label="Average requests per minute over the last 48 hours"
    >
      <AreaChart
        accessibilityLayer
        data={data}
        margin={{
          left: 12,
          right: 12,
          bottom: 4,
        }}
      >
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="hourIndex"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          interval={7}
          tickFormatter={(value) => data[value]?.label || ""}
        />
        <YAxis
          hide
          domain={[0, (dataMax) => Math.max(0.1, Number(dataMax || 0))]}
        />
        <ChartTooltip
          cursor={false}
          content={(
            <ChartTooltipContent
              indicator="line"
              labelFormatter={(_, payload) => payload?.[0]?.payload?.tooltipLabel}
              valueFormatter={(value, payload) => `${formatRequestRate(value)} (${payload.count} requests)`}
            />
          )}
        />
        <Area
          dataKey="requests"
          type="monotone"
          fill="var(--color-requests)"
          fillOpacity={0.4}
          stroke="var(--color-requests)"
          strokeWidth={3}
          activeDot={{ r: 4.5, strokeWidth: 2 }}
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ChartContainer>
  );
}
