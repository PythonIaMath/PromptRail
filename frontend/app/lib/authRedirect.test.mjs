import assert from "node:assert/strict";
import test from "node:test";

import {
  authDestination,
  authErrorCallbackPath,
  decodeAuthRedirectPath,
  encodeAuthRedirectPath,
  resolveAuthRedirect,
  safeAuthRedirectPath,
} from "./authRedirect.js";

test("defaults existing users to the dashboard and new users to onboarding", () => {
  const redirect = resolveAuthRedirect("");

  assert.equal(redirect.mode, "login");
  assert.equal(authDestination("login", redirect.nextPath, redirect.signupPath), "/dashboard");
  assert.equal(authDestination("signup", redirect.nextPath, redirect.signupPath), "/onboarding");
});

test("preserves the plugin onboarding destination for signup", () => {
  const redirect = resolveAuthRedirect("?mode=signup&next=/plugins/onboarding");

  assert.equal(redirect.mode, "signup");
  assert.equal(
    authDestination(redirect.mode, redirect.nextPath, redirect.signupPath),
    "/plugins/onboarding",
  );
});

test("preserves an explicit dashboard destination for signup", () => {
  const redirect = resolveAuthRedirect("?mode=signup&next=/dashboard");

  assert.equal(
    authDestination(redirect.mode, redirect.nextPath, redirect.signupPath),
    "/dashboard",
  );
});

test("preserves query parameters in protected-page destinations", () => {
  const redirect = resolveAuthRedirect("?next=%2Fdevice%3Fcode%3DABCD-1234");

  assert.equal(
    authDestination(redirect.mode, redirect.nextPath, redirect.signupPath),
    "/device?code=ABCD-1234",
  );
});

test("rejects absolute and protocol-relative redirect destinations", () => {
  assert.equal(
    safeAuthRedirectPath("https://attacker.example/session", "/dashboard"),
    "/dashboard",
  );
  assert.equal(
    safeAuthRedirectPath("//attacker.example/session", "/dashboard"),
    "/dashboard",
  );
});

test("rejects backslash redirect destinations", () => {
  assert.equal(
    safeAuthRedirectPath("/\\attacker.example/session", "/dashboard"),
    "/dashboard",
  );
});

test("keeps local redirect paths with query strings and fragments", () => {
  assert.equal(
    safeAuthRedirectPath("/device?code=ABCD-1234#approve", "/dashboard"),
    "/device?code=ABCD-1234#approve",
  );
});

test("round-trips redirect paths through a Better Auth-safe value", () => {
  const path = "/plugins/onboarding?subscription=success&checkout_session_id=cs_test_123";

  assert.equal(decodeAuthRedirectPath(encodeAuthRedirectPath(path)), path);
  assert.equal(decodeAuthRedirectPath("not-hex"), "");
});

test("keeps query-bearing destinations out of the magic-link error callback query", () => {
  const nextPath = "/plugins/onboarding?subscription=success&checkout_session_id=cs_test_123";
  const callbackPath = authErrorCallbackPath({
    mode: "signup",
    nextPath,
    checkoutActivation: true,
  });

  assert.equal((callbackPath.match(/\?/g) || []).length, 1);
  assert.equal(callbackPath.includes("checkout_session_id"), false);
  assert.equal(
    resolveAuthRedirect(callbackPath.slice(callbackPath.indexOf("?"))).signupPath,
    nextPath,
  );
});
