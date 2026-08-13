import { serverEnv } from "./serverEnv.js";
import { requireMongoDatabase } from "./mongo.js";

const ACTIVE_SUBSCRIPTION_STATUSES = new Set(["active", "trialing"]);

function pluginAccessCollection(database) {
  return database.collection(serverEnv("LEROUTER_PLUGIN_ACCESS_COLLECTION", "plugin_access"));
}

function apiKeyCollection(database) {
  return database.collection(serverEnv("LEROUTER_API_KEY_COLLECTION", "api_keys"));
}

export function pluginSubscriptionPrices() {
  const monthly = Number(serverEnv("LEROUTER_PLUGIN_SUBSCRIPTION_MONTHLY_USD", "10"));
  const yearly = Number(serverEnv("LEROUTER_PLUGIN_SUBSCRIPTION_YEARLY_USD", "100"));
  if (![monthly, yearly].every((value) => Number.isFinite(value) && value > 0)) {
    throw new Error("Plugin subscription prices must be positive.");
  }
  return {
    monthly: Math.round(monthly * 100) / 100,
    yearly: Math.round(yearly * 100) / 100,
  };
}

export function pluginSubscriptionPriceUsd(interval = "month") {
  const value = pluginSubscriptionPrices()[interval === "year" ? "yearly" : "monthly"];
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error("Plugin subscription price must be positive.");
  }
  return Math.round(value * 100) / 100;
}

export function pluginSubscriptionActive(status) {
  return ACTIVE_SUBSCRIPTION_STATUSES.has(String(status || "").toLowerCase());
}

export function buildPluginAccessSnapshot({ counter, subscription, userId = null }) {
  const subscriber = pluginSubscriptionActive(subscription?.status);

  return {
    allowed: subscriber,
    tier: subscriber ? "subscriber" : "none",
    subscriptionStatus: subscription?.status || null,
    stripeCustomerId: subscription?.stripeCustomerId || null,
    stripeSubscriptionId: subscription?.stripeSubscriptionId || null,
    cancelAtPeriodEnd: Boolean(subscription?.cancelAtPeriodEnd),
    prices: pluginSubscriptionPrices(),
  };
}

async function subscriptionForUser(database, userId) {
  if (!userId) {
    return null;
  }
  return pluginAccessCollection(database).findOne({
    id: `plugin-subscription:${userId}`,
    type: "subscription",
  });
}

export async function getPluginAccess(userId = null) {
  const database = await requireMongoDatabase();
  return buildPluginAccessSnapshot({
    counter: null,
    subscription: await subscriptionForUser(database, userId),
    userId,
  });
}

export async function syncPluginSubscription({
  userId,
  userEmail = null,
  status,
  stripeCustomerId,
  stripeSubscriptionId,
  cancelAtPeriodEnd = false,
}) {
  if (!userId) {
    throw new Error("Plugin subscription is missing a PromptRail user ID.");
  }

  const database = await requireMongoDatabase();
  const now = new Date();
  await pluginAccessCollection(database).updateOne(
    { id: `plugin-subscription:${userId}` },
    {
      $set: {
        id: `plugin-subscription:${userId}`,
        type: "subscription",
        userId,
        userEmail,
        status: String(status || "inactive"),
        stripeCustomerId: stripeCustomerId || null,
        stripeSubscriptionId: stripeSubscriptionId || null,
        cancelAtPeriodEnd: Boolean(cancelAtPeriodEnd),
        updatedAt: now,
      },
      $setOnInsert: { createdAt: now },
    },
    { upsert: true },
  );

  if (!pluginSubscriptionActive(status)) {
    await apiKeyCollection(database).updateMany(
      {
        userId,
        revokedAt: null,
        $or: [{ kind: "plugin" }, { routeId: /^plugin_/ }],
      },
      {
        $set: {
          revokedAt: now,
          revokedReason: "plugin_subscription_inactive",
          updatedAt: now,
        },
      },
    );
  }

  return getPluginAccess(userId);
}

export async function resolvePluginSubscriptionUserId({ userId, stripeSubscriptionId }) {
  if (userId) {
    return userId;
  }
  if (!stripeSubscriptionId) {
    return null;
  }
  const database = await requireMongoDatabase();
  const row = await pluginAccessCollection(database).findOne({
    type: "subscription",
    stripeSubscriptionId,
  });
  return row?.userId || null;
}
