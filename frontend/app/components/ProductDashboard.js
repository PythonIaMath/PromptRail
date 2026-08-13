"use client";

import { authClient } from "../lib/auth-client.js";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Progress,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "./ui/dashboard-ui.js";
const ROUTE_EVENT_LIMIT = 20;
const LOCAL_DEBUG_ROUTE_EVENT_LIMIT = 50;
const ANALYTICS_USAGE_LOG_LIMIT = 750;
const DASHBOARD_THEME_STORAGE_KEY = "lerouter-dashboard-theme";
const labColors = ["#2d8fbd", "#4b7f52", "#d7a22a", "#b05c42", "#6d6fb3", "#6f7d88"];
const routeColors = ["#dceeff", "#e7f4ff", "#eef8ff", "#f4fbff", "#d4e9fb", "#c8dff4"];
const routeDistributionModes = [
  ["route", "Route"],
  ["provider", "Provider"],
  ["model", "Model"],
];
const RequestTrendChart = dynamic(() => import("./RequestTrendChart.js"), {
  ssr: false,
  loading: () => <div className="request-trend-chart request-trend-chart-loading" aria-hidden="true" />,
});
const dashboardPages = {
  analytics: {
    href: "/dashboard/analytics",
    label: "Analytics",
    title: "Analytics",
    description: "Request volume, spend, token usage, cache efficiency, and model routing behavior.",
  },
  credit: {
    href: "/dashboard/credit",
    label: "Credit",
    title: "Credit",
    description: "Recharge pay-as-you-go credits and review your available balance.",
  },
  apiKeys: {
    href: "/dashboard/api-keys",
    label: "API Keys",
    title: "API Keys",
    description: "Create, rename, and monitor routed access keys.",
  },
  settings: {
    href: "/dashboard/settings",
    label: "Settings",
    title: "Settings",
    description: "Manage account access, password, and workspace setup.",
  },
  setup: {
    href: "/dashboard/setup",
    label: "Setup",
    title: "Setup",
    description: "Copy the install prompt for Hermes or OpenClaw.",
  },
};

function dashboardPath(activePage) {
  return dashboardPages[activePage]?.href || dashboardPages.analytics.href;
}

const planOptions = [
  { label: "Weekly", value: "weekly" },
  { label: "Monthly", value: "monthly" },
  { label: "Quarterly", value: "quarterly" },
  { label: "Yearly", value: "yearly" },
];
const inferenceModeOptions = [
  { label: "User managed", value: "user_managed" },
  { label: "Router managed", value: "router_managed" },
];
const runtimeOptions = [
  { label: "Hermes", value: "hermes" },
  { label: "OpenClaw", value: "openclaw" },
];

function makeRouteId(workspaceName) {
  const seed = (workspaceName || "workspace")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 32);
  return `route_${seed || "team"}`;
}

function runtimeLabel(runtime) {
  return runtime === "openclaw" ? "OpenClaw" : "Hermes";
}

function dashboardDataRequirements(activePage) {
  return {
    budget: activePage === "analytics" || activePage === "credit" || activePage === "apiKeys" || activePage === "settings",
    usage: activePage === "analytics",
    apiKeys: activePage === "analytics" || activePage === "apiKeys",
    pollMs: activePage === "analytics" ? 10000 : activePage === "credit" || activePage === "apiKeys" ? 30000 : 0,
  };
}

function readJson(key) {
  const value = window.localStorage.getItem(key);

  if (!value) {
    return null;
  }

  try {
    return JSON.parse(value);
  } catch {
    window.localStorage.removeItem(key);
    return null;
  }
}

function asCurrency(value) {
  return `$${Number(value || 0).toFixed(2)}`;
}

function asPercent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function asCompactNumber(value) {
  const number = Number(value || 0);
  if (number >= 1_000_000) {
    return `${(number / 1_000_000).toFixed(number >= 10_000_000 ? 0 : 2)}M`;
  }
  if (number >= 1_000) {
    return `${(number / 1_000).toFixed(number >= 10_000 ? 0 : 1)}K`;
  }
  return Math.round(number).toLocaleString("en");
}

function formatTime(value) {
  if (!value) {
    return "-";
  }

  try {
    return new Intl.DateTimeFormat("en", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(value));
  } catch {
    return "-";
  }
}

function formatLatency(value) {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  const latency = Number(value);
  return Number.isFinite(latency) ? `${Math.round(latency)}ms` : "-";
}

function labCompletionSegments(completions) {
  if (!completions || typeof completions !== "object") {
    return [];
  }

  const total = Object.values(completions).reduce((sum, count) => sum + Number(count || 0), 0);
  if (!total) {
    return [];
  }

  return Object.entries(completions)
    .map(([lab, count], index) => ({
      lab,
      count: Number(count || 0),
      percent: Number(count || 0) / total,
      color: labColors[index % labColors.length],
    }))
    .sort((a, b) => b.count - a.count);
}

function labCompletionSegmentsFromLogs(usageLogs) {
  const completions = {};

  for (const event of usageLogs) {
    if (event.metadata?.status === "started" || event.metadata?.kind === "routing_operation") {
      continue;
    }

    const lab = event.modelLab || event.metadata?.modelLab || event.modelCompany || event.triggeredLabs?.[0] || event.triggeredProviders?.[0];
    if (!lab) {
      continue;
    }
    completions[lab] = Number(completions[lab] || 0) + 1;
  }

  return labCompletionSegments(completions);
}

function parseLabCompletions(user, mongoUser, usageLogs) {
  const logSegments = labCompletionSegmentsFromLogs(usageLogs);
  if (logSegments.length) {
    return logSegments;
  }

  const localSegments = labCompletionSegments(user?.labCompletions);
  if (localSegments.length) {
    return localSegments;
  }

  const mongoSegments = labCompletionSegments(mongoUser?.labCompletions);
  if (mongoSegments.length) {
    return mongoSegments;
  }

  return labCompletionSegments(user?.providerCompletions || mongoUser?.providerCompletions);
}

function buildConicGradient(labSegments) {
  if (!labSegments.length) {
    return "#edf2f6";
  }

  let cursor = 0;
  const stops = labSegments.map((segment) => {
    const start = cursor;
    cursor += segment.percent * 100;
    return `${segment.color} ${start}% ${cursor}%`;
  });

  return `conic-gradient(${stops.join(", ")})`;
}

function routeLabel(event) {
  return (
    event.routeName
    || event.routeId
    || event.metadata?.routeName
    || event.metadata?.route_name
    || event.metadata?.routeId
    || event.metadata?.route_id
    || "default"
  );
}

function providerLabel(event) {
  return labLabel(event);
}

function distributionLabel(event, dimension) {
  if (dimension === "provider") {
    return providerLabel(event);
  }
  if (dimension === "model") {
    return preciseModelName(event);
  }
  return routeLabel(event);
}

function buildRouteDistribution(events, dimension = "route") {
  const counts = new Map();
  for (const event of events) {
    if (!isCompletedRequest(event)) {
      continue;
    }
    const label = distributionLabel(event, dimension);
    counts.set(label, Number(counts.get(label) || 0) + 1);
  }

  const total = [...counts.values()].reduce((sum, count) => sum + count, 0);
  const maxCount = Math.max(1, ...counts.values());
  const routes = [...counts.entries()]
    .map(([route, count], index) => ({
      route,
      count,
      color: routeColors[index % routeColors.length],
      percent: total ? count / total : 0,
      width: count / maxCount,
    }))
    .sort((a, b) => b.count - a.count || a.route.localeCompare(b.route));

  return { routes, total };
}

function isCompletedRequest(event) {
  return event.metadata?.kind !== "routing_operation" && event.metadata?.status !== "started";
}

function isVisibleRouteEvent(event) {
  return isCompletedRequest(event);
}

function usageFromEvent(event) {
  const usage = event.metadata?.usage;
  return usage && typeof usage === "object" ? usage : {};
}

function tokenCountFromUsage(usage) {
  return Number(
    usage.total_tokens
    || usage.totalTokens
    || (
      Number(usage.prompt_tokens || usage.input_tokens || usage.promptTokens || usage.inputTokens || 0)
      + Number(usage.completion_tokens || usage.output_tokens || usage.completionTokens || usage.outputTokens || 0)
    ),
  );
}

function cachedTokenCountFromUsage(usage) {
  const details = usage.prompt_tokens_details || usage.input_tokens_details || {};
  return Number(
    usage.cached_tokens
    || usage.cache_read_tokens
    || usage.input_cached_tokens
    || details.cached_tokens
    || details.cache_read_tokens
    || 0,
  );
}

function eventSpend(event) {
  return Number(event.spendUsd || event.spend_usd || 0);
}

function eventTokenVolume(event) {
  return tokenCountFromUsage(usageFromEvent(event));
}

function eventCachedTokens(event) {
  return cachedTokenCountFromUsage(usageFromEvent(event));
}

function completedEventsInWindow(events, start, end) {
  return events.filter((event) => {
    if (!isCompletedRequest(event) || !event.createdAt) {
      return false;
    }
    const timestamp = new Date(event.createdAt).getTime();
    return Number.isFinite(timestamp) && timestamp >= start.getTime() && timestamp < end.getTime();
  });
}

function completedUsageEvents(usageLogs) {
  return usageLogs.filter(isCompletedRequest);
}

function metricBuckets(events, start, end, reducer) {
  const bucketCount = 14;
  const bucketMs = (end.getTime() - start.getTime()) / bucketCount;
  const buckets = Array.from({ length: bucketCount }, (_, index) => ({
    start: start.getTime() + bucketMs * index,
    end: start.getTime() + bucketMs * (index + 1),
    events: [],
  }));

  for (const event of events) {
    const timestamp = new Date(event.createdAt).getTime();
    if (!Number.isFinite(timestamp) || timestamp < start.getTime() || timestamp >= end.getTime()) {
      continue;
    }

    const index = Math.min(bucketCount - 1, Math.floor((timestamp - start.getTime()) / bucketMs));
    buckets[index].events.push(event);
  }

  return buckets.map((bucket) => reducer(bucket.events));
}

function percentChange(current, previous) {
  if (!previous) {
    return current ? null : 0;
  }
  return (current - previous) / previous;
}

function formatPeriodDelta(value) {
  if (value === null) {
    return "";
  }
  return `${value >= 0 ? "↑" : "↓"} ${Math.abs(value * 100).toFixed(1)}%`;
}

function buildAnalyticsSummary(usageLogs) {
  const now = new Date();
  const currentStart = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
  const previousStart = new Date(now.getTime() - 14 * 24 * 60 * 60 * 1000);
  const completedEvents = completedUsageEvents(usageLogs);
  const currentEvents = completedEventsInWindow(completedEvents, currentStart, now);
  const previousEvents = completedEventsInWindow(completedEvents, previousStart, currentStart);
  const currentSpend = currentEvents.reduce((sum, event) => sum + eventSpend(event), 0);
  const previousSpend = previousEvents.reduce((sum, event) => sum + eventSpend(event), 0);
  const currentTokens = currentEvents.reduce((sum, event) => sum + eventTokenVolume(event), 0);
  const previousTokens = previousEvents.reduce((sum, event) => sum + eventTokenVolume(event), 0);
  const totalSpend = completedEvents.reduce((sum, event) => sum + eventSpend(event), 0);
  const totalTokens = completedEvents.reduce((sum, event) => sum + eventTokenVolume(event), 0);
  const totalCachedTokens = completedEvents.reduce((sum, event) => sum + eventCachedTokens(event), 0);
  const currentCachedTokens = currentEvents.reduce((sum, event) => sum + eventCachedTokens(event), 0);
  const previousCachedTokens = previousEvents.reduce((sum, event) => sum + eventCachedTokens(event), 0);
  const currentCacheRate = currentTokens ? currentCachedTokens / currentTokens : 0;
  const previousCacheRate = previousTokens ? previousCachedTokens / previousTokens : 0;
  const cacheHitRate = totalTokens ? totalCachedTokens / totalTokens : 0;
  const seriesStart = previousStart;

  return [
    {
      label: "Total spend",
      value: asCurrency(totalSpend),
      delta: percentChange(currentSpend, previousSpend),
      tone: "cost",
      series: metricBuckets(completedEvents, seriesStart, now, (events) => events.reduce((sum, event) => sum + eventSpend(event), 0)),
    },
    {
      label: "Requests",
      value: asCompactNumber(completedEvents.length),
      delta: percentChange(currentEvents.length, previousEvents.length),
      tone: "cost",
      series: metricBuckets(completedEvents, seriesStart, now, (events) => events.length),
    },
    {
      label: "Token volume",
      value: asCompactNumber(totalTokens),
      delta: percentChange(currentTokens, previousTokens),
      tone: "cost",
      series: metricBuckets(completedEvents, seriesStart, now, (events) => events.reduce((sum, event) => sum + eventTokenVolume(event), 0)),
    },
    {
      label: "Cache hit rate",
      value: `${(cacheHitRate * 100).toFixed(1)}%`,
      delta: percentChange(currentCacheRate, previousCacheRate),
      tone: "good",
      series: metricBuckets(completedEvents, seriesStart, now, (events) => {
        const tokens = events.reduce((sum, event) => sum + eventTokenVolume(event), 0);
        const cachedTokens = events.reduce((sum, event) => sum + eventCachedTokens(event), 0);
        return tokens ? cachedTokens / tokens : 0;
      }),
    },
  ];
}

function isLocalhostUrl(value) {
  return typeof value === "string" && value.toLowerCase().includes("localhost");
}

function startOfHour(date) {
  const nextDate = new Date(date);
  nextDate.setMinutes(0, 0, 0);
  return nextDate;
}

function requestRateLabel(date) {
  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    hour12: false,
  }).format(date);
}

function requestRateTooltipLabel(date) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function buildRequestRateTrend(usageLogs, now = new Date()) {
  const hourMs = 60 * 60 * 1000;
  const end = startOfHour(now);
  const start = new Date(end.getTime() - 47 * hourMs);
  const buckets = Array.from({ length: 48 }, (_, index) => {
    const bucketStart = new Date(start.getTime() + index * hourMs);
    return {
      hourIndex: index,
      label: requestRateLabel(bucketStart),
      tooltipLabel: requestRateTooltipLabel(bucketStart),
      value: 0,
      count: 0,
    };
  });

  for (const event of usageLogs) {
    if (!isCompletedRequest(event) || !event.createdAt) {
      continue;
    }

    const timestamp = new Date(event.createdAt).getTime();
    if (!Number.isFinite(timestamp) || timestamp < start.getTime() || timestamp > now.getTime()) {
      continue;
    }

    const bucketIndex = Math.floor((timestamp - start.getTime()) / hourMs);
    if (buckets[bucketIndex]) {
      buckets[bucketIndex].count += 1;
    }
  }

  return buckets.map((bucket) => ({
    ...bucket,
    requests: bucket.count / 60,
  }));
}

function labLabel(event) {
  return event.modelLab || event.metadata?.modelLab || event.modelCompany || event.triggeredLabs?.[0] || event.triggeredProviders?.[0] || event.provider || "unknown";
}

function modelLabFromId(modelId) {
  if (!modelId) {
    return "unknown";
  }
  const owner = String(modelId).toLowerCase().split("/", 1)[0].replace(/^~/, "");
  const labels = {
    anthropic: "Anthropic",
    deepseek: "DeepSeek",
    "deepseek-ai": "DeepSeek",
    google: "Google",
    meta: "Meta",
    "meta-llama": "Meta",
    mistral: "Mistral",
    mistralai: "Mistral",
    moonshot: "Moonshot",
    moonshotai: "Moonshot",
    nvidia: "NVIDIA",
    openai: "OpenAI",
    qwen: "Qwen",
    qwenlm: "Qwen",
    "x-ai": "xAI",
    "z-ai": "Z.ai",
  };
  return labels[owner] || owner || "unknown";
}

function preciseModelName(event) {
  const attempts = Array.isArray(event.metadata?.provider_attempts) ? event.metadata.provider_attempts : [];
  const successfulAttempt = attempts.find((attempt) => (
    attempt?.success === true
    || attempt?.status === "success"
    || attempt?.status === "succeeded"
    || attempt?.status === "executed"
  ));
  const firstAttempt = attempts[0];

  return (
    event.modelId
    || event.model_id
    || event.metadata?.selected_model_id
    || event.metadata?.selected_model
    || event.metadata?.model_id
    || event.metadata?.model
    || successfulAttempt?.model_id
    || successfulAttempt?.modelId
    || successfulAttempt?.model
    || firstAttempt?.model_id
    || firstAttempt?.modelId
    || firstAttempt?.model
    || "-"
  );
}

function routeEventKey(event) {
  return event.id || `${event.createdAt}-${event.modelId}-${event.metadata?.operation || ""}`;
}

function biencoderRanking(event) {
  const metadata = event.metadata || {};
  const ranked = Array.isArray(metadata.biencoder_ranked_candidates)
    ? metadata.biencoder_ranked_candidates
    : [];

  return ranked
    .map((model, index) => ({ ...model, _rankingIndex: index }))
    .sort((a, b) => {
      const aRank = Number(a.biencoder_rank ?? a.rank ?? a._rankingIndex + 1);
      const bRank = Number(b.biencoder_rank ?? b.rank ?? b._rankingIndex + 1);
      return aRank - bRank;
    });
}

function budgetRanking(event) {
  const ranked = event.metadata?.budget_ranked_candidates || [];
  return Array.isArray(ranked) ? ranked : [];
}

function attemptedModelMap(event) {
  const attempts = Array.isArray(event.metadata?.provider_attempts) ? event.metadata.provider_attempts : [];
  return attempts.reduce((acc, attempt, index) => {
    const modelId = attempt?.model_id || attempt?.modelId || attempt?.model;
    if (modelId) {
      acc[modelId] = { ...attempt, attemptIndex: index + 1 };
    }
    return acc;
  }, {});
}

function hasRouteDebugMetadata(event) {
  const metadata = event.metadata || {};
  return Boolean(
    Array.isArray(metadata.biencoder_ranked_candidates)
    || Array.isArray(metadata.budget_ranked_candidates)
    || Array.isArray(metadata.provider_attempts)
    || metadata.router_latency_ms
    || metadata.select_latency_ms
  );
}

function scoreValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(4) : "-";
}

function debugNumber(value, digits = 4) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "-";
  }
  if (Math.abs(number) > 0 && Math.abs(number) < 0.0001) {
    return number.toExponential(3);
  }
  return number.toLocaleString("en", {
    maximumFractionDigits: digits,
  });
}

function debugCurrency(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "-";
  }
  if (Math.abs(number) > 0 && Math.abs(number) < 0.01) {
    return `$${number.toExponential(3)}`;
  }
  return `$${number.toFixed(4)}`;
}

function budgetResult(model) {
  const result = model?.budget_result || model?.budgetResult;
  return result && typeof result === "object" ? result : null;
}

function budgetComputationRows(model) {
  const result = budgetResult(model);
  if (!result) {
    return [];
  }

  const inputTokens = result.input_length_tokens;
  const outputTokens = result.output_length_prediction_tokens;
  const outputWeight = result.output_token_weight;
  const weightedTokens = result.weighted_tokens;
  const medianWeightedTokens = result.median_weighted_tokens;
  const sizeFactor = result.size_factor;
  const beta = result.request_size_beta;
  const alpha = result.request_difficulty_alpha;
  const difficulty = result.difficulty;
  const rawRequestWeight = result.unclipped_request_weight;
  const requestWeightMin = result.request_weight_min;
  const requestWeightMax = result.request_weight_max;
  const requestWeight = result.request_weight;
  const remainingBudget = result.remaining_budget_usd;
  const remainingWeight = result.remaining_weight;
  const budgetPerWeight = result.budget_per_weight;
  const requestBudget = result.request_budget_usd;
  const expectedPrice = result.expected_price_usd;
  const expectedBudgetDebit = result.expected_budget_debit_usd;
  const shadowPrice = result.budget_shadow_price;
  const budgetMalus = result.budget_malus;
  const promptCacheLoss = model.prompt_cache_loss_usd;
  const cacheableTokens = model.cacheable_input_tokens;
  const cachedDelta = model.cached_input_price_difference_per_million;

  return [
    {
      label: "weighted_tokens",
      value: debugNumber(weightedTokens),
      formula: `${debugNumber(inputTokens)} + ${debugNumber(outputWeight)} * ${debugNumber(outputTokens)}`,
    },
    {
      label: "size_factor",
      value: debugNumber(sizeFactor),
      formula: `${debugNumber(weightedTokens)} / ${debugNumber(medianWeightedTokens)}`,
    },
    {
      label: "raw r_w",
      value: debugNumber(rawRequestWeight),
      formula: `${debugNumber(sizeFactor)}^${debugNumber(beta)} * exp(${debugNumber(alpha)} * ${debugNumber(difficulty)})`,
    },
    {
      label: "r_w",
      value: debugNumber(requestWeight),
      formula: `clip(${debugNumber(rawRequestWeight)}, ${debugNumber(requestWeightMin)}, ${debugNumber(requestWeightMax)})`,
    },
    {
      label: "budget_per_weight",
      value: debugCurrency(budgetPerWeight),
      formula: `${debugCurrency(remainingBudget)} / ${debugNumber(remainingWeight)}`,
    },
    {
      label: "request_budget",
      value: debugCurrency(requestBudget),
      formula: `${debugNumber(requestWeight)} * ${debugCurrency(budgetPerWeight)}`,
    },
    {
      label: "expected_provider_price",
      value: debugCurrency(expectedPrice),
      formula: "input price * input tokens + output price * predicted output tokens",
    },
    {
      label: "expected_budget_debit",
      value: debugCurrency(expectedBudgetDebit),
      formula: result.spend_accounting_mode === "provider_spend"
        ? "expected provider price"
        : "routing fee rate * expected provider price",
    },
    {
      label: "budget_malus",
      value: debugNumber(budgetMalus, 8),
      formula: `${debugNumber(shadowPrice)} * ${debugCurrency(expectedBudgetDebit)}`,
    },
    {
      label: "prompt_cache_loss",
      value: debugCurrency(promptCacheLoss),
      formula: `${debugNumber(cacheableTokens)} * ${debugCurrency(cachedDelta)} / 1M`,
    },
  ];
}

function biencoderRank(model, index) {
  const rank = Number(model.biencoder_rank ?? model.rank);
  return Number.isFinite(rank) && rank > 0 ? rank : index + 1;
}

function routerLatency(event) {
  return (
    event.metadata?.router_latency_ms
    ?? event.metadata?.route_latency_ms
    ?? event.metadata?.select_latency_ms
    ?? event.routerLatencyMs
    ?? null
  );
}

function executionLatency(event) {
  return event.metadata?.latency_ms ?? event.metadata?.latency ?? null;
}

function eventLabel(event) {
  if (event.metadata?.kind === "routing_operation") {
    return event.metadata?.operation || "Routing operation";
  }

  if (event.metadata?.status === "started") {
    return "Started";
  }

  return event.success ? "Completed" : "Failed";
}

async function apiErrorMessage(response, fallback) {
  const payload = await response.json().catch(() => ({}));
  return payload.error || payload.message || fallback;
}

function accountMenuLabel(session) {
  const email = session?.user?.email || "PromptRail";
  const localPart = email.split("@")[0] || email;
  return localPart || "PromptRail";
}

function DashboardMenuIcon({ name }) {
  const commonProps = {
    className: "dashboard-page-menu-svg",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round",
    "aria-hidden": "true",
  };

  if (name === "analytics") {
    return (
      <svg {...commonProps}>
        <path d="M4 19V5" />
        <path d="M4 19h16" />
        <path d="m7 15 3-4 3 2 4-7" />
      </svg>
    );
  }

  if (name === "credit") {
    return (
      <svg {...commonProps}>
        <rect x="3" y="6" width="18" height="12" rx="2" />
        <path d="M3 10h18" />
        <path d="M7 15h4" />
      </svg>
    );
  }

  if (name === "apiKeys") {
    return (
      <svg {...commonProps}>
        <circle cx="7.5" cy="14.5" r="3.5" />
        <path d="M10 12 21 1" />
        <path d="m16 6 2 2" />
        <path d="m13 9 2 2" />
      </svg>
    );
  }

  if (name === "settings") {
    return (
      <svg {...commonProps}>
        <path d="M12 15.5A3.5 3.5 0 1 0 12 8a3.5 3.5 0 0 0 0 7.5Z" />
        <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6V20a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-.6 1.7 1.7 0 0 0-1.88.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1H4a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 .6-1 1.7 1.7 0 0 0-.34-1.88l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6V4a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 .6 1.7 1.7 0 0 0 1.88-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9c.15.37.36.7.6 1h.1a2 2 0 1 1 0 4H20a1.7 1.7 0 0 0-.6 1Z" />
      </svg>
    );
  }

  if (name === "setup") {
    return (
      <svg {...commonProps}>
        <path d="m14.7 6.3 3 3" />
        <path d="M4 20l5.5-1.5L19 9l-4-4-9.5 9.5L4 20Z" />
      </svg>
    );
  }

  return (
    <svg {...commonProps}>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </svg>
  );
}

function DashboardNav({ activePage, session }) {
  const label = accountMenuLabel(session);
  const email = session?.user?.email || "";

  return (
    <details className="dashboard-page-menu">
      <summary className="dashboard-page-menu-trigger">
        <span className="dashboard-page-menu-mark">
          <DashboardMenuIcon name="account" />
        </span>
        <span>{label}</span>
      </summary>
      <nav className="dashboard-page-menu-panel" aria-label="Dashboard pages">
        {email ? <span className="dashboard-page-menu-email">{email}</span> : null}
        {Object.entries(dashboardPages).map(([page, config]) => (
          <a
            className={`dashboard-page-menu-link${activePage === page ? " dashboard-page-menu-link-active" : ""}`}
            href={config.href}
            key={page}
          >
            <span className="dashboard-page-menu-icon">
              <DashboardMenuIcon name={page} />
            </span>
            <span>{config.label}</span>
          </a>
        ))}
      </nav>
    </details>
  );
}

function Sparkline({ series = [], tone = "cost" }) {
  const stroke = tone === "good" ? "#4fb985" : "#ef5350";
  const values = series.length ? series.map((value) => Number(value || 0)) : [0, 0];
  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min;
  const width = 180;
  const height = 56;
  const points = values.map((value, index) => {
    const x = values.length === 1 ? width / 2 : 4 + (index / (values.length - 1)) * (width - 8);
    const normalized = range ? (value - min) / range : 0;
    const y = 44 - normalized * 32;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  return (
    <svg className="dashboard-kpi-sparkline" viewBox="0 0 180 56" aria-hidden="true">
      <polyline points={points} fill="none" stroke={stroke} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function AnalyticsMetricCard({ metric }) {
  const delta = formatPeriodDelta(metric.delta);
  const isPositive = metric.delta !== null && Number(metric.delta || 0) >= 0;
  const deltaClass = metric.tone === "good"
    ? isPositive ? "dashboard-kpi-delta-good" : "dashboard-kpi-delta-bad"
    : isPositive ? "dashboard-kpi-delta-bad" : "dashboard-kpi-delta-good";

  return (
    <Card className="dashboard-kpi-card">
      <CardHeader>
        <div>
          <CardDescription>{metric.label}</CardDescription>
          <CardTitle>{metric.value}</CardTitle>
          {delta ? <span className={`dashboard-kpi-delta ${deltaClass}`}>{delta}</span> : null}
        </div>
        <div className="dashboard-kpi-trend">
          <Sparkline series={metric.series} tone={metric.tone} />
        </div>
      </CardHeader>
    </Card>
  );
}

function PaymentStatus({ value }) {
  const label = String(value || "unknown").toLowerCase();
  const variant = label === "paid" || label === "succeeded" ? "default" : "secondary";
  return <Badge variant={variant}>{label}</Badge>;
}

export default function ProductDashboard({ activePage = "analytics" }) {
  const router = useRouter();
  const { data: session, isPending } = authClient.useSession();
  const [setup, setSetup] = useState(null);
  const [budgetData, setBudgetData] = useState(null);
  const [usageLogs, setUsageLogs] = useState([]);
  const [apiKeys, setApiKeys] = useState([]);
  const [isDebugDashboard, setIsDebugDashboard] = useState(false);
  const [isLoadingData, setIsLoadingData] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [checkoutAmountUsd, setCheckoutAmountUsd] = useState("25");
  const [checkoutError, setCheckoutError] = useState("");
  const [creditNotice, setCreditNotice] = useState("");
  const [isStartingCheckout, setIsStartingCheckout] = useState(false);
  const [autoTopUpEnabled, setAutoTopUpEnabled] = useState(false);
  const [autoTopUpThresholdUsd, setAutoTopUpThresholdUsd] = useState("5");
  const [autoTopUpAmountUsd, setAutoTopUpAmountUsd] = useState("25");
  const [autoTopUpError, setAutoTopUpError] = useState("");
  const [autoTopUpNotice, setAutoTopUpNotice] = useState("");
  const [isSavingAutoTopUp, setIsSavingAutoTopUp] = useState(false);
  const [routeDistributionMode, setRouteDistributionMode] = useState("route");
  const [selectedRouteEventKey, setSelectedRouteEventKey] = useState(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [settingsError, setSettingsError] = useState("");
  const [settingsNotice, setSettingsNotice] = useState("");
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [keyName, setKeyName] = useState("Production key");
  const [createdKey, setCreatedKey] = useState("");
  const [keyError, setKeyError] = useState("");
  const [keyNotice, setKeyNotice] = useState("");
  const [isCreatingKey, setIsCreatingKey] = useState(false);
  const [renameDrafts, setRenameDrafts] = useState({});
  const [renamingKeyId, setRenamingKeyId] = useState("");
  const [revokingKeyId, setRevokingKeyId] = useState("");
  const [setupRuntime, setSetupRuntime] = useState("hermes");
  const [setupInferenceMode, setSetupInferenceMode] = useState("user_managed");
  const [setupPlanType, setSetupPlanType] = useState("monthly");
  const [setupBudget, setSetupBudget] = useState("500");
  const [setupRouteId, setSetupRouteId] = useState("route_workspace");
  const [setupRawApiKey, setSetupRawApiKey] = useState("");
  const [setupInstruction, setSetupInstruction] = useState("");
  const [setupCopyNotice, setSetupCopyNotice] = useState("");
  const [setupError, setSetupError] = useState("");
  const [isCreatingSetupKey, setIsCreatingSetupKey] = useState(false);
  const [dashboardTheme, setDashboardTheme] = useState("white");
  const pageConfig = dashboardPages[activePage] || dashboardPages.analytics;
  const currentDashboardPath = dashboardPath(activePage);
  const dashboardPageClassName = `product-dashboard-page${dashboardTheme === "white" ? " product-dashboard-page-white" : ""}`;

  useEffect(() => {
    const savedTheme = window.localStorage.getItem(DASHBOARD_THEME_STORAGE_KEY);
    if (savedTheme === "black" || savedTheme === "white") {
      setDashboardTheme(savedTheme);
    }
  }, []);

  useEffect(() => {
    setIsDebugDashboard(
      isLocalhostUrl(process.env.NEXT_PUBLIC_APP_URL)
      || isLocalhostUrl(window.location.href),
    );
  }, []);

  useEffect(() => {
    if (isPending) {
      return;
    }

    if (!session?.user) {
      router.replace(`/login?next=${encodeURIComponent(currentDashboardPath)}`);
      return;
    }

    const savedSetup = readJson("lerouter-setup");
    setSetup(savedSetup);
    if (savedSetup) {
      setSetupRuntime(savedSetup.runtime || "hermes");
      setSetupInferenceMode(savedSetup.inferenceMode || "user_managed");
      setSetupPlanType(savedSetup.planType || "monthly");
      setSetupBudget(String(savedSetup.budget || "500"));
      setSetupRouteId(savedSetup.routeId || makeRouteId(savedSetup.workspaceName || "workspace"));
      setSetupRawApiKey(savedSetup.apiKey || "");
    }

    const creditsStatus = new URLSearchParams(window.location.search).get("credits");
    if (creditsStatus === "success") {
      setCreditNotice("Payment received. Your credits will appear after Stripe confirms the webhook.");
    }
    if (creditsStatus === "cancelled") {
      setCreditNotice("Checkout cancelled. No credits were added.");
    }
  }, [currentDashboardPath, isPending, router, session?.user]);

  useEffect(() => {
    if (!session?.user) {
      return;
    }

    let isMounted = true;
    const requirements = dashboardDataRequirements(activePage);

    async function loadDashboardData({ silent = false } = {}) {
      if (!silent) {
        setIsLoadingData(true);
      }
      setLoadError("");

      try {
        const [budgetResponse, usageResponse, apiKeysResponse] = await Promise.all([
          requirements.budget ? fetch("/api/user-budget", { cache: "no-store" }) : null,
          requirements.usage ? fetch(`/api/usage-log?limit=${ANALYTICS_USAGE_LOG_LIMIT}`, { cache: "no-store" }) : null,
          requirements.apiKeys ? fetch("/api/api-keys", { cache: "no-store" }) : null,
        ]);

        if ([budgetResponse, usageResponse, apiKeysResponse].some((response) => response?.status === 401)) {
          router.replace(`/login?next=${encodeURIComponent(currentDashboardPath)}`);
          return;
        }

        if (budgetResponse && !budgetResponse.ok) {
          throw new Error(await apiErrorMessage(budgetResponse, "Budget data unavailable."));
        }

        if (usageResponse && !usageResponse.ok) {
          throw new Error(await apiErrorMessage(usageResponse, "Usage data unavailable."));
        }

        if (apiKeysResponse && !apiKeysResponse.ok) {
          throw new Error(await apiErrorMessage(apiKeysResponse, "API key data unavailable."));
        }

        const [nextBudgetData, nextUsageData, nextApiKeysData] = await Promise.all([
          budgetResponse ? budgetResponse.json() : null,
          usageResponse ? usageResponse.json() : null,
          apiKeysResponse ? apiKeysResponse.json() : null,
        ]);

        if (isMounted) {
          if (nextBudgetData) {
            setBudgetData(nextBudgetData);
          }
          if (nextUsageData) {
            setUsageLogs(nextUsageData.logs?.length ? nextUsageData.logs : nextUsageData.mongoLogs || []);
          }
          if (nextApiKeysData) {
            setApiKeys(nextApiKeysData.keys || []);
          }
        }
      } catch (error) {
        if (isMounted) {
          setLoadError(error.message || "Dashboard data unavailable.");
        }
      } finally {
        if (isMounted) {
          setIsLoadingData(false);
        }
      }
    }

    loadDashboardData();
    const interval = requirements.pollMs
      ? window.setInterval(() => {
        loadDashboardData({ silent: true });
      }, requirements.pollMs)
      : null;

    return () => {
      isMounted = false;
      if (interval) {
        window.clearInterval(interval);
      }
    };
  }, [activePage, currentDashboardPath, router, session?.user]);

  const summary = useMemo(() => {
    const user = budgetData?.user || {};
    const mongoUser = budgetData?.mongoUser || {};
    const billing = budgetData?.billing || {};
    const localRequests = Number(user.totalRequests || 0);
    const mongoRequests = Number(mongoUser.totalRequests || 0);
    const statsSource = localRequests > 0 ? user : mongoRequests > 0 ? mongoUser : user;
    const storedBudget = Number(user.budgetUsd ?? mongoUser.budgetUsd ?? setup?.budget ?? 0);
    const remaining = Number(user.budgetRemainingUsd ?? mongoUser.budgetRemainingUsd ?? 0);
    const spend = Number(statsSource.totalSpendUsd ?? user.totalSpendUsd ?? mongoUser.totalSpendUsd ?? 0);
    const paidCredits = Number(billing.totalCreditsUsd || 0);
    const budget = paidCredits > 0 ? paidCredits : Math.max(storedBudget, spend + remaining);
    const totalRequests = Number(statsSource.totalRequests ?? 0);
    const successRate = Number(statsSource.successRate ?? 0);
    const labSegments = activePage === "analytics" ? parseLabCompletions(user, mongoUser, usageLogs) : [];

    return {
      budget,
      remaining,
      spend,
      paidCredits,
      pendingCredits: Number(billing.pendingCreditsUsd || 0),
      totalRequests,
      successRate,
      labSegments,
      spendPercent: budget ? Math.min(100, Math.round((spend / budget) * 100)) : 0,
    };
  }, [activePage, budgetData, setup?.budget, usageLogs]);

  const activeApiKey = apiKeys.find((key) => !key.revokedAt) || apiKeys[0] || setup?.apiKeyRecord || null;
  const setupAgentName = runtimeLabel(setupRuntime);
  const setupApiBaseUrl = (process.env.NEXT_PUBLIC_LEROUTER_API_URL || "https://promptrail--lerouter-api-fastapi-app.modal.run")
    .replace(/\/+$/g, "");

  useEffect(() => {
    if (activePage !== "setup") {
      return undefined;
    }

    let isMounted = true;
    setSetupInstruction("");

    async function loadSetupInstruction() {
      const { routerManagedInstructions, userManagedInstructions } = await import("./OnboardingFlow.js");
      const nextInstruction = setupInferenceMode === "user_managed"
        ? userManagedInstructions({
          agentName: setupAgentName,
          apiBaseUrl: setupApiBaseUrl,
          apiKey: setupRawApiKey,
          budget: setupBudget,
          planType: setupPlanType,
          routeId: setupRouteId,
          runtime: setupRuntime,
        })
        : routerManagedInstructions({
          agentName: setupAgentName,
          apiBaseUrl: setupApiBaseUrl,
          apiKey: setupRawApiKey,
          budget: setupBudget,
          planType: setupPlanType,
          routeId: setupRouteId,
          runtime: setupRuntime,
        });

      if (isMounted) {
        setSetupInstruction(nextInstruction);
      }
    }

    loadSetupInstruction().catch((error) => {
      if (isMounted) {
        setSetupError(error.message || "Setup prompt could not be loaded.");
      }
    });

    return () => {
      isMounted = false;
    };
  }, [activePage, setupAgentName, setupApiBaseUrl, setupBudget, setupInferenceMode, setupPlanType, setupRawApiKey, setupRouteId, setupRuntime]);

  const visibleRouteEvents = activePage === "analytics" ? usageLogs.filter(isVisibleRouteEvent) : [];
  const routeEventLimit = isDebugDashboard ? LOCAL_DEBUG_ROUTE_EVENT_LIMIT : ROUTE_EVENT_LIMIT;
  const recentUsageLogs = visibleRouteEvents.slice(0, routeEventLimit);
  const selectedRouteEvent = recentUsageLogs.find((event) => routeEventKey(event) === selectedRouteEventKey) || null;
  const selectedRanking = selectedRouteEvent ? biencoderRanking(selectedRouteEvent) : [];
  const selectedBudgetRanking = selectedRouteEvent ? budgetRanking(selectedRouteEvent) : [];
  const selectedAttempts = selectedRouteEvent ? attemptedModelMap(selectedRouteEvent) : {};
  const routeDistribution = useMemo(
    () => (activePage === "analytics" ? buildRouteDistribution(visibleRouteEvents, routeDistributionMode) : { routes: [], total: 0 }),
    [activePage, routeDistributionMode, visibleRouteEvents],
  );
  const requestTrend = useMemo(
    () => (activePage === "analytics" ? buildRequestRateTrend(usageLogs) : []),
    [activePage, usageLogs],
  );
  const analyticsMetrics = useMemo(
    () => (activePage === "analytics" ? buildAnalyticsSummary(usageLogs) : []),
    [activePage, usageLogs],
  );
  const recentPayments = budgetData?.billing?.recentPayments || [];
  const autoTopUpUser = budgetData?.user || {};
  const hasSavedPaymentMethod = Boolean(autoTopUpUser.stripePaymentMethodId);

  useEffect(() => {
    if (!budgetData?.user) {
      return;
    }

    setAutoTopUpEnabled(Boolean(budgetData.user.autoTopUpEnabled));
    setAutoTopUpThresholdUsd(String(Number(budgetData.user.autoTopUpThresholdUsd || 5)));
    setAutoTopUpAmountUsd(String(Number(budgetData.user.autoTopUpAmountUsd || 25)));
  }, [budgetData?.user]);

  useEffect(() => {
    setRenameDrafts((currentDrafts) => {
      const nextDrafts = {};
      for (const key of apiKeys) {
        nextDrafts[key.id] = currentDrafts[key.id] ?? key.name ?? "";
      }
      return nextDrafts;
    });
  }, [apiKeys]);

  useEffect(() => {
    if (!selectedRouteEvent) {
      return undefined;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function closeOnEscape(event) {
      if (event.key === "Escape") {
        setSelectedRouteEventKey(null);
      }
    }

    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [selectedRouteEvent]);

  async function logout() {
    await authClient.signOut();
    router.push("/login");
    router.refresh();
  }

  function changeDashboardTheme(nextTheme) {
    setDashboardTheme(nextTheme);
    window.localStorage.setItem(DASHBOARD_THEME_STORAGE_KEY, nextTheme);
  }

  async function startCheckout(event) {
    event.preventDefault();
    setCheckoutError("");

    const amountUsd = Number(checkoutAmountUsd);
    if (!Number.isFinite(amountUsd) || amountUsd < 5 || amountUsd > 1000) {
      setCheckoutError("Enter an amount between $5 and $1000.");
      return;
    }

    setIsStartingCheckout(true);

    try {
      const response = await fetch("/api/billing/checkout", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ amountUsd }),
      });
      const payload = await response.json();

      if (!response.ok) {
        throw new Error(payload.error || "Checkout could not be started.");
      }

      window.location.assign(payload.url);
    } catch (error) {
      setCheckoutError(error.message || "Checkout could not be started.");
      setIsStartingCheckout(false);
    }
  }

  async function saveAutoTopUp(event) {
    event.preventDefault();
    setAutoTopUpError("");
    setAutoTopUpNotice("");

    const thresholdUsd = Number(autoTopUpThresholdUsd);
    const amountUsd = Number(autoTopUpAmountUsd);

    if (!Number.isFinite(thresholdUsd) || thresholdUsd < 1 || thresholdUsd > 1000) {
      setAutoTopUpError("Enter a threshold between $1 and $1000.");
      return;
    }

    if (!Number.isFinite(amountUsd) || amountUsd < 5 || amountUsd > 1000) {
      setAutoTopUpError("Enter a top-up amount between $5 and $1000.");
      return;
    }

    if (amountUsd <= thresholdUsd) {
      setAutoTopUpError("The top-up amount must be greater than the threshold.");
      return;
    }

    setIsSavingAutoTopUp(true);

    try {
      const response = await fetch("/api/billing/auto-top-up", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          enabled: autoTopUpEnabled,
          thresholdUsd,
          amountUsd,
        }),
      });
      const payload = await response.json();

      if (!response.ok) {
        throw new Error(payload.error || "Auto top-up settings could not be saved.");
      }

      setBudgetData((current) => ({
        ...(current || {}),
        user: payload.user,
      }));
      setAutoTopUpNotice("Auto top-up settings saved.");
    } catch (error) {
      setAutoTopUpError(error.message || "Auto top-up settings could not be saved.");
    } finally {
      setIsSavingAutoTopUp(false);
    }
  }

  async function createDashboardApiKey(event) {
    event.preventDefault();
    setKeyError("");
    setKeyNotice("");
    setCreatedKey("");

    const name = keyName.trim();
    if (!name) {
      setKeyError("Key name is required.");
      return;
    }

    setIsCreatingKey(true);
    try {
      const response = await fetch("/api/api-keys", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          name,
          routeId: budgetData?.user?.routeId || session?.user?.routeId || "default",
        }),
      });
      const payload = await response.json();

      if (!response.ok) {
        throw new Error(payload.error || "API key creation failed.");
      }

      setApiKeys((currentKeys) => [payload.apiKey, ...currentKeys]);
      setCreatedKey(payload.key);
      setKeyNotice("API key created. Copy it now, it will not be shown again.");
    } catch (error) {
      setKeyError(error.message || "API key creation failed.");
    } finally {
      setIsCreatingKey(false);
    }
  }

  async function renameDashboardApiKey(keyId) {
    setKeyError("");
    setKeyNotice("");

    const name = String(renameDrafts[keyId] || "").trim();
    if (!name) {
      setKeyError("Key name is required.");
      return;
    }

    setRenamingKeyId(keyId);
    try {
      const response = await fetch("/api/api-keys", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ id: keyId, name }),
      });
      const payload = await response.json();

      if (!response.ok) {
        throw new Error(payload.error || "API key rename failed.");
      }

      setApiKeys((currentKeys) => currentKeys.map((key) => (
        key.id === keyId ? payload.apiKey : key
      )));
      setKeyNotice("API key renamed.");
    } catch (error) {
      setKeyError(error.message || "API key rename failed.");
    } finally {
      setRenamingKeyId("");
    }
  }

  async function revokeDashboardApiKey(keyId) {
    setKeyError("");
    setKeyNotice("");
    setRevokingKeyId(keyId);

    try {
      const response = await fetch(`/api/api-keys?id=${encodeURIComponent(keyId)}`, {
        method: "DELETE",
      });
      const payload = await response.json();

      if (!response.ok) {
        throw new Error(payload.error || "API key revocation failed.");
      }

      if (!payload.revoked) {
        throw new Error("API key was not revoked.");
      }

      const revokedAt = new Date().toISOString();
      setApiKeys((currentKeys) => currentKeys.map((key) => (
        key.id === keyId ? { ...key, revokedAt, updatedAt: revokedAt } : key
      )));
      setKeyNotice("API key revoked.");
    } catch (error) {
      setKeyError(error.message || "API key revocation failed.");
    } finally {
      setRevokingKeyId("");
    }
  }

  async function createSetupApiKey() {
    setSetupError("");
    setSetupCopyNotice("");
    setIsCreatingSetupKey(true);

    try {
      const response = await fetch("/api/api-keys", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          name: `${setupAgentName} setup key`,
          routeId: setupRouteId || "default",
        }),
      });
      const payload = await response.json();

      if (!response.ok) {
        throw new Error(payload.error || "API key creation failed.");
      }

      setApiKeys((currentKeys) => [payload.apiKey, ...currentKeys]);
      setSetupRawApiKey(payload.key);
      const nextSetup = {
        ...(setup || {}),
        budget: setupBudget,
        inferenceMode: setupInferenceMode,
        planType: setupPlanType,
        runtime: setupRuntime,
        routeId: setupRouteId,
        apiKey: payload.key,
        apiKeyRecord: payload.apiKey,
      };
      setSetup(nextSetup);
      window.localStorage.setItem("lerouter-setup", JSON.stringify(nextSetup));
      setSetupCopyNotice("New setup key created. Copy the prompt now; the raw key will not be shown again.");
    } catch (error) {
      setSetupError(error.message || "API key creation failed.");
    } finally {
      setIsCreatingSetupKey(false);
    }
  }

  function copySetupInstruction() {
    if (!setupInstruction) {
      return;
    }
    navigator.clipboard?.writeText(setupInstruction).catch(() => undefined);
    setSetupCopyNotice("Setup prompt copied.");
  }

  async function changePassword(event) {
    event.preventDefault();
    setSettingsError("");
    setSettingsNotice("");

    if (!currentPassword || !newPassword) {
      setSettingsError("Current password and new password are required.");
      return;
    }

    if (newPassword !== confirmPassword) {
      setSettingsError("New passwords do not match.");
      return;
    }

    setIsChangingPassword(true);
    try {
      const response = await authClient.changePassword({
        currentPassword,
        newPassword,
        revokeOtherSessions: true,
      });

      if (response?.error) {
        throw new Error(response.error.message || "Password could not be changed.");
      }

      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setSettingsNotice("Password updated. Other sessions were signed out.");
    } catch (error) {
      setSettingsError(error.message || "Password could not be changed.");
    } finally {
      setIsChangingPassword(false);
    }
  }

  if (isPending || isLoadingData) {
    return (
      <main className={dashboardPageClassName}>
        <section className="product-dashboard-shell">
          <Card className="dashboard-loading-card">
            <CardHeader>
              <Badge variant="secondary">PromptRail</Badge>
              <CardTitle>Loading dashboard...</CardTitle>
              <CardDescription>Fetching workspace, usage, and routing data.</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="dashboard-loading-lines" aria-hidden="true">
                <span />
                <span />
                <span />
              </div>
            </CardContent>
          </Card>
        </section>
      </main>
    );
  }

  return (
    <main className={dashboardPageClassName}>
      <section className="product-dashboard-shell">
        <header className="product-dashboard-header">
          <div>
            <h1>{pageConfig.title}</h1>
          </div>
          <DashboardNav activePage={activePage} session={session} />
        </header>

        {loadError ? (
          <Card className="dashboard-callout dashboard-callout-error">
            <CardContent>
              <strong>Dashboard data failed to load.</strong>
              <span>{loadError}</span>
            </CardContent>
          </Card>
        ) : null}

        {creditNotice ? (
          <Card className="dashboard-callout">
            <CardContent>
              <div>
                <strong>Credits update</strong>
                <span>{creditNotice}</span>
              </div>
            </CardContent>
          </Card>
        ) : null}

        {!activeApiKey && !setup?.apiKey && activePage === "analytics" ? (
          <Card className="dashboard-callout">
            <CardContent>
              <div>
                <strong>No routed key yet.</strong>
                <span>Create an API key before sending real traffic.</span>
              </div>
              <Button asChild>
                <a href="/dashboard/api-keys">Create API key</a>
              </Button>
            </CardContent>
          </Card>
        ) : null}

        {activePage === "analytics" ? (
        <>
        <div className="dashboard-kpi-grid">
          {analyticsMetrics.map((metric) => (
            <AnalyticsMetricCard key={metric.label} metric={metric} />
          ))}
        </div>

        <Card className="dashboard-request-trend-card">
          <CardHeader>
            <CardTitle>Request</CardTitle>
          </CardHeader>
          <CardContent>
            <RequestTrendChart data={requestTrend} />
          </CardContent>
        </Card>

        <div className="dashboard-main-grid">
          <Card className="provider-chart-panel" aria-label="Model lab completion split">
            <CardHeader>
              <CardTitle>{summary.labSegments.length ? "Completion split" : "No completions yet"}</CardTitle>
            </CardHeader>
            <CardContent>
              <div
                className="provider-donut"
                style={{ background: buildConicGradient(summary.labSegments) }}
                aria-hidden="true"
              />
              <div className="provider-legend">
                {summary.labSegments.length ? summary.labSegments.map((segment) => (
                  <div className="provider-legend-row" key={segment.lab}>
                    <span className="provider-swatch" style={{ background: segment.color }} />
                    <strong>{segment.lab}</strong>
                    <span>{Math.round(segment.percent * 100)}%</span>
                  </div>
                )) : (
                  <span className="dashboard-empty-state">Send completions to populate this chart.</span>
                )}
              </div>
            </CardContent>
          </Card>

          <Card className="route-distribution-panel" aria-label="Route request distribution">
            <CardHeader>
              <div
                className="route-distribution-tabs"
                data-active-index={Math.max(0, routeDistributionModes.findIndex(([mode]) => mode === routeDistributionMode))}
                aria-label="Route distribution dimensions"
              >
                {routeDistributionModes.map(([mode, label]) => (
                  <button
                    className={routeDistributionMode === mode ? "route-distribution-tab-active" : ""}
                    key={mode}
                    type="button"
                    aria-pressed={routeDistributionMode === mode}
                    onClick={() => setRouteDistributionMode(mode)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="route-distribution-controls">
                <span>All ({routeDistribution.total})</span>
                <strong>Requests ↕</strong>
              </div>
            </CardHeader>
            <CardContent>
              {routeDistribution.routes.length ? (
                <div className="route-distribution-list">
                  {routeDistribution.routes.map((route) => {
                    const normalizedWidth = Math.min(1, route.width);
                    return (
                    <div
                      className="route-distribution-row"
                      key={route.route}
                      style={{
                        "--route-row-background": route.color,
                        "--route-row-background-opacity": Number((0.16 + normalizedWidth * 0.56).toFixed(3)),
                        "--route-bar-opacity": Number((0.08 + normalizedWidth * 0.18).toFixed(3)),
                      }}
                    >
                      <div
                        className="route-distribution-bar"
                        style={{
                          backgroundColor: route.color,
                          transform: `scaleX(${Math.max(0.08, normalizedWidth)})`,
                        }}
                        aria-hidden="true"
                      />
                      <span title={route.route}>{route.route}</span>
                      <strong>{route.count}</strong>
                    </div>
                    );
                  })}
                </div>
              ) : (
                <div className="route-distribution-empty">
                  <strong>No {routeDistributionMode} traffic yet</strong>
                  <span>Requests will appear here as soon as they run through Rail-1.</span>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <Card className="dashboard-table-card">
          <CardHeader>
            <div>
              <CardTitle>Route events</CardTitle>
            </div>
            <Badge variant="secondary">{recentUsageLogs.length} of {visibleRouteEvents.length} shown</Badge>
          </CardHeader>
          <CardContent>
            <Table aria-label="Recent route events">
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Request</TableHead>
                  <TableHead>Lab</TableHead>
                  {isDebugDashboard ? <TableHead>Model</TableHead> : null}
                  <TableHead>Route</TableHead>
                  <TableHead className="ui-table-cell-right">Cost</TableHead>
                  {isDebugDashboard ? <TableHead className="ui-table-cell-right">Router</TableHead> : null}
                  <TableHead className="ui-table-cell-right">Latency</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recentUsageLogs.length ? recentUsageLogs.map((event) => {
                  const key = routeEventKey(event);
                  const isSelected = selectedRouteEventKey === key;
                  const canInspectEvent = isDebugDashboard || hasRouteDebugMetadata(event);
                  return (
                  <TableRow
                    key={key}
                    className={canInspectEvent ? `dashboard-route-row-clickable${isSelected ? " dashboard-route-row-selected" : ""}` : ""}
                    onClick={canInspectEvent ? () => setSelectedRouteEventKey(isSelected ? null : key) : undefined}
                    tabIndex={canInspectEvent ? 0 : undefined}
                    onKeyDown={canInspectEvent ? (keyboardEvent) => {
                      if (keyboardEvent.key === "Enter" || keyboardEvent.key === " ") {
                        keyboardEvent.preventDefault();
                        setSelectedRouteEventKey(isSelected ? null : key);
                      }
                    } : undefined}
                  >
                    <TableCell>{formatTime(event.createdAt)}</TableCell>
                    <TableCell className="ui-table-cell-strong">
                      {eventLabel(event)}
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{labLabel(event)}</Badge>
                    </TableCell>
                    {isDebugDashboard ? (
                      <TableCell>
                        <code>{preciseModelName(event)}</code>
                      </TableCell>
                    ) : null}
                    <TableCell>{event.routeName || event.routeId}</TableCell>
                    <TableCell className="ui-table-cell-right">{asCurrency(event.spendUsd)}</TableCell>
                    {isDebugDashboard ? (
                      <TableCell className="ui-table-cell-right">{formatLatency(routerLatency(event))}</TableCell>
                    ) : null}
                    <TableCell className="ui-table-cell-right">
                      {formatLatency(executionLatency(event))}
                    </TableCell>
                  </TableRow>
                  );
                }) : (
                  <TableRow>
                    <TableCell>-</TableCell>
                    <TableCell className="ui-table-cell-strong">No usage yet</TableCell>
                    <TableCell>
                      <Badge variant="secondary">none</Badge>
                    </TableCell>
                    {isDebugDashboard ? <TableCell>-</TableCell> : null}
                    <TableCell>Send requests through a route</TableCell>
                    <TableCell className="ui-table-cell-right">$0.00</TableCell>
                    {isDebugDashboard ? <TableCell className="ui-table-cell-right">-</TableCell> : null}
                    <TableCell className="ui-table-cell-right">-</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
            {selectedRouteEvent ? (
              <div
                className="route-debug-modal-backdrop"
                role="presentation"
                onMouseDown={(event) => {
                  if (event.target === event.currentTarget) {
                    setSelectedRouteEventKey(null);
                  }
                }}
              >
              <div
                className="route-debug-detail"
                role="dialog"
                aria-modal="true"
                aria-labelledby="route-debug-title"
                onMouseDown={(event) => event.stopPropagation()}
              >
                <div className="route-debug-detail-head">
                  <div>
                    <span>Request detail</span>
                    <strong id="route-debug-title">{selectedRouteEvent.routeName || selectedRouteEvent.routeId || "unknown route"}</strong>
                  </div>
                  <Button
                    aria-label="Close request detail"
                    variant="ghost"
                    size="sm"
                    type="button"
                    onClick={() => setSelectedRouteEventKey(null)}
                  >
                    Close
                  </Button>
                </div>
                <div className="route-debug-summary">
                  <span>Selected <code>{preciseModelName(selectedRouteEvent)}</code></span>
                  <span>Router {formatLatency(routerLatency(selectedRouteEvent))}</span>
                  <span>Execution {formatLatency(executionLatency(selectedRouteEvent))}</span>
                  <span>{Object.keys(selectedAttempts).length} attempted</span>
                </div>
                <div className="route-debug-grid">
                  <div className="route-debug-panel">
                    <div className="route-debug-panel-title">
                      <span>Bi-encoder ranking</span>
                      <Badge variant="secondary">{selectedRanking.length} models</Badge>
                    </div>
                    <div className="route-debug-list">
                      {selectedRanking.length ? (
                        <>
                          <div className="route-debug-model-header route-debug-model-header-biencoder" aria-hidden="true">
                            <span>Rank</span>
                            <span>Model</span>
                            <span>Lab</span>
                            <span>Score</span>
                            <span>Prob.</span>
                            <span>Source</span>
                            <span>Attempt</span>
                          </div>
                          {selectedRanking.map((model, index) => {
                        const modelId = model.model_id || model.modelId || model.model || `model-${index}`;
                        const attempt = selectedAttempts[modelId];
                        const attemptStatus = attempt
                          ? `attempt ${attempt.attemptIndex}: ${attempt.ok ? "ok" : attempt.status || "failed"}`
                          : "not attempted";
                        return (
                          <div className="route-debug-model-row route-debug-model-row-biencoder" key={`${modelId}-${index}`}>
                            <span>#{biencoderRank(model, index)}</span>
                            <code title={modelId}>{modelId}</code>
                            <Badge variant="outline">{model.model_lab || modelLabFromId(modelId)}</Badge>
                            <strong className="route-debug-score">score {scoreValue(model.biencoder_score)}</strong>
                            <span>p {scoreValue(model.biencoder_probability)}</span>
                            <span>{model.biencoder_source || "-"}</span>
                            <span>{attemptStatus}</span>
                          </div>
                        );
                          })}
                        </>
                      ) : (
                        <div className="route-debug-empty">No bi-encoder scores stored for this request.</div>
                      )}
                    </div>
                  </div>
                  <div className="route-debug-panel">
                    <div className="route-debug-panel-title">
                      <span>Final routing order</span>
                      <Badge variant="secondary">{selectedBudgetRanking.length} models</Badge>
                    </div>
                    <div className="route-debug-list">
                      {selectedBudgetRanking.length ? (
                        <>
                          <div className="route-debug-model-header route-debug-model-header-compact" aria-hidden="true">
                            <span>Rank</span>
                            <span>Model</span>
                            <span>Final</span>
                            <span>Budget</span>
                            <span>Cache loss</span>
                            <span>Calc</span>
                          </div>
                          {selectedBudgetRanking.map((model, index) => {
                        const modelId = model.model_id || model.modelId || model.model || `budget-model-${index}`;
                        const computationRows = budgetComputationRows(model);
                        return (
                          <div className="route-debug-model-row route-debug-model-row-compact" key={`${modelId}-${index}`}>
                            <span>{index + 1}</span>
                            <code>{modelId}</code>
                            <span>final {scoreValue(model.final_score)}</span>
                            <span>malus {scoreValue(model.budget_malus)}</span>
                            <span>
                              cache loss {scoreValue(model.switch_cost_penalty)}
                            </span>
                            <details className="route-debug-budget-calc">
                              <summary>calc</summary>
                              {computationRows.length ? (
                                <div className="route-debug-budget-steps">
                                  {computationRows.map((row) => (
                                    <div className="route-debug-budget-step" key={row.label}>
                                      <span>{row.label}</span>
                                      <strong>{row.value}</strong>
                                      <code>{row.formula}</code>
                                    </div>
                                  ))}
                                </div>
                              ) : (
                                <div className="route-debug-budget-missing">
                                  No budget_result stored for this request.
                                </div>
                              )}
                            </details>
                          </div>
                        );
                          })}
                        </>
                      ) : (
                        <div className="route-debug-empty">No budget ranking stored for this request.</div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
              </div>
            ) : null}
          </CardContent>
        </Card>
        </>
        ) : null}

        {activePage === "credit" ? (
          <div className="dashboard-credit-layout">
            <Card className="dashboard-credit-balance-card">
              <CardHeader>
                <div>
                  <CardTitle>{asCurrency(summary.remaining)}</CardTitle>
                </div>
                <Badge variant="secondary">{asCurrency(summary.paidCredits)} paid</Badge>
              </CardHeader>
              <CardContent>
                <Progress value={summary.spendPercent} />
                <div className="dashboard-credit-balance-meta">
                  <span>{asCurrency(summary.spend)} used</span>
                  <span>{asCurrency(summary.budget)} total</span>
                </div>
                {summary.pendingCredits ? (
                  <p className="dashboard-credit-pending">
                    {asCurrency(summary.pendingCredits)} pending Stripe confirmation.
                  </p>
                ) : null}
              </CardContent>
            </Card>

            <Card className="dashboard-credit-card">
              <CardHeader>
                <div>
                  <CardTitle>Add credit</CardTitle>
                </div>
                <Badge variant="outline">Stripe</Badge>
              </CardHeader>
              <CardContent>
                <form className="dashboard-credit-form" onSubmit={startCheckout}>
                  <label>
                    Amount
                    <span className="dashboard-credit-input">
                      <span>$</span>
                      <input
                        type="number"
                        min="5"
                        max="1000"
                        step="1"
                        value={checkoutAmountUsd}
                        onChange={(event) => setCheckoutAmountUsd(event.target.value)}
                      />
                    </span>
                  </label>
                  <Button type="submit" disabled={isStartingCheckout}>
                    {isStartingCheckout ? "Opening..." : "Add credits"}
                  </Button>
                </form>
                {checkoutError ? <p className="dashboard-credit-error">{checkoutError}</p> : null}
              </CardContent>
            </Card>

            <Card className="dashboard-credit-card dashboard-auto-top-up-card">
              <CardHeader>
                <div>
                  <CardTitle>Auto top-up</CardTitle>
                  <CardDescription>Recharge credits automatically when balance runs low.</CardDescription>
                </div>
                <Badge variant={autoTopUpEnabled ? "default" : "outline"}>
                  {autoTopUpEnabled ? "On" : "Off"}
                </Badge>
              </CardHeader>
              <CardContent>
                <form className="dashboard-auto-top-up-form" onSubmit={saveAutoTopUp}>
                  <label className="dashboard-auto-top-up-toggle">
                    <input
                      type="checkbox"
                      checked={autoTopUpEnabled}
                      onChange={(event) => setAutoTopUpEnabled(event.target.checked)}
                    />
                    <span>Enable auto top-up</span>
                  </label>
                  <div className="dashboard-auto-top-up-grid">
                    <label>
                      Trigger below
                      <span className="dashboard-credit-input">
                        <span>$</span>
                        <input
                          type="number"
                          min="1"
                          max="1000"
                          step="1"
                          value={autoTopUpThresholdUsd}
                          onChange={(event) => setAutoTopUpThresholdUsd(event.target.value)}
                        />
                      </span>
                    </label>
                    <label>
                      Add
                      <span className="dashboard-credit-input">
                        <span>$</span>
                        <input
                          type="number"
                          min="5"
                          max="1000"
                          step="1"
                          value={autoTopUpAmountUsd}
                          onChange={(event) => setAutoTopUpAmountUsd(event.target.value)}
                        />
                      </span>
                    </label>
                  </div>
                  <Button type="submit" disabled={isSavingAutoTopUp}>
                    {isSavingAutoTopUp ? "Saving..." : "Save auto top-up"}
                  </Button>
                </form>
                {!hasSavedPaymentMethod ? (
                  <p className="dashboard-credit-pending">
                    Add credits once with Stripe to save a payment method before auto top-up can charge in production.
                  </p>
                ) : null}
                {autoTopUpUser.autoTopUpLastFailure ? (
                  <p className="dashboard-credit-error">{autoTopUpUser.autoTopUpLastFailure}</p>
                ) : null}
                {autoTopUpError ? <p className="dashboard-credit-error">{autoTopUpError}</p> : null}
                {autoTopUpNotice ? <p className="dashboard-credit-success">{autoTopUpNotice}</p> : null}
              </CardContent>
            </Card>

            <Card className="dashboard-payment-table-card">
              <CardHeader>
                <div>
                  <CardTitle>Recent payments</CardTitle>
                </div>
                <Badge variant="secondary">{recentPayments.length} shown</Badge>
              </CardHeader>
              <CardContent>
                <Table aria-label="Recent credit payments">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Time</TableHead>
                      <TableHead>Amount</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Payment</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {recentPayments.map((payment) => (
                      <TableRow key={payment.id || payment.stripePaymentIntentId || payment.createdAt}>
                        <TableCell>{formatTime(payment.createdAt || payment.updatedAt)}</TableCell>
                        <TableCell>{asCurrency(payment.amountUsd)}</TableCell>
                        <TableCell>
                          <PaymentStatus value={payment.paymentStatus || payment.status} />
                        </TableCell>
                        <TableCell>
                          <code>{payment.id || payment.stripePaymentIntentId || "-"}</code>
                        </TableCell>
                      </TableRow>
                    ))}
                    {!recentPayments.length ? (
                      <TableRow>
                        <TableCell colSpan={4}>No payments yet.</TableCell>
                      </TableRow>
                    ) : null}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </div>
        ) : null}

        {activePage === "apiKeys" ? (
          <div className="dashboard-api-key-layout">
            <Card className="dashboard-api-key-create-card">
              <CardHeader>
                <div>
                  <CardTitle>Create API key</CardTitle>
                </div>
                <Badge variant="secondary">{apiKeys.filter((key) => !key.revokedAt).length} active</Badge>
              </CardHeader>
              <CardContent>
                <form className="dashboard-api-key-form" onSubmit={createDashboardApiKey}>
                  <label className="dashboard-settings-field">
                    Key name
                    <input
                      type="text"
                      maxLength={80}
                      value={keyName}
                      onChange={(event) => setKeyName(event.target.value)}
                    />
                  </label>
                  <Button type="submit" disabled={isCreatingKey}>
                    {isCreatingKey ? "Creating..." : "Create key"}
                  </Button>
                </form>
                {createdKey ? (
                  <div className="dashboard-created-key">
                    <span>New API key</span>
                    <code>{createdKey}</code>
                  </div>
                ) : null}
                {keyError ? <p className="dashboard-settings-message dashboard-settings-message-error">{keyError}</p> : null}
                {keyNotice ? <p className="dashboard-settings-message dashboard-settings-message-success">{keyNotice}</p> : null}
              </CardContent>
            </Card>

            <Card className="dashboard-api-key-table-card">
              <CardHeader>
                <div>
                  <CardTitle>Manage keys</CardTitle>
                </div>
                <Badge variant="secondary">{apiKeys.length} total</Badge>
              </CardHeader>
              <CardContent>
                <Table aria-label="API keys">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Name</TableHead>
                      <TableHead>Key</TableHead>
                      <TableHead>Route</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Last used</TableHead>
                      <TableHead>Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {apiKeys.map((key) => (
                      <TableRow key={key.id}>
                        <TableCell>
                          <input
                            className="dashboard-api-key-name-input"
                            type="text"
                            maxLength={80}
                            value={renameDrafts[key.id] ?? key.name ?? ""}
                            onChange={(event) => setRenameDrafts((currentDrafts) => ({
                              ...currentDrafts,
                              [key.id]: event.target.value,
                            }))}
                            disabled={Boolean(key.revokedAt)}
                          />
                        </TableCell>
                        <TableCell>
                          <code>{key.displayKey}</code>
                        </TableCell>
                        <TableCell>{key.routeId || "default"}</TableCell>
                        <TableCell>
                          <Badge variant={key.revokedAt ? "secondary" : "default"}>
                            {key.revokedAt ? "revoked" : "active"}
                          </Badge>
                        </TableCell>
                        <TableCell>{formatTime(key.lastUsedAt)}</TableCell>
                        <TableCell>
                          <div className="dashboard-api-key-actions">
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled={Boolean(key.revokedAt) || renamingKeyId === key.id}
                              onClick={() => renameDashboardApiKey(key.id)}
                            >
                              {renamingKeyId === key.id ? "Saving..." : "Rename"}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled={Boolean(key.revokedAt) || revokingKeyId === key.id}
                              onClick={() => revokeDashboardApiKey(key.id)}
                            >
                              {revokingKeyId === key.id ? "Revoking..." : "Revoke"}
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                    {!apiKeys.length ? (
                      <TableRow>
                        <TableCell colSpan={6}>No API keys yet.</TableCell>
                      </TableRow>
                    ) : null}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </div>
        ) : null}

        {activePage === "setup" ? (
          <div className="dashboard-setup-layout">
            <Card className="dashboard-setup-controls-card">
              <CardHeader>
                <div>
                  <CardTitle>Hermes / OpenClaw setup</CardTitle>
                </div>
                <Badge variant={setupRawApiKey ? "default" : "secondary"}>
                  {setupRawApiKey ? "key ready" : "needs key"}
                </Badge>
              </CardHeader>
              <CardContent>
                <div className="dashboard-setup-controls">
                  <label className="dashboard-settings-field">
                    Agent
                    <select value={setupRuntime} onChange={(event) => setSetupRuntime(event.target.value)}>
                      {runtimeOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="dashboard-settings-field">
                    Inference
                    <select value={setupInferenceMode} onChange={(event) => setSetupInferenceMode(event.target.value)}>
                      {inferenceModeOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="dashboard-settings-field">
                    Budget
                    <input
                      min="1"
                      type="number"
                      value={setupBudget}
                      onChange={(event) => setSetupBudget(event.target.value)}
                    />
                  </label>
                  <label className="dashboard-settings-field">
                    Cycle
                    <select value={setupPlanType} onChange={(event) => setSetupPlanType(event.target.value)}>
                      {planOptions.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="dashboard-settings-field dashboard-setup-route-field">
                    Route ID
                    <input
                      value={setupRouteId}
                      onChange={(event) => setSetupRouteId(event.target.value)}
                    />
                  </label>
                </div>
                {!setupRawApiKey ? (
                  <Card className="dashboard-setup-key-callout">
                    <CardContent>
                      <div>
                        <strong>Create a setup key</strong>
                        <span>Existing keys are masked, so a fresh key is needed before the prompt can be pasted.</span>
                      </div>
                      <Button type="button" onClick={createSetupApiKey} disabled={isCreatingSetupKey}>
                        {isCreatingSetupKey ? "Creating..." : "Create setup key"}
                      </Button>
                    </CardContent>
                  </Card>
                ) : (
                  <div className="dashboard-created-key">
                    <span>Setup API key</span>
                    <code>{setupRawApiKey}</code>
                  </div>
                )}
                {setupError ? <p className="dashboard-settings-message dashboard-settings-message-error">{setupError}</p> : null}
                {setupCopyNotice ? <p className="dashboard-settings-message dashboard-settings-message-success">{setupCopyNotice}</p> : null}
              </CardContent>
            </Card>

            <Card className="dashboard-setup-prompt-card">
              <CardHeader>
                <div>
                  <CardTitle>{setupAgentName} prompt</CardTitle>
                </div>
                <Button type="button" onClick={copySetupInstruction} disabled={!setupRawApiKey || !setupInstruction}>
                  Copy prompt
                </Button>
              </CardHeader>
              <CardContent>
                <pre>{setupInstruction || "Loading setup prompt..."}</pre>
              </CardContent>
            </Card>
          </div>
        ) : null}

        {activePage === "settings" ? (
          <div className="dashboard-settings-grid">
            <Card className="dashboard-settings-card">
              <CardHeader>
                <CardTitle>{session?.user?.email}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="dashboard-settings-facts">
                  <div>
                    <span>Route ID</span>
                    <code>{budgetData?.user?.routeId || budgetData?.mongoUser?.routeId || "default"}</code>
                  </div>
                  <div>
                    <span>Email verification</span>
                    <strong>{session?.user?.emailVerified ? "Verified" : "Not verified"}</strong>
                  </div>
                </div>
                <div className="dashboard-settings-actions">
                  <Button asChild variant="outline">
                    <a href="/onboarding">Setup</a>
                  </Button>
                  <Button variant="outline" type="button" onClick={logout}>
                    Log out
                  </Button>
                </div>
              </CardContent>
            </Card>

            <Card className="dashboard-settings-card dashboard-theme-card">
              <CardHeader>
                <CardTitle>Dashboard theme</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="dashboard-theme-options" aria-label="Dashboard theme">
                  {[
                    ["white", "White"],
                    ["black", "Black"],
                  ].map(([theme, label]) => (
                    <button
                      className={dashboardTheme === theme ? "dashboard-theme-option dashboard-theme-option-active" : "dashboard-theme-option"}
                      key={theme}
                      type="button"
                      aria-pressed={dashboardTheme === theme}
                      onClick={() => changeDashboardTheme(theme)}
                    >
                      <span className={`dashboard-theme-swatch dashboard-theme-swatch-${theme}`} aria-hidden="true" />
                      {label}
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card className="dashboard-settings-card">
              <CardHeader>
                <CardTitle>Change password</CardTitle>
              </CardHeader>
              <CardContent>
                <form className="dashboard-settings-form" onSubmit={changePassword}>
                  <label className="dashboard-settings-field">
                    Current password
                    <input
                      type="password"
                      value={currentPassword}
                      autoComplete="current-password"
                      onChange={(event) => setCurrentPassword(event.target.value)}
                    />
                  </label>
                  <label className="dashboard-settings-field">
                    New password
                    <input
                      type="password"
                      value={newPassword}
                      autoComplete="new-password"
                      onChange={(event) => setNewPassword(event.target.value)}
                    />
                  </label>
                  <label className="dashboard-settings-field">
                    Confirm new password
                    <input
                      type="password"
                      value={confirmPassword}
                      autoComplete="new-password"
                      onChange={(event) => setConfirmPassword(event.target.value)}
                    />
                  </label>
                  {settingsError ? <p className="dashboard-settings-message dashboard-settings-message-error">{settingsError}</p> : null}
                  {settingsNotice ? <p className="dashboard-settings-message dashboard-settings-message-success">{settingsNotice}</p> : null}
                  <Button type="submit" disabled={isChangingPassword}>
                    {isChangingPassword ? "Updating..." : "Update password"}
                  </Button>
                </form>
              </CardContent>
            </Card>
          </div>
        ) : null}
      </section>
    </main>
  );
}
