import assert from "node:assert/strict";
import test from "node:test";

import {
  buildPluginAccessSnapshot,
  pluginSubscriptionActive,
} from "./pluginAccess.js";

test("denies access without an active subscription", () => {
  const access = buildPluginAccessSnapshot({
    counter: null,
    subscription: null,
    userId: "user-1",
  });

  assert.equal(access.allowed, false);
  assert.equal(access.tier, "none");
  assert.deepEqual(access.prices, { monthly: 10, yearly: 100 });
});

test("allows only active or trialing subscriptions", () => {
  assert.equal(pluginSubscriptionActive("active"), true);
  assert.equal(pluginSubscriptionActive("trialing"), true);
  assert.equal(pluginSubscriptionActive("past_due"), false);
  assert.equal(pluginSubscriptionActive("canceled"), false);

  const access = buildPluginAccessSnapshot({
    counter: null,
    subscription: {
      status: "active",
      stripeCustomerId: "cus_123",
      stripeSubscriptionId: "sub_123",
    },
    userId: "subscriber-1",
  });

  assert.equal(access.allowed, true);
  assert.equal(access.tier, "subscriber");
  assert.deepEqual(access.prices, { monthly: 10, yearly: 100 });
});

test("denies inactive subscriptions", () => {
  const access = buildPluginAccessSnapshot({
    counter: null,
    subscription: { status: "past_due" },
    userId: "user-2",
  });

  assert.equal(access.allowed, false);
  assert.equal(access.tier, "none");
  assert.equal(access.subscriptionStatus, "past_due");
});
