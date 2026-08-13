import assert from "node:assert/strict";
import test from "node:test";
import {
  budgetInputFromManifest,
  routeIdFromWorkspace,
  validateInstallationManifest,
} from "./installation.js";

test("validates all terminal onboarding decisions", () => {
  const manifest = validateInstallationManifest({
    workspace_name: "Production Agents",
    harness: "openclaw",
    inference_mode: "router_managed",
    budget: { amount_usd: 250, cycle: "quarterly" },
    route_refresh_interval: "7d",
  });

  assert.equal(manifest.route_id, "route_production_agents");
  assert.equal(manifest.harness, "openclaw");
  assert.equal(manifest.inference_mode, "router_managed");
  assert.deepEqual(manifest.budget, { amount_usd: 250, cycle: "quarterly" });
});

test("rejects unsupported refresh intervals instead of silently changing them", () => {
  assert.throws(() => validateInstallationManifest({
    workspace_name: "Production Agents",
    harness: "hermes",
    inference_mode: "user_managed",
    budget: { amount_usd: 250, cycle: "monthly" },
    route_refresh_interval: "24h",
  }), /must be 7d/);
});

test("maps the manifest budget to the existing budget schema", () => {
  const manifest = validateInstallationManifest({
    workspace_name: "Production Agents",
    harness: "hermes",
    inference_mode: "user_managed",
    budget: { amount_usd: 100, cycle: "weekly" },
  });
  const budget = budgetInputFromManifest(manifest);
  assert.equal(budget.routeId, routeIdFromWorkspace("Production Agents"));
  assert.equal(budget.budgetUsd, 100);
  assert.equal(budget.budgetCycleDays, 7);
});
