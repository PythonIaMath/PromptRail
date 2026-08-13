import { serverEnv } from "./serverEnv.js";
import { requireMongoDatabase } from "./mongo.js";
import { getPluginAccess } from "./pluginAccess.js";

export const INFINITE_INFERENCE_SCOPES = Object.freeze([
  "infinite:infer",
  "providers:connect",
  "usage:read",
]);

const ACTIVE_STATUSES = new Set(["active", "trialing"]);
const INFINITE_PRODUCTS = new Set(["infinite", "infinite_beta"]);

function entitlementCollection(database) {
  return database.collection(
    serverEnv("LEROUTER_PRODUCT_ENTITLEMENT_COLLECTION", "product_entitlements"),
  );
}

export function buildInfiniteAccessSnapshot(rows = []) {
  const active = rows.filter(
    (row) => INFINITE_PRODUCTS.has(String(row?.product || ""))
      && ACTIVE_STATUSES.has(String(row?.status || "").toLowerCase()),
  );
  const entitlements = [...new Set(active.map((row) => String(row.product)))].sort();
  const scopes = [...new Set(active.flatMap((row) => Array.isArray(row.scopes) ? row.scopes : []))]
    .map(String)
    .sort();
  return {
    allowed: entitlements.length > 0 && scopes.includes("infinite:infer"),
    entitlements,
    scopes,
    beta: entitlements.includes("infinite_beta"),
  };
}

export async function getInfiniteAccess(userId = null) {
  if (!userId) {
    return buildInfiniteAccessSnapshot([]);
  }
  const database = await requireMongoDatabase();
  const rows = await entitlementCollection(database)
    .find({ userId, product: { $in: [...INFINITE_PRODUCTS] } }, { projection: { _id: 0 } })
    .toArray();
  return buildInfiniteAccessSnapshot(rows);
}

export async function syncInfiniteSubscriptionEntitlement({ userId, userEmail = null, status }) {
  if (!userId) {
    throw new Error("PromptRail Infinite entitlement is missing a user ID.");
  }

  const normalizedStatus = String(status || "inactive").toLowerCase();
  const database = await requireMongoDatabase();
  const now = new Date();
  await entitlementCollection(database).updateOne(
    { id: `product-entitlement:${userId}:infinite` },
    {
      $set: {
        id: `product-entitlement:${userId}:infinite`,
        userId,
        userEmail,
        product: "infinite",
        status: normalizedStatus,
        scopes: [...INFINITE_INFERENCE_SCOPES],
        source: "stripe_subscription",
        updatedAt: now,
      },
      $setOnInsert: { createdAt: now },
    },
    { upsert: true },
  );

  if (!ACTIVE_STATUSES.has(normalizedStatus)) {
    await database.collection(serverEnv("LEROUTER_API_KEY_COLLECTION", "api_keys")).updateMany(
      { userId, kind: "infinite", revokedAt: null },
      {
        $set: {
          revokedAt: now,
          revokedReason: "infinite_subscription_inactive",
          updatedAt: now,
        },
      },
    );
  }

  return getInfiniteAccess(userId);
}

export async function optIntoInfiniteBeta(user) {
  if (!user?.id) {
    throw new Error("PromptRail user identity is required.");
  }
  if (serverEnv("PROMPTRAIL_INFINITE_BETA_OPT_IN_ENABLED", "0") !== "1") {
    throw new Error("PromptRail Infinite activation is disabled.");
  }
  const pluginAccess = await getPluginAccess(user.id);
  if (!pluginAccess.allowed) {
    throw new Error("An active PromptRail subscription is required for Infinite.");
  }

  const database = await requireMongoDatabase();
  const now = new Date();
  await entitlementCollection(database).updateOne(
    { id: `product-entitlement:${user.id}:infinite_beta` },
    {
      $set: {
        id: `product-entitlement:${user.id}:infinite_beta`,
        userId: user.id,
        userEmail: user.email || null,
        product: "infinite_beta",
        status: "active",
        scopes: [...INFINITE_INFERENCE_SCOPES],
        source: "subscriber_beta_opt_in",
        updatedAt: now,
      },
      $setOnInsert: { createdAt: now },
    },
    { upsert: true },
  );
  return getInfiniteAccess(user.id);
}
