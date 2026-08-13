import assert from "node:assert/strict";
import test from "node:test";

import {
  pluginCheckoutClaim,
  pluginCheckoutMetadata,
  pluginCheckoutSuccessUrl,
} from "./pluginCheckout.js";

function paidCheckout(overrides = {}) {
  return {
    client_reference_id: null,
    customer_details: { email: "buyer@example.com" },
    metadata: { kind: "plugin_subscription", accountRequired: "true" },
    payment_status: "paid",
    status: "complete",
    subscription: {
      id: "sub_123",
      customer: "cus_123",
      metadata: { kind: "plugin_subscription", accountRequired: "true" },
      status: "active",
    },
    ...overrides,
  };
}

test("creates guest checkout metadata without inventing an account identity", () => {
  assert.deepEqual(pluginCheckoutMetadata({ interval: "year" }), {
    kind: "plugin_subscription",
    interval: "year",
    accountRequired: "true",
  });
});

test("keeps Stripe's Checkout Session template literal in the success URL", () => {
  assert.equal(
    pluginCheckoutSuccessUrl("https://promptrail.ai/"),
    "https://promptrail.ai/plugins/onboarding?subscription=success&checkout_session_id={CHECKOUT_SESSION_ID}",
  );
});

test("claims a paid guest subscription for an account with the Stripe email", () => {
  assert.deepEqual(
    pluginCheckoutClaim(paidCheckout(), { id: "user_123", email: "Buyer@Example.com" }),
    {
      status: "active",
      stripeCustomerId: "cus_123",
      stripeSubscriptionId: "sub_123",
      userEmail: "Buyer@Example.com",
      userId: "user_123",
    },
  );
});

test("rejects a guest subscription claim from a different email", () => {
  assert.throws(
    () => pluginCheckoutClaim(paidCheckout(), { id: "user_456", email: "attacker@example.com" }),
    (error) => error.code === "email_mismatch" && error.status === 403,
  );
});

test("rejects an unpaid or inactive Checkout Session", () => {
  assert.throws(
    () => pluginCheckoutClaim(paidCheckout({ payment_status: "unpaid" }), { id: "user_123", email: "buyer@example.com" }),
    (error) => error.code === "payment_not_complete",
  );
  assert.throws(
    () => pluginCheckoutClaim(paidCheckout({
      subscription: { id: "sub_123", customer: "cus_123", status: "past_due" },
    }), { id: "user_123", email: "buyer@example.com" }),
    (error) => error.code === "subscription_inactive",
  );
});

test("does not let another account claim a checkout created while signed in", () => {
  assert.throws(
    () => pluginCheckoutClaim(paidCheckout({ client_reference_id: "original_user" }), {
      id: "different_user",
      email: "buyer@example.com",
    }),
    (error) => error.code === "account_mismatch" && error.status === 403,
  );
});
