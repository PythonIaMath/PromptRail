import assert from "node:assert/strict";
import test from "node:test";
import { pluginOnboardingView } from "./pluginOnboardingState.js";

function view(overrides = {}) {
  return pluginOnboardingView({
    access: null,
    accessLoading: false,
    hasCheckoutSession: false,
    hasUser: false,
    isSessionPending: false,
    isUrlPending: false,
    subscriptionState: "",
    ...overrides,
  });
}

test("keeps the onboarding page loading until session and access resolve", () => {
  assert.equal(view({ isSessionPending: true }), "loading");
  assert.equal(view({ isUrlPending: true }), "loading");
  assert.equal(view({ hasUser: true, accessLoading: true }), "loading");
});

test("shows pricing only before the account has plugin access", () => {
  assert.equal(view(), "pricing");
  assert.equal(view({ hasUser: true, access: { allowed: false } }), "pricing");
});

test("does not send a successful checkout return back to pricing", () => {
  assert.equal(
    view({ hasCheckoutSession: true, subscriptionState: "success" }),
    "accountRequired",
  );
  assert.equal(view({ subscriptionState: "success" }), "pricing");
  assert.equal(
    view({
      hasUser: true,
      access: { allowed: false },
      subscriptionState: "success",
    }),
    "paymentPending",
  );
});

test("shows setup as soon as plugin access is active", () => {
  assert.equal(view({ hasUser: true, access: { allowed: true } }), "setup");
  assert.equal(
    view({
      hasUser: true,
      access: { allowed: true },
      subscriptionState: "success",
    }),
    "setup",
  );
});
