import assert from "node:assert/strict";
import test from "node:test";
import { sanitizeReceipt } from "../api/infinite/internal/receipts/route.js";

test("Infinite receipts retain routing facts but reject prompt-shaped fields", () => {
  const receipt = sanitizeReceipt({
    route_id: "route_123",
    tenant_id: "tenant_1",
    policy_version: "infinite-v1",
    catalog_version: "catalog-v1",
    selected_model: "provider/model",
    executed_model: "provider/model",
    capacity_class: "user_free",
    attempts: [
      { provider: "provider", connection_id_hash: "hash", result: "success", dispatched: true },
    ],
    pre_dispatch_latency_ms: 7,
    prompt: "must not be retained",
    response: "must not be retained",
    shadow_decision: {
      policy_version: "infinite-v1",
      candidate_ids: ["free-a"],
      capacity_classes: { "free-a": "user_free" },
      predicted_success: { "free-a": 0.88 },
      selected_hypothetical_route: "free-a",
      decision_latency_ms: 14,
      capability_rejection_reasons: {},
      prompt: "must not be retained here either",
    },
  });
  assert.equal(receipt.virtual_model, "promptrail/infinite");
  assert.equal("prompt" in receipt, false);
  assert.equal("response" in receipt, false);
  assert.deepEqual(receipt.shadow_decision.candidate_ids, ["free-a"]);
  assert.equal("prompt" in receipt.shadow_decision, false);
  assert.equal(receipt.pre_dispatch_latency_ms, 7);
  assert.equal(receipt.attempts[0].dispatched, true);
});

test("Infinite receipts reject non-finite accounting values", () => {
  assert.throws(
    () => sanitizeReceipt({
      route_id: "route_123",
      tenant_id: "tenant_1",
      policy_version: "infinite-v1",
      catalog_version: "catalog-v1",
      capacity_class: "user_free",
      total_latency_ms: Number.POSITIVE_INFINITY,
    }),
    /Invalid total latency/,
  );
});

test("Infinite receipts reject paid API execution", () => {
  assert.throws(
    () => sanitizeReceipt({
      route_id: "route_paid",
      tenant_id: "tenant_1",
      policy_version: "infinite-v1",
      catalog_version: "catalog-v1",
      capacity_class: "user_paid",
    }),
    /Invalid capacity class/,
  );
});
