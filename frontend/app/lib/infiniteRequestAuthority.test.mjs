import assert from "node:assert/strict";
import test from "node:test";
import {
  hashPromptRailApiKey,
  materializeTenantCandidates,
  policyResponse,
  selectTenantPolicy,
  validateInfiniteApiKeyRecord,
} from "./infiniteRequestAuthority.js";

test("Infinite request authority hashes keys without retaining the plaintext", () => {
  const hash = hashPromptRailApiKey("lr_live_secret");
  assert.equal(hash.length, 64);
  assert.equal(hash.includes("secret"), false);
});

test("Infinite request authority requires an active scoped Infinite key", () => {
  assert.equal(validateInfiniteApiKeyRecord(null), null);
  assert.equal(
    validateInfiniteApiKeyRecord({
      userId: "tenant",
      kind: "plugin",
      scopes: [],
    }),
    null,
  );
  assert.equal(
    validateInfiniteApiKeyRecord({
      id: "key_1",
      userId: "tenant_1",
      kind: "infinite",
      scopes: ["infinite:infer", "usage:read"],
      revokedAt: null,
    }).tenantId,
    "tenant_1",
  );
});

test("tenant provider templates resolve only the authorized tenant's active connections", () => {
  const candidates = [
    {
      candidateId: "free-openrouter",
      provider: "openrouter",
      model: "cohere/north-mini-code:free",
      capacityClass: "user_free",
      admissionStatus: "byok_only",
      connectionSource: "tenant_provider",
      connectionIds: ["stale-control-plane-reference"],
      apiKey: "must-never-cross-the-authority-boundary",
      credentials: { apiKey: "also-secret" },
      capabilities: { toolCalling: true, apiKey: "nested-secret" },
      operationalState: {
        healthy: true,
        estimatedRequestCostUsd: 0,
        authorization: "nested-secret",
      },
      semanticProfile: {
        profileText: "coding actor",
        completionSuccessPrior: 0.8,
        rawPrompt: "must-not-cross",
      },
    },
    {
      candidateId: "managed-static",
      provider: "partner",
      capacityClass: "managed_free",
      admissionStatus: "hosted_allowed",
      connectionSource: "static",
      connectionIds: ["managed-connection"],
      operationalState: { healthy: true, estimatedRequestCostUsd: 0 },
    },
    {
      candidateId: "missing-paid",
      provider: "openrouter",
      capacityClass: "user_paid",
      admissionStatus: "byok_only",
      connectionSource: "tenant_provider",
    },
    {
      candidateId: "unverified-free",
      provider: "openrouter",
      model: "openrouter/free",
      capacityClass: "user_free",
      admissionStatus: "byok_only",
      connectionSource: "tenant_provider",
      operationalState: { healthy: true, estimatedRequestCostUsd: 0 },
    },
  ];
  const connections = [
    {
      id: "connection-b",
      userId: "tenant-1",
      provider: "openrouter",
      capacityClass: "user_free",
      admissionStatus: "byok_only",
      status: "active",
    },
    {
      id: "connection-a",
      userId: "tenant-1",
      provider: "openrouter",
      capacityClass: "user_free",
      admissionStatus: "byok_only",
      status: "active",
    },
    {
      id: "other-tenant",
      userId: "tenant-2",
      provider: "openrouter",
      capacityClass: "user_free",
      admissionStatus: "byok_only",
      status: "active",
    },
    {
      id: "revoked",
      userId: "tenant-1",
      provider: "openrouter",
      capacityClass: "user_free",
      admissionStatus: "byok_only",
      status: "revoked",
    },
    {
      id: "paid-connection",
      userId: "tenant-1",
      provider: "openrouter",
      capacityClass: "user_paid",
      admissionStatus: "byok_only",
      status: "active",
    },
  ];

  const resolved = materializeTenantCandidates({
    candidates,
    connections,
    tenantId: "tenant-1",
  });

  assert.deepEqual(
    resolved.map((candidate) => [candidate.candidateId, candidate.connectionIds]),
    [
      ["free-openrouter", ["connection-a", "connection-b"]],
      ["managed-static", ["managed-connection"]],
    ],
  );
  assert.equal(Object.hasOwn(resolved[0], "apiKey"), false);
  assert.equal(Object.hasOwn(resolved[0], "credentials"), false);
  assert.equal(Object.hasOwn(resolved[0], "connectionSource"), false);
  assert.deepEqual(resolved[0].capabilities, { toolCalling: true });
  assert.deepEqual(resolved[0].operationalState, {
    healthy: true,
    estimatedRequestCostUsd: 0,
  });
  assert.deepEqual(resolved[0].semanticProfile, {
    profileText: "coding actor",
    completionSuccessPrior: 0.8,
  });
});

test("tenant-specific candidate overrides the global template deterministically", () => {
  const base = {
    candidateId: "free-openrouter",
    provider: "openrouter",
    model: "cohere/north-mini-code:free",
    capacityClass: "user_free",
    admissionStatus: "byok_only",
    connectionSource: "tenant_provider",
    operationalState: { healthy: true, estimatedRequestCostUsd: 0 },
  };
  const [resolved] = materializeTenantCandidates({
    candidates: [
      { ...base, quality: 0.5 },
      { ...base, tenantId: "tenant-1", quality: 0.9 },
    ],
    connections: [
      {
        id: "connection-a",
        userId: "tenant-1",
        provider: "openrouter",
        capacityClass: "user_free",
        admissionStatus: "byok_only",
        status: "active",
      },
    ],
    tenantId: "tenant-1",
  });

  assert.equal(resolved.quality, 0.9);
});

test("gateway policy response excludes control-plane-only and unknown fields", () => {
  assert.deepEqual(
    policyResponse({
      policyVersion: "infinite-v1",
      catalogVersion: "catalog-v1",
      freeQualityFloor: 0.8,
      modelVariationCoefficient: 0.75,
      allowedCandidateIds: ["candidate"],
      allowPaidByok: true,
      paidPerRequestLimitUsd: 10,
      paidMonthlyBudgetRemainingUsd: 100,
      internalNote: "do not return",
    }),
    {
      policyVersion: "infinite-v1",
      catalogVersion: "catalog-v1",
      freeQualityFloor: 0.8,
      modelVariationCoefficient: 0.75,
    },
  );
});

test("gateway policy restores the reviewed routing variation for legacy policies", () => {
  assert.equal(policyResponse({ policyVersion: "infinite-v1" }).modelVariationCoefficient, 0.75);
});

test("tenant policy selection prefers a tenant override and otherwise uses the global default", () => {
  const globalPolicy = { tenantId: null, policyVersion: "global" };
  const tenantPolicy = { tenantId: "tenant-1", policyVersion: "tenant" };
  assert.equal(selectTenantPolicy([globalPolicy, tenantPolicy], "tenant-1"), tenantPolicy);
  assert.equal(selectTenantPolicy([globalPolicy, tenantPolicy], "tenant-2"), globalPolicy);
  assert.equal(selectTenantPolicy([], "tenant-2"), null);
});
