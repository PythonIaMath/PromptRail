import { createHash } from "node:crypto";
import { serverEnv } from "./serverEnv.js";
import { requireMongoDatabase } from "./mongo.js";
import { buildInfiniteAccessSnapshot } from "./infiniteAccess.js";

function collection(database, envName, fallback) {
  return database.collection(serverEnv(envName, fallback));
}

function normalizedConnectionId(row) {
  return String(row?.id || "").trim();
}

const CANDIDATE_RESPONSE_FIELDS = Object.freeze([
  "candidateId",
  "provider",
  "model",
  "capacityClass",
  "admissionStatus",
  "baseUrl",
  "wireApi",
  "executorMode",
  "quality",
  "priority",
]);

const CAPABILITY_RESPONSE_FIELDS = Object.freeze([
  "contextWindow",
  "toolCalling",
  "vision",
  "structuredOutput",
  "reasoning",
  "streaming",
  "maximumOutputTokens",
]);
const OPERATIONAL_RESPONSE_FIELDS = Object.freeze([
  "healthy",
  "quotaKnown",
  "quotaRemainingFraction",
  "reserveFloor",
  "recentLatencyMs",
  "recentFailureRate",
  "estimatedRequestCostUsd",
]);
const SEMANTIC_RESPONSE_FIELDS = Object.freeze([
  "profileText",
  "completionSuccessPrior",
]);
const POLICY_RESPONSE_FIELDS = Object.freeze([
  "policyVersion",
  "catalogVersion",
  "freeQualityFloor",
  "modelVariationCoefficient",
  "allowReserveUse",
  "maximumAttempts",
  "deadlineMs",
]);
const DEFAULT_MODEL_VARIATION_COEFFICIENT = 0.75;
const ROUTABLE_CAPACITY_CLASSES = new Set([
  "managed_free",
  "user_free",
  "user_subscription",
]);

function candidateIsRoutable(candidate) {
  if (!ROUTABLE_CAPACITY_CLASSES.has(candidate?.capacityClass)) return false;
  if (["managed_free", "user_free"].includes(candidate.capacityClass)) {
    const estimatedCost = candidate?.operationalState?.estimatedRequestCostUsd;
    if (
      typeof estimatedCost !== "number" ||
      !Number.isFinite(estimatedCost) ||
      estimatedCost !== 0
    ) {
      return false;
    }
  }
  if (
    candidate.provider === "openrouter" &&
    candidate.capacityClass === "user_free" &&
    (candidate.model === "openrouter/free" ||
      !String(candidate.model || "").endsWith(":free"))
  ) {
    return false;
  }
  return true;
}

function pickFields(value, fields) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    fields.filter((field) => Object.hasOwn(value, field)).map((field) => [field, value[field]]),
  );
}

export function policyResponse(policy) {
  const response = pickFields(policy, POLICY_RESPONSE_FIELDS);
  if (!Object.hasOwn(response, "modelVariationCoefficient")) {
    response.modelVariationCoefficient = DEFAULT_MODEL_VARIATION_COEFFICIENT;
  }
  return response;
}

export function selectTenantPolicy(policies, tenantId) {
  const rows = Array.isArray(policies) ? policies : [];
  return (
    rows.find((policy) => String(policy?.tenantId || "") === tenantId)
    || rows.find((policy) => policy?.tenantId == null)
    || null
  );
}

function candidateResponse(candidate, connectionIds) {
  return {
    ...pickFields(candidate, CANDIDATE_RESPONSE_FIELDS),
    connectionIds,
    capabilities: pickFields(candidate.capabilities, CAPABILITY_RESPONSE_FIELDS),
    operationalState: pickFields(
      candidate.operationalState,
      OPERATIONAL_RESPONSE_FIELDS,
    ),
    semanticProfile: pickFields(candidate.semanticProfile, SEMANTIC_RESPONSE_FIELDS),
  };
}

function candidateForTenant(candidates, tenantId) {
  const selected = new Map();
  for (const candidate of candidates) {
    const candidateId = String(candidate?.candidateId || "").trim();
    if (!candidateId) continue;
    const existing = selected.get(candidateId);
    const tenantSpecific = String(candidate?.tenantId || "") === tenantId;
    const existingTenantSpecific = String(existing?.tenantId || "") === tenantId;
    if (!existing || (tenantSpecific && !existingTenantSpecific)) {
      selected.set(candidateId, candidate);
    }
  }
  return [...selected.values()].sort((left, right) =>
    String(left.candidateId).localeCompare(String(right.candidateId)),
  );
}

export function materializeTenantCandidates({ candidates, connections, tenantId }) {
  const scopedCandidates = candidateForTenant(candidates, tenantId).filter(candidateIsRoutable);
  const activeConnections = connections
    .filter(
      (connection) =>
        String(connection?.userId || "") === tenantId &&
        connection?.status === "active" &&
        ROUTABLE_CAPACITY_CLASSES.has(connection?.capacityClass) &&
        normalizedConnectionId(connection),
    )
    .sort((left, right) => {
      const priority = Number(left.priority || 0) - Number(right.priority || 0);
      return priority || normalizedConnectionId(left).localeCompare(normalizedConnectionId(right));
    });

  return scopedCandidates.flatMap((candidate) => {
    if (candidate.connectionSource === "static") {
      const connectionIds = Array.isArray(candidate.connectionIds)
        ? [...new Set(candidate.connectionIds.map(String).filter(Boolean))]
        : [];
      return connectionIds.length > 0
        ? [candidateResponse(candidate, connectionIds)]
        : [];
    }
    if (candidate.connectionSource !== "tenant_provider") return [];

    const connectionIds = activeConnections
      .filter(
        (connection) =>
          connection.provider === candidate.provider &&
          connection.capacityClass === candidate.capacityClass &&
          connection.admissionStatus === candidate.admissionStatus,
      )
      .map(normalizedConnectionId);
    return connectionIds.length > 0
      ? [candidateResponse(candidate, connectionIds)]
      : [];
  });
}

export function hashPromptRailApiKey(value) {
  return createHash("sha256")
    .update(String(value || ""), "utf8")
    .digest("hex");
}

export function validateInfiniteApiKeyRecord(apiKey) {
  if (!apiKey?.userId || apiKey.revokedAt) return null;
  const scopes = Array.isArray(apiKey.scopes) ? apiKey.scopes.map(String) : [];
  if (apiKey.kind !== "infinite" || !scopes.includes("infinite:infer"))
    return null;
  return {
    keyId: String(apiKey.id || ""),
    tenantId: String(apiKey.userId),
    scopes,
  };
}

export async function authorizeInfiniteRequest(apiKeyValue) {
  const token = String(apiKeyValue || "").trim();
  if (!token || token.length > 4096)
    return { status: 401, code: "invalid_api_key" };

  const database = await requireMongoDatabase();
  const apiKey = await collection(
    database,
    "LEROUTER_API_KEY_COLLECTION",
    "api_keys",
  ).findOne(
    { keyHash: hashPromptRailApiKey(token), revokedAt: null },
    {
      projection: {
        _id: 0,
        id: 1,
        userId: 1,
        kind: 1,
        scopes: 1,
        revokedAt: 1,
      },
    },
  );
  const identity = validateInfiniteApiKeyRecord(apiKey);
  if (!identity) return { status: 401, code: "invalid_api_key" };

  const entitlementRows = await collection(
    database,
    "LEROUTER_PRODUCT_ENTITLEMENT_COLLECTION",
    "product_entitlements",
  )
    .find(
      {
        userId: identity.tenantId,
        product: { $in: ["infinite", "infinite_beta"] },
      },
      { projection: { _id: 0 } },
    )
    .toArray();
  const access = buildInfiniteAccessSnapshot(entitlementRows);
  if (!access.allowed) {
    return {
      status: 402,
      code: "infinite_entitlement_required",
      identity,
      access,
    };
  }

  const policyRows = await collection(
    database,
    "LEROUTER_INFINITE_TENANT_POLICY_COLLECTION",
    "infinite_tenant_policies",
  )
    .find(
      {
        $or: [
          { tenantId: identity.tenantId },
          { tenantId: null },
          { tenantId: { $exists: false } },
        ],
      },
      { projection: { _id: 0 } },
    )
    .toArray();
  const policy = selectTenantPolicy(policyRows, identity.tenantId);
  if (!policy)
    return { status: 503, code: "tenant_policy_unavailable", identity, access };

  const allowedCandidateIds = Array.isArray(policy.allowedCandidateIds)
    ? policy.allowedCandidateIds.map(String)
    : [];
  const candidateRows = await collection(
    database,
    "LEROUTER_INFINITE_CANDIDATE_COLLECTION",
    "infinite_candidates",
  )
    .find(
      {
        candidateId: { $in: allowedCandidateIds },
        enabled: true,
        $or: [
          { tenantId: identity.tenantId },
          { tenantId: null },
          { tenantId: { $exists: false } },
        ],
      },
      { projection: { _id: 0 } },
    )
    .toArray();

  const dynamicCandidates = candidateRows.filter(
    (candidate) => candidate.connectionSource === "tenant_provider",
  );
  const providers = [...new Set(dynamicCandidates.map((candidate) => candidate.provider))];
  const providerConnections = providers.length
    ? await collection(
        database,
        "LEROUTER_INFINITE_PROVIDER_CONNECTION_COLLECTION",
        "infinite_provider_connections",
      )
        .find(
          {
            userId: identity.tenantId,
            status: "active",
            provider: { $in: providers },
          },
          {
            projection: {
              _id: 0,
              id: 1,
              userId: 1,
              provider: 1,
              capacityClass: 1,
              admissionStatus: 1,
              status: 1,
              priority: 1,
            },
          },
        )
        .toArray()
    : [];
  const candidates = materializeTenantCandidates({
    candidates: candidateRows,
    connections: providerConnections,
    tenantId: identity.tenantId,
  });

  return {
    status: 200,
    identity,
    access,
    policy: policyResponse(policy),
    candidates,
  };
}
