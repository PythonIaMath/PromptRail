import assert from "node:assert/strict";
import test from "node:test";
import { normalizeAnswers, routeIdFromWorkspace } from "../lib/core.mjs";
import {
  OPENCLAW_USER_MANAGED_SETUP_CONTRACT,
  openClawSetupContract,
} from "../lib/harnesses.mjs";

test("normalizes the current onboarding decisions", () => {
  assert.deepEqual(normalizeAnswers({
    workspace_name: "Acme Agents",
    harness: "hermes",
    inference_mode: "user_managed",
    budget: { amount_usd: "500", cycle: "monthly" },
  }), {
    workspace_name: "Acme Agents",
    route_id: "route_acme_agents",
    harness: "hermes",
    inference_mode: "user_managed",
    budget: { amount_usd: 500, cycle: "monthly" },
    route_refresh_interval: "7d",
  });
});

test("rejects missing decisions instead of applying fallbacks", () => {
  assert.throws(() => normalizeAnswers({
    workspace_name: "",
    harness: "hermes",
    inference_mode: "user_managed",
    budget: { amount_usd: 500, cycle: "monthly" },
  }), /Workspace name/);
});

test("route ids match the dashboard convention", () => {
  assert.equal(routeIdFromWorkspace("R&D / Production"), "route_r_d_production");
});

test("exposes the credential-aware OpenClaw user-managed setup contract", () => {
  assert.deepEqual(
    openClawSetupContract("user_managed"),
    OPENCLAW_USER_MANAGED_SETUP_CONTRACT,
  );
  assert.deepEqual(openClawSetupContract("router_managed"), []);
  assert.throws(
    () => openClawSetupContract("invalid"),
    /Unsupported OpenClaw inference mode/,
  );
});
