import { randomBytes, randomUUID } from "node:crypto";
import { PLAN_DAYS, normalizeUserBudgetInput } from "./routeSchemas.js";

export const DEVICE_SESSION_SECONDS = 10 * 60;
export const INSTALL_TOKEN_SECONDS = 5 * 60;
export const INSTALLATION_HARNESSES = new Set(["hermes", "openclaw"]);
export const INSTALLATION_INFERENCE_MODES = new Set(["user_managed", "router_managed"]);
export const INSTALLATION_CYCLES = new Set(["weekly", "monthly", "quarterly", "yearly"]);

export function randomToken(bytes = 32) {
  return randomBytes(bytes).toString("base64url");
}

export function userCode() {
  const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const bytes = randomBytes(8);
  const value = [...bytes].map((byte) => alphabet[byte % alphabet.length]).join("");
  return `${value.slice(0, 4)}-${value.slice(4)}`;
}

export function routeIdFromWorkspace(workspaceName) {
  const seed = String(workspaceName || "workspace")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 32);
  return `route_${seed || "workspace"}`;
}

export function validateInstallationManifest(input) {
  const workspaceName = String(input.workspace_name || "").trim();
  const harness = String(input.harness || "").trim().toLowerCase();
  const inferenceMode = String(input.inference_mode || "").trim().toLowerCase();
  const amountUsd = Number(input.budget?.amount_usd);
  const cycle = String(input.budget?.cycle || "").trim().toLowerCase();
  const routeRefreshInterval = String(input.route_refresh_interval || "7d").trim().toLowerCase();

  if (!workspaceName || workspaceName.length > 80) {
    throw new Error("workspace_name must contain 1 to 80 characters.");
  }
  if (!INSTALLATION_HARNESSES.has(harness)) {
    throw new Error("harness must be hermes or openclaw.");
  }
  if (!INSTALLATION_INFERENCE_MODES.has(inferenceMode)) {
    throw new Error("inference_mode must be user_managed or router_managed.");
  }
  if (!Number.isFinite(amountUsd) || amountUsd <= 0) {
    throw new Error("budget.amount_usd must be a positive number.");
  }
  if (!INSTALLATION_CYCLES.has(cycle)) {
    throw new Error("budget.cycle must be weekly, monthly, quarterly, or yearly.");
  }
  if (routeRefreshInterval !== "7d") {
    throw new Error("route_refresh_interval must be 7d in this release.");
  }

  return {
    installation_id: randomUUID(),
    workspace_name: workspaceName,
    route_id: routeIdFromWorkspace(workspaceName),
    harness,
    inference_mode: inferenceMode,
    budget: {
      amount_usd: Math.round(amountUsd * 100) / 100,
      cycle,
    },
    route_refresh_interval: routeRefreshInterval,
  };
}

export function budgetInputFromManifest(manifest) {
  return normalizeUserBudgetInput({
    routeId: manifest.route_id,
    budget: manifest.budget.amount_usd,
    budgetRemaining: manifest.budget.amount_usd,
    planType: manifest.budget.cycle,
    budgetCycleDays: PLAN_DAYS[manifest.budget.cycle],
    budgetElapsedDays: 0,
  });
}
