import assert from "node:assert/strict";
import test from "node:test";

import {
  publicProviderConnection,
  validateReviewedSubscriptionInput,
  validateProviderConnectionInput,
} from "./infiniteProviderConnections.js";

test("user provider admission is explicit and allowlisted", () => {
  process.env.PROMPTRAIL_INFINITE_PROVIDER_ALLOWLIST = "openrouter,together";
  assert.deepEqual(
    validateProviderConnectionInput({
      provider: "OpenRouter",
      capacityClass: "user_free",
      admissionStatus: "byok_only",
      apiKey: "provider-secret",
    }),
    {
      provider: "openrouter",
      capacityClass: "user_free",
      admissionStatus: "byok_only",
      apiKey: "provider-secret",
    },
  );
  assert.throws(
    () =>
      validateProviderConnectionInput({
        provider: "unknown",
        capacityClass: "user_free",
        admissionStatus: "byok_only",
        apiKey: "provider-secret",
      }),
    /not admitted/,
  );
});

test("reviewed subscription credentials are OAuth-only and bounded", () => {
  process.env.PROMPTRAIL_INFINITE_PROVIDER_ALLOWLIST = "codex,anthropic";
  assert.equal(
    validateReviewedSubscriptionInput({
      provider: "codex",
      credentials: {
        authType: "oauth",
        accessToken: "access",
        refreshToken: "refresh",
      },
    }).provider,
    "codex",
  );
  assert.throws(() =>
    validateReviewedSubscriptionInput({
      provider: "codex",
      credentials: { authType: "apikey", accessToken: "access" },
    }),
  );
});

test("API-key flow accepts only verified free BYOK capacity", () => {
  process.env.PROMPTRAIL_INFINITE_PROVIDER_ALLOWLIST = "openrouter";
  for (const input of [
    { capacityClass: "managed_free", admissionStatus: "hosted_allowed" },
    { capacityClass: "user_subscription", admissionStatus: "byok_only" },
    { capacityClass: "user_paid", admissionStatus: "hosted_allowed" },
    { capacityClass: "user_paid", admissionStatus: "byok_only" },
  ]) {
    assert.throws(() =>
      validateProviderConnectionInput({
        provider: "openrouter",
        apiKey: "provider-secret",
        ...input,
      }),
    );
  }
});

test("public provider connection never exposes credential material", () => {
  const publicRow = publicProviderConnection({
    id: "connection-1",
    provider: "openrouter",
    capacityClass: "user_free",
    admissionStatus: "byok_only",
    status: "active",
    credentialVersion: 1,
    credentialEnvelope: { ciphertext: "secret-ciphertext" },
  });
  assert.equal("credentialEnvelope" in publicRow, false);
  assert.equal(JSON.stringify(publicRow).includes("secret-ciphertext"), false);
});
