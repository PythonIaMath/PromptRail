import { createHash } from "node:crypto";
import { ObjectId } from "mongodb";
import { requireMongoCollections } from "./mongo.js";
import { userBudgetDefaults } from "./routeSchemas.js";

export function apiKeyHash(value) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

export function serializeDocument(document) {
  if (!document) {
    return null;
  }

  const { _id, ...rest } = document;
  return JSON.parse(JSON.stringify(rest));
}

function dateFrom(value) {
  if (!value) {
    return null;
  }
  return value instanceof Date ? value : new Date(value);
}

function iso(value) {
  return dateFrom(value)?.toISOString() || null;
}

function asOptionalNumber(value) {
  if (value === undefined || value === null || value === "") {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function requestWeightFromMetadata(metadata, modelId) {
  const direct = asOptionalNumber(metadata?.request_weight ?? metadata?.requestWeight);
  if (direct !== null) {
    return direct;
  }

  const ranked = Array.isArray(metadata?.budget_ranked_candidates)
    ? metadata.budget_ranked_candidates
    : [];
  const selected = ranked.find((model) => {
    const candidateId = model?.model_id || model?.modelId || model?.model;
    return candidateId && modelId && String(candidateId) === String(modelId);
  });
  return asOptionalNumber(selected?.request_weight ?? selected?.requestWeight);
}

function publicUser(user) {
  if (!user) {
    return null;
  }

  return {
    id: user.id || String(user._id || ""),
    name: user.name,
    email: user.email,
    emailVerified: Boolean(user.emailVerified),
    image: user.image || null,
    routeId: user.routeId || userBudgetDefaults.routeId,
    budgetUsd: Number(user.budgetUsd || 0),
    budgetRemainingUsd: Number(user.budgetRemainingUsd || 0),
    remainingWeight: user.remainingWeight ?? null,
    totalPredictedWeight: user.totalPredictedWeight ?? null,
    medianWeightedTokens: user.medianWeightedTokens ?? null,
    averageRequestsPerPeriod: user.averageRequestsPerPeriod ?? null,
    outputTokenWeight: user.outputTokenWeight ?? null,
    requestWeightBeta: user.requestWeightBeta ?? null,
    requestDifficultyAlpha: user.requestDifficultyAlpha ?? null,
    requestWeightMin: user.requestWeightMin ?? null,
    requestWeightCapMultiplier: user.requestWeightCapMultiplier ?? null,
    difficultyWeightAlpha: user.difficultyWeightAlpha ?? null,
    budgetShadowPrice: user.budgetShadowPrice ?? userBudgetDefaults.budgetShadowPrice,
    budgetControllerLearningRate:
      user.budgetControllerLearningRate ?? userBudgetDefaults.budgetControllerLearningRate,
    budgetShadowPriceMin: user.budgetShadowPriceMin ?? userBudgetDefaults.budgetShadowPriceMin,
    budgetShadowPriceMax: user.budgetShadowPriceMax ?? userBudgetDefaults.budgetShadowPriceMax,
    budgetCycleDays: Number(user.budgetCycleDays || userBudgetDefaults.budgetCycleDays),
    budgetElapsedDays: Number(user.budgetElapsedDays || 0),
    budgetCycleStartedAt: iso(user.budgetCycleStartedAt),
    lastRequestAt: iso(user.lastRequestAt),
    totalRequests: Number(user.totalRequests || 0),
    totalSpendUsd: Number(user.totalSpendUsd || 0),
    successfulRequests: Number(user.successfulRequests || 0),
    failedRequests: Number(user.failedRequests || 0),
    successRate: Number(user.successRate || 0),
    providerCompletions: user.providerCompletions || {},
    labCompletions: user.labCompletions || {},
    stripeCustomerId: user.stripeCustomerId || null,
    stripePaymentMethodId: user.stripePaymentMethodId || null,
    autoTopUpEnabled: Boolean(user.autoTopUpEnabled),
    autoTopUpThresholdUsd: Number(user.autoTopUpThresholdUsd || 5),
    autoTopUpAmountUsd: Number(user.autoTopUpAmountUsd || 25),
    autoTopUpLastAttemptAt: iso(user.autoTopUpLastAttemptAt),
    autoTopUpLastSucceededAt: iso(user.autoTopUpLastSucceededAt),
    autoTopUpLastFailure: user.autoTopUpLastFailure || null,
    createdAt: iso(user.createdAt),
    updatedAt: iso(user.updatedAt),
  };
}

function objectIdFromString(value) {
  const text = String(value || "");
  return ObjectId.isValid(text) ? new ObjectId(text) : null;
}

async function findUserDocument(collections, userId, email = null) {
  const objectId = objectIdFromString(userId);
  if (objectId) {
    const byObjectId = await collections.users.findOne({ _id: objectId });
    if (byObjectId) {
      return byObjectId;
    }
  }

  const byLegacyId = await collections.users.findOne({ id: userId });
  if (byLegacyId) {
    return byLegacyId;
  }

  if (email) {
    return collections.users.findOne({ email });
  }

  return null;
}

async function userDocumentFilter(collections, userId, email = null) {
  const existing = await findUserDocument(collections, userId, email);
  return existing?._id ? { _id: existing._id } : { id: userId };
}

function labFromModelId(modelId) {
  if (!modelId) {
    return null;
  }

  const owner = String(modelId).toLowerCase().split("/", 1)[0].replace(/^~/, "");
  const labels = {
    "ai21": "AI21",
    "amazon": "Amazon",
    "anthropic": "Anthropic",
    "cohere": "Cohere",
    "deepseek": "DeepSeek",
    "deepseek-ai": "DeepSeek",
    "google": "Google",
    "meta": "Meta",
    "meta-llama": "Meta",
    "microsoft": "Microsoft",
    "mistral": "Mistral",
    "mistralai": "Mistral",
    "moonshot": "Moonshot",
    "moonshotai": "Moonshot",
    "nvidia": "NVIDIA",
    "openai": "OpenAI",
    "qwen": "Qwen",
    "qwenlm": "Qwen",
    "x-ai": "xAI",
    "z-ai": "Z.ai",
  };

  return labels[owner] || owner || null;
}

function normalizeUserForSet(input = {}) {
  const next = {};
  for (const [key, value] of Object.entries(input)) {
    if (value !== undefined) {
      next[key] = value;
    }
  }

  for (const key of ["budgetCycleStartedAt", "lastRequestAt", "createdAt", "updatedAt"]) {
    if (next[key]) {
      next[key] = dateFrom(next[key]);
    }
  }

  return next;
}

export async function ensureUserDefaults(user) {
  if (!user?.id) {
    return null;
  }

  const collections = await requireMongoCollections();
  const now = new Date();
  const routeId = user.routeId || userBudgetDefaults.routeId;
  const defaults = {
    budgetUsd: userBudgetDefaults.budgetUsd,
    budgetRemainingUsd: userBudgetDefaults.budgetRemainingUsd,
    remainingWeight: userBudgetDefaults.remainingWeight,
    totalPredictedWeight: userBudgetDefaults.totalPredictedWeight,
    medianWeightedTokens: userBudgetDefaults.medianWeightedTokens,
    averageRequestsPerPeriod: userBudgetDefaults.averageRequestsPerPeriod,
    outputTokenWeight: userBudgetDefaults.outputTokenWeight,
    requestWeightBeta: userBudgetDefaults.requestWeightBeta,
    requestDifficultyAlpha: userBudgetDefaults.requestDifficultyAlpha,
    requestWeightMin: userBudgetDefaults.requestWeightMin,
    requestWeightCapMultiplier: userBudgetDefaults.requestWeightCapMultiplier,
    difficultyWeightAlpha: userBudgetDefaults.difficultyWeightAlpha,
    budgetShadowPrice: userBudgetDefaults.budgetShadowPrice,
    budgetControllerLearningRate: userBudgetDefaults.budgetControllerLearningRate,
    budgetShadowPriceMin: userBudgetDefaults.budgetShadowPriceMin,
    budgetShadowPriceMax: userBudgetDefaults.budgetShadowPriceMax,
    budgetCycleDays: userBudgetDefaults.budgetCycleDays,
    budgetElapsedDays: userBudgetDefaults.budgetElapsedDays,
    totalRequests: userBudgetDefaults.totalRequests,
    totalSpendUsd: userBudgetDefaults.totalSpendUsd,
    successfulRequests: userBudgetDefaults.successfulRequests,
    failedRequests: userBudgetDefaults.failedRequests,
    successRate: userBudgetDefaults.successRate,
    providerCompletions: userBudgetDefaults.providerCompletions,
    labCompletions: userBudgetDefaults.labCompletions,
    autoTopUpEnabled: false,
    autoTopUpThresholdUsd: 5,
    autoTopUpAmountUsd: 25,
  };

  const filter = await userDocumentFilter(collections, user.id, user.email);

  await collections.users.updateOne(
    filter,
    {
      $setOnInsert: {
        id: user.id,
        createdAt: now,
        ...defaults,
      },
      $set: {
        email: user.email,
        name: user.name || user.email?.split("@")[0] || "PromptRail user",
        emailVerified: Boolean(user.emailVerified),
        routeId,
        updatedAt: now,
      },
    },
    { upsert: true },
  );

  const stored = await findUserDocument(collections, user.id, user.email);
  return publicUser(stored);
}

export async function getUser(userId) {
  const collections = await requireMongoCollections();
  return publicUser(await findUserDocument(collections, userId));
}

export async function updateUser(userId, set) {
  const collections = await requireMongoCollections();
  const filter = await userDocumentFilter(collections, userId);
  await collections.users.updateOne(
    filter,
    { $set: normalizeUserForSet({ ...set, updatedAt: new Date() }) },
  );
  return getUser(userId);
}

export async function updateAutoTopUpSettings(user, settings) {
  const collections = await requireMongoCollections();
  const current = await ensureUserDefaults(user);

  if (!current) {
    throw new Error("User not found.");
  }

  const now = new Date();
  const enabled = Boolean(settings.enabled);
  const thresholdUsd = Number(settings.thresholdUsd);
  const amountUsd = Number(settings.amountUsd);

  if (!Number.isFinite(thresholdUsd) || thresholdUsd < 1 || thresholdUsd > 1000) {
    throw new Error("Auto top-up threshold must be between $1 and $1000.");
  }

  if (!Number.isFinite(amountUsd) || amountUsd < 5 || amountUsd > 1000) {
    throw new Error("Auto top-up amount must be between $5 and $1000.");
  }

  if (amountUsd <= thresholdUsd) {
    throw new Error("Auto top-up amount must be greater than the threshold.");
  }

  const userFilter = await userDocumentFilter(collections, user.id, user.email);
  const nextSettings = {
    autoTopUpEnabled: enabled,
    autoTopUpThresholdUsd: Math.round(thresholdUsd * 100) / 100,
    autoTopUpAmountUsd: Math.round(amountUsd * 100) / 100,
    autoTopUpLastFailure: null,
    updatedAt: now,
  };

  await collections.users.updateOne(
    userFilter,
    { $set: nextSettings },
  );

  await collections.userBudgets.updateOne(
    { "loginInfo.userId": user.id },
    { $set: { ...nextSettings, "loginInfo.email": user.email } },
  );

  return getUser(user.id);
}

export async function getBillingSummary(userId) {
  const collections = await requireMongoCollections();
  const [paidRow] = await collections.checkoutSessions.aggregate([
    { $match: { userId, creditedAt: { $ne: null } } },
    { $group: { _id: null, totalCreditsUsd: { $sum: "$amountUsd" }, paymentCount: { $sum: 1 } } },
  ]).toArray();
  const [pendingRow] = await collections.checkoutSessions.aggregate([
    { $match: { userId, creditedAt: null, status: { $nin: ["expired", "failed"] } } },
    { $group: { _id: null, pendingCreditsUsd: { $sum: "$amountUsd" } } },
  ]).toArray();
  const recentPayments = (await collections.checkoutSessions
    .find({ userId }, { projection: { _id: 0 } })
    .sort({ createdAt: -1 })
    .limit(5)
    .toArray())
    .map(serializeDocument);

  return {
    totalCreditsUsd: Number(paidRow?.totalCreditsUsd || 0),
    pendingCreditsUsd: Number(pendingRow?.pendingCreditsUsd || 0),
    paymentCount: Number(paidRow?.paymentCount || 0),
    recentPayments,
  };
}

export async function updateUserBudget({ user, budget, planType = "monthly" }) {
  const collections = await requireMongoCollections();
  const current = await ensureUserDefaults(user);
  const currentBudgetRemainingUsd = Number(current?.budgetRemainingUsd || 0);
  const now = new Date();
  const userFilter = await userDocumentFilter(collections, user.id, user.email);

  await collections.users.updateOne(
    userFilter,
    {
      $set: {
        routeId: budget.routeId,
        budgetUsd: budget.budgetUsd,
        budgetRemainingUsd: currentBudgetRemainingUsd,
        remainingWeight: budget.remainingWeight,
        totalPredictedWeight: budget.totalPredictedWeight,
        medianWeightedTokens: budget.medianWeightedTokens,
        averageRequestsPerPeriod: budget.averageRequestsPerPeriod,
        outputTokenWeight: budget.outputTokenWeight,
        requestWeightBeta: budget.requestWeightBeta,
        requestDifficultyAlpha: budget.requestDifficultyAlpha,
        requestWeightMin: budget.requestWeightMin,
        requestWeightCapMultiplier: budget.requestWeightCapMultiplier,
        difficultyWeightAlpha: budget.difficultyWeightAlpha,
        budgetShadowPrice: budget.budgetShadowPrice,
        budgetControllerLearningRate: budget.budgetControllerLearningRate,
        budgetShadowPriceMin: budget.budgetShadowPriceMin,
        budgetShadowPriceMax: budget.budgetShadowPriceMax,
        budgetCycleDays: budget.budgetCycleDays,
        budgetElapsedDays: budget.budgetElapsedDays,
        budgetCycleStartedAt: dateFrom(budget.budgetCycleStartedAt),
        updatedAt: now,
      },
    },
  );

  await collections.userBudgets.updateOne(
    { "loginInfo.userId": user.id, routeId: budget.routeId },
    {
      $set: {
        loginInfo: {
          userId: user.id,
          email: user.email,
          provider: "better-auth-email",
        },
        routeId: budget.routeId,
        planType,
        budgetUsd: budget.budgetUsd,
        budgetRemainingUsd: currentBudgetRemainingUsd,
        remainingWeight: budget.remainingWeight,
        totalPredictedWeight: budget.totalPredictedWeight,
        medianWeightedTokens: budget.medianWeightedTokens,
        averageRequestsPerPeriod: budget.averageRequestsPerPeriod,
        outputTokenWeight: budget.outputTokenWeight,
        requestWeightBeta: budget.requestWeightBeta,
        requestDifficultyAlpha: budget.requestDifficultyAlpha,
        requestWeightMin: budget.requestWeightMin,
        requestWeightCapMultiplier: budget.requestWeightCapMultiplier,
        difficultyWeightAlpha: budget.difficultyWeightAlpha,
        budgetShadowPrice: budget.budgetShadowPrice,
        budgetControllerLearningRate: budget.budgetControllerLearningRate,
        budgetShadowPriceMin: budget.budgetShadowPriceMin,
        budgetShadowPriceMax: budget.budgetShadowPriceMax,
        timeStampDays: budget.budgetCycleDays,
        timeSpendDays: budget.budgetElapsedDays,
        updatedAt: now,
      },
      $setOnInsert: {
        createdAt: now,
        successfulRequests: 0,
        failedRequests: 0,
        successRate: 0,
        providerCompletions: {},
        labCompletions: {},
      },
    },
    { upsert: true },
  );

  return {
    ...budget,
    budgetRemainingUsd: currentBudgetRemainingUsd,
  };
}

export async function findMongoBudgetUser(user) {
  const collections = await requireMongoCollections();
  return serializeDocument(await collections.userBudgets.findOne({
    routeId: user.routeId || userBudgetDefaults.routeId,
    $or: [
      { "loginInfo.userId": user.id },
      { "loginInfo.email": user.email },
    ],
  }));
}

export async function listApiKeys(userId) {
  const collections = await requireMongoCollections();
  return (await collections.apiKeys
    .find({ userId }, { projection: { _id: 0, keyHash: 0 } })
    .sort({ createdAt: -1 })
    .toArray())
    .map(serializeDocument)
    .map((row) => ({
      ...row,
      displayKey: row.displayKey || `${row.keyPrefix}...${row.keySuffix}`,
    }));
}

export async function insertApiKey({ row, key, user }) {
  const collections = await requireMongoCollections();
  await collections.apiKeys.insertOne({
    ...row,
    userId: user.id,
    userEmail: user.email,
    displayKey: `${row.keyPrefix}...${row.keySuffix}`,
    createdAt: dateFrom(row.createdAt),
    updatedAt: dateFrom(row.updatedAt),
  });
  return {
    key,
    apiKey: {
      id: row.id,
      name: row.name,
      keyPrefix: row.keyPrefix,
      keySuffix: row.keySuffix,
      routeId: row.routeId,
      revokedAt: row.revokedAt,
      lastUsedAt: row.lastUsedAt,
      createdAt: row.createdAt,
      displayKey: `${row.keyPrefix}...${row.keySuffix}`,
    },
  };
}

export async function revokeApiKey({ userId, keyId }) {
  const collections = await requireMongoCollections();
  const now = new Date();
  const result = await collections.apiKeys.updateOne(
    { id: keyId, userId, revokedAt: null },
    { $set: { revokedAt: now, updatedAt: now } },
  );
  return result.modifiedCount > 0;
}

export async function renameApiKey({ userId, keyId, name }) {
  const collections = await requireMongoCollections();
  const now = new Date();
  const result = await collections.apiKeys.findOneAndUpdate(
    { id: keyId, userId },
    { $set: { name, updatedAt: now } },
    {
      projection: { _id: 0, keyHash: 0 },
      returnDocument: "after",
    },
  );

  const row = result?.value || result;
  if (!row) {
    return null;
  }

  return {
    ...serializeDocument(row),
    displayKey: row.displayKey || `${row.keyPrefix}...${row.keySuffix}`,
  };
}

export async function getApiKeyAccess(tokenHash) {
  const collections = await requireMongoCollections();
  const now = new Date();
  const apiKey = await collections.apiKeys.findOne({ keyHash: tokenHash, revokedAt: null });
  if (!apiKey?.userId) {
    return null;
  }

  await collections.apiKeys.updateOne(
    { id: apiKey.id },
    { $set: { lastUsedAt: now, updatedAt: now } },
  );

  const storedUser = await getUser(apiKey.userId);
  const user = storedUser
    ? { ...storedUser, routeId: apiKey.routeId || storedUser.routeId || "default" }
    : {
        id: apiKey.userId,
        email: apiKey.userEmail,
        name: apiKey.userEmail?.split("@")[0] || "PromptRail user",
        emailVerified: true,
        routeId: apiKey.routeId || "default",
      };
  if (!user) {
    return null;
  }

  return {
    type: "api_key",
    keyId: apiKey.id,
    routeId: apiKey.routeId || "default",
    user,
  };
}

export async function getRoutePolicy({ userId, routeId }) {
  const collections = await requireMongoCollections();
  const policy = await collections.routePolicies.findOne(
    { routeId, $or: [{ userId }, { userId: null }, { userId: { $exists: false } }] },
    { projection: { _id: 0 } },
  );
  return serializeDocument(policy);
}

export async function upsertRoutePolicy({ user, policy }) {
  const collections = await requireMongoCollections();
  const now = new Date();

  await collections.routePolicies.updateOne(
    { routeId: policy.routeId },
    {
      $set: {
        routeId: policy.routeId,
        userId: user.id,
        routes: policy.routes,
        routeDefinitions: policy.routeDefinitions || {},
        metadata: policy.metadata || {},
        updatedAt: now,
      },
      $setOnInsert: {
        createdAt: now,
      },
    },
    { upsert: true },
  );
  const userFilter = await userDocumentFilter(collections, user.id, user.email);
  await collections.users.updateOne(
    userFilter,
    { $set: { routeId: policy.routeId, updatedAt: now } },
  );
  await collections.apiKeys.updateMany(
    { userId: user.id, revokedAt: null },
    { $set: { routeId: policy.routeId, updatedAt: now } },
  );
  await collections.userBudgets.updateOne(
    { "loginInfo.userId": user.id, routeId: policy.routeId },
    {
      $set: {
        "loginInfo.userId": user.id,
        "loginInfo.email": user.email,
        routeId: policy.routeId,
        updatedAt: now,
      },
      $setOnInsert: {
        budgetUsd: Number(user.budgetUsd || 0),
        budgetRemainingUsd: Number(user.budgetRemainingUsd || user.budgetUsd || 0),
        remainingWeight: user.remainingWeight ?? null,
        totalPredictedWeight: user.totalPredictedWeight ?? null,
        medianWeightedTokens: user.medianWeightedTokens ?? null,
        averageRequestsPerPeriod: user.averageRequestsPerPeriod ?? null,
        outputTokenWeight: user.outputTokenWeight ?? null,
        requestWeightBeta: user.requestWeightBeta ?? null,
        requestDifficultyAlpha: user.requestDifficultyAlpha ?? null,
        requestWeightMin: user.requestWeightMin ?? null,
        requestWeightCapMultiplier: user.requestWeightCapMultiplier ?? null,
        difficultyWeightAlpha: user.difficultyWeightAlpha ?? null,
        budgetShadowPrice: user.budgetShadowPrice ?? userBudgetDefaults.budgetShadowPrice,
        budgetControllerLearningRate:
          user.budgetControllerLearningRate ?? userBudgetDefaults.budgetControllerLearningRate,
        budgetShadowPriceMin: user.budgetShadowPriceMin ?? userBudgetDefaults.budgetShadowPriceMin,
        budgetShadowPriceMax: user.budgetShadowPriceMax ?? userBudgetDefaults.budgetShadowPriceMax,
        timeStampDays: Number(user.budgetCycleDays || 30),
        timeSpendDays: Number(user.budgetElapsedDays || 0),
        totalRequests: 0,
        totalSpendUsd: 0,
        successfulRequests: 0,
        failedRequests: 0,
        successRate: 0,
        providerCompletions: {},
        labCompletions: {},
        planType: "monthly",
        createdAt: now,
      },
    },
    { upsert: true },
  );

  return {
    ...policy,
    userId: user.id,
    updatedAt: now.toISOString(),
  };
}

export async function listUsageLogs({ userId, limit, includeInternal = false }) {
  const collections = await requireMongoCollections();
  const query = includeInternal
    ? { userId }
    : {
        userId,
        "metadata.kind": { $ne: "routing_operation" },
        "metadata.status": { $ne: "started" },
      };
  return (await collections.usageLogs
    .find(query, { projection: { _id: 0 } })
    .sort({ createdAt: -1 })
    .limit(limit)
    .toArray())
    .map(serializeDocument);
}

export async function insertUsageAndUpdateUser({
  user,
  routeId,
  routeName,
  provider,
  modelId,
  success,
  spendUsd,
  metadata,
  isRoutingOperation,
}) {
  const collections = await requireMongoCollections();
  const current = await getUser(user.id);
  if (!current) {
    throw new Error("User not found.");
  }

  if (spendUsd > 0 && Number(current.budgetRemainingUsd || 0) + 0.000001 < spendUsd) {
    const error = new Error("Insufficient PromptRail credits.");
    error.status = 402;
    error.details = {
      creditBalanceUsd: Number(current.budgetRemainingUsd || 0),
      requiredUsd: spendUsd,
    };
    throw error;
  }

  const now = new Date();
  const providerCompletions = current.providerCompletions || {};
  const labCompletions = current.labCompletions || {};
  const modelLab = labFromModelId(modelId);
  if (!isRoutingOperation) {
    providerCompletions[provider] = Number(providerCompletions[provider] || 0) + 1;
    if (modelLab) {
      labCompletions[modelLab] = Number(labCompletions[modelLab] || 0) + 1;
    }
  }

  const nextBudgetRemainingUsd = Math.max(0, Number(current.budgetRemainingUsd || 0) - spendUsd);
  const currentRemainingWeight = asOptionalNumber(current.remainingWeight);
  const requestWeight = requestWeightFromMetadata(metadata, modelId);
  const nextRemainingWeight = currentRemainingWeight !== null && requestWeight !== null
    ? Math.max(0, currentRemainingWeight - requestWeight)
    : currentRemainingWeight;
  const nextTotalSpendUsd = Number(current.totalSpendUsd || 0) + spendUsd;
  const nextTotalRequests = Number(current.totalRequests || 0) + (isRoutingOperation ? 0 : 1);
  const nextSuccessfulRequests = Number(current.successfulRequests || 0) + (!isRoutingOperation && success ? 1 : 0);
  const nextFailedRequests = Number(current.failedRequests || 0) + (!isRoutingOperation && !success ? 1 : 0);
  const nextSuccessRate = nextTotalRequests ? nextSuccessfulRequests / nextTotalRequests : 0;
  const nextBudgetElapsedDays = Math.max(1, Number(current.budgetElapsedDays || 0));

  await collections.usageLogs.insertOne({
    id: crypto.randomUUID(),
    userId: user.id,
    userEmail: user.email,
    routeId,
    routeName,
    provider,
    modelId,
    success,
    spendUsd,
    budgetRemainingUsd: nextBudgetRemainingUsd,
    metadata,
    createdAt: now,
  });

  if (isRoutingOperation) {
    return {
      routeId,
      routeName,
      provider,
      modelId,
      success,
      spendUsd,
      budgetRemainingUsd: Number(current.budgetRemainingUsd || 0),
      remainingWeight: asOptionalNumber(current.remainingWeight),
      totalSpendUsd: Number(current.totalSpendUsd || 0),
      totalRequests: Number(current.totalRequests || 0),
      successfulRequests: Number(current.successfulRequests || 0),
      failedRequests: Number(current.failedRequests || 0),
      successRate: Number(current.successRate || 0),
      providerCompletions,
      labCompletions,
      budgetElapsedDays: nextBudgetElapsedDays,
      createdAt: now.toISOString(),
    };
  }

  const userFilter = await userDocumentFilter(collections, user.id, user.email);
  await collections.users.updateOne(
    userFilter,
    {
      $set: {
        budgetRemainingUsd: nextBudgetRemainingUsd,
        remainingWeight: nextRemainingWeight,
        totalSpendUsd: nextTotalSpendUsd,
        totalRequests: nextTotalRequests,
        successfulRequests: nextSuccessfulRequests,
        failedRequests: nextFailedRequests,
        successRate: nextSuccessRate,
        providerCompletions,
        labCompletions,
        budgetElapsedDays: nextBudgetElapsedDays,
        lastRequestAt: now,
        updatedAt: now,
      },
    },
  );

  await collections.userBudgets.updateOne(
    { "loginInfo.userId": user.id, routeId },
    {
      $set: {
        loginInfo: {
          userId: user.id,
          email: user.email,
          provider: "better-auth-email",
        },
        routeId,
        budgetRemainingUsd: nextBudgetRemainingUsd,
        remainingWeight: nextRemainingWeight,
        successfulRequests: nextSuccessfulRequests,
        failedRequests: nextFailedRequests,
        successRate: nextSuccessRate,
        providerCompletions,
        labCompletions,
        updatedAt: now,
      },
      $max: {
        timeSpendDays: 1,
      },
      $inc: {
        totalRequests: isRoutingOperation ? 0 : 1,
        totalSpendUsd: spendUsd,
      },
      $setOnInsert: {
        budgetUsd: Number(current.budgetUsd || 0),
        timeStampDays: Number(current.budgetCycleDays || 30),
        createdAt: now,
      },
    },
    { upsert: true },
  );

  return {
    routeId,
    routeName,
    provider,
    modelId,
    success,
    spendUsd,
    budgetRemainingUsd: nextBudgetRemainingUsd,
    remainingWeight: nextRemainingWeight,
    totalSpendUsd: nextTotalSpendUsd,
    totalRequests: nextTotalRequests,
    successfulRequests: nextSuccessfulRequests,
    failedRequests: nextFailedRequests,
    successRate: nextSuccessRate,
    providerCompletions,
    labCompletions,
    budgetElapsedDays: nextBudgetElapsedDays,
    createdAt: now.toISOString(),
  };
}

export async function upsertCheckoutSession(checkoutSession) {
  const collections = await requireMongoCollections();
  await collections.checkoutSessions.updateOne(
    { id: checkoutSession.id },
    {
      $set: {
        ...checkoutSession,
        createdAt: dateFrom(checkoutSession.createdAt),
        updatedAt: dateFrom(checkoutSession.updatedAt),
        creditedAt: dateFrom(checkoutSession.creditedAt),
      },
    },
    { upsert: true },
  );
}

export async function getCheckoutSession(id) {
  const collections = await requireMongoCollections();
  return serializeDocument(await collections.checkoutSessions.findOne({ id }, { projection: { _id: 0 } }));
}

export async function beginAutoTopUpAttempt(userId) {
  const collections = await requireMongoCollections();
  const current = await getUser(userId);

  if (!current?.autoTopUpEnabled) {
    return null;
  }

  const thresholdUsd = Number(current.autoTopUpThresholdUsd || 0);
  const amountUsd = Number(current.autoTopUpAmountUsd || 0);
  const balanceUsd = Number(current.budgetRemainingUsd || 0);

  if (balanceUsd > thresholdUsd) {
    return null;
  }

  if (!current.stripeCustomerId || !current.stripePaymentMethodId) {
    await updateUser(userId, {
      autoTopUpLastFailure: "Add credits once with Stripe before auto top-up can charge a saved payment method.",
      autoTopUpLastAttemptAt: new Date(),
    });
    return null;
  }

  const now = new Date();
  const staleBefore = new Date(now.getTime() - 15 * 60 * 1000);
  const userFilter = await userDocumentFilter(collections, userId, current.email);
  const result = await collections.users.updateOne(
    {
      ...userFilter,
      autoTopUpEnabled: true,
      budgetRemainingUsd: { $lte: thresholdUsd },
      $or: [
        { autoTopUpInFlightAt: { $exists: false } },
        { autoTopUpInFlightAt: null },
        { autoTopUpInFlightAt: { $lt: staleBefore } },
      ],
    },
    {
      $set: {
        autoTopUpInFlightAt: now,
        autoTopUpLastAttemptAt: now,
        autoTopUpLastFailure: null,
        updatedAt: now,
      },
    },
  );

  if (!result.modifiedCount) {
    return null;
  }

  return {
    user: current,
    amountUsd,
    thresholdUsd,
    balanceUsd,
  };
}

export async function finishAutoTopUpAttempt(userId, set = {}) {
  const collections = await requireMongoCollections();
  const current = await getUser(userId);
  const userFilter = await userDocumentFilter(collections, userId, current?.email);
  await collections.users.updateOne(
    userFilter,
    {
      $unset: { autoTopUpInFlightAt: "" },
      $set: normalizeUserForSet({ ...set, updatedAt: new Date() }),
    },
  );
}

export async function creditStripePayment({
  id,
  userId,
  amountUsd,
  amountCents,
  stripeCustomerId,
  stripePaymentIntentId,
  stripePaymentMethodId,
  status,
  paymentStatus,
  currency,
  kind = "manual_top_up",
  metadata = {},
}) {
  const collections = await requireMongoCollections();
  const existingSession = await getCheckoutSession(id);

  if (existingSession?.creditedAt) {
    return { credited: false, userId, amountUsd: Number(existingSession.amountUsd || 0) };
  }

  const current = await getUser(userId);
  if (!current) {
    throw new Error("Checkout session user was not found.");
  }

  const now = new Date();
  const nextBudgetUsd = Number(current.budgetUsd || 0) + amountUsd;
  const nextBudgetRemainingUsd = Number(current.budgetRemainingUsd || 0) + amountUsd;

  await upsertCheckoutSession({
    id,
    userId,
    amountUsd,
    amountCents: amountCents || Math.round(amountUsd * 100),
    currency: String(currency || "usd").toLowerCase(),
    status: status || "complete",
    paymentStatus: paymentStatus || "paid",
    stripeCustomerId,
    stripePaymentIntentId,
    stripePaymentMethodId,
    kind,
    metadata,
    creditedAt: now,
    createdAt: existingSession?.createdAt || now,
    updatedAt: now,
  });

  const userFilter = await userDocumentFilter(collections, userId);
  await collections.users.updateOne(
    userFilter,
    {
      $set: {
        budgetUsd: nextBudgetUsd,
        budgetRemainingUsd: nextBudgetRemainingUsd,
        ...(stripeCustomerId ? { stripeCustomerId } : {}),
        ...(stripePaymentMethodId ? { stripePaymentMethodId } : {}),
        ...(kind === "auto_top_up" ? { autoTopUpLastSucceededAt: now, autoTopUpLastFailure: null } : {}),
        updatedAt: now,
      },
      ...(kind === "auto_top_up" ? { $unset: { autoTopUpInFlightAt: "" } } : {}),
    },
  );

  await collections.userBudgets.updateOne(
    { "loginInfo.userId": userId },
    {
      $set: {
        budgetUsd: nextBudgetUsd,
        budgetRemainingUsd: nextBudgetRemainingUsd,
        ...(stripeCustomerId ? { stripeCustomerId } : {}),
        ...(stripePaymentMethodId ? { stripePaymentMethodId } : {}),
        updatedAt: now,
      },
    },
  );

  return {
    credited: true,
    userId,
    amountUsd,
    budgetUsd: nextBudgetUsd,
    budgetRemainingUsd: nextBudgetRemainingUsd,
  };
}

export async function creditCheckoutSession({ checkoutSession, userId, amountUsd, stripeCustomerId, stripePaymentIntentId, stripePaymentMethodId, status, paymentStatus, currency }) {
  return creditStripePayment({
    id: checkoutSession.id,
    userId,
    amountUsd,
    amountCents: Math.round(amountUsd * 100),
    stripeCustomerId,
    stripePaymentIntentId,
    stripePaymentMethodId,
    status,
    paymentStatus,
    currency,
    kind: "manual_top_up",
  });
}

export async function creditAutoTopUpPaymentIntent({ paymentIntent, userId, amountUsd }) {
  const stripePaymentMethodId = typeof paymentIntent.payment_method === "string"
    ? paymentIntent.payment_method
    : paymentIntent.payment_method?.id || null;

  return creditStripePayment({
    id: paymentIntent.id,
    userId,
    amountUsd,
    amountCents: paymentIntent.amount_received || paymentIntent.amount || Math.round(amountUsd * 100),
    stripeCustomerId: typeof paymentIntent.customer === "string" ? paymentIntent.customer : paymentIntent.customer?.id || null,
    stripePaymentIntentId: paymentIntent.id,
    stripePaymentMethodId,
    status: paymentIntent.status,
    paymentStatus: paymentIntent.status === "succeeded" ? "paid" : paymentIntent.status,
    currency: paymentIntent.currency,
    kind: "auto_top_up",
    metadata: paymentIntent.metadata || {},
  });
}

export async function insertSetupLink({ id, tokenHash, userId, instruction, metadata = {} }) {
  const collections = await requireMongoCollections();
  const now = new Date();

  await collections.setupLinks.insertOne({
    id,
    tokenHash,
    userId,
    instruction,
    metadata,
    createdAt: now,
    updatedAt: now,
  });

  return { id, createdAt: now.toISOString() };
}

export async function getSetupLinkInstruction({ id, tokenHash }) {
  const collections = await requireMongoCollections();
  const setupLink = await collections.setupLinks.findOne(
    { id, tokenHash },
    { projection: { _id: 0, instruction: 1 } },
  );

  return setupLink?.instruction || "";
}

export async function createDeviceSession({
  id,
  deviceCodeHash,
  userCode,
  product,
  deviceName,
  detectedHarnesses,
  expiresAt,
}) {
  const collections = await requireMongoCollections();
  const now = new Date();
  await collections.deviceSessions.insertOne({
    id,
    deviceCodeHash,
    userCode,
    product,
    deviceName,
    detectedHarnesses,
    status: "pending",
    userId: null,
    userEmail: null,
    approvedAt: null,
    consumedAt: null,
    expiresAt: dateFrom(expiresAt),
    createdAt: now,
    updatedAt: now,
  });
}

export async function getDeviceSessionByUserCode(userCode) {
  const collections = await requireMongoCollections();
  return serializeDocument(await collections.deviceSessions.findOne(
    { userCode },
    { projection: { _id: 0, deviceCodeHash: 0 } },
  ));
}

export async function decideDeviceSession({ userCode, user, approved }) {
  const collections = await requireMongoCollections();
  const now = new Date();
  const result = await collections.deviceSessions.findOneAndUpdate(
    {
      userCode,
      status: "pending",
      consumedAt: null,
      expiresAt: { $gt: now },
    },
    {
      $set: {
        status: approved ? "approved" : "denied",
        userId: user.id,
        userEmail: user.email,
        approvedAt: approved ? now : null,
        updatedAt: now,
      },
    },
    { returnDocument: "after", projection: { _id: 0, deviceCodeHash: 0 } },
  );
  return serializeDocument(result?.value || result);
}

export async function consumeApprovedDeviceSession({ deviceCodeHash, installTokenHash, installTokenExpiresAt }) {
  const collections = await requireMongoCollections();
  const now = new Date();
  const result = await collections.deviceSessions.findOneAndUpdate(
    {
      deviceCodeHash,
      status: "approved",
      consumedAt: null,
      expiresAt: { $gt: now },
    },
    {
      $set: {
        status: "consumed",
        consumedAt: now,
        installTokenHash,
        installTokenExpiresAt: dateFrom(installTokenExpiresAt),
        updatedAt: now,
      },
    },
    { returnDocument: "after", projection: { _id: 0, deviceCodeHash: 0, installTokenHash: 0 } },
  );
  return serializeDocument(result?.value || result);
}

export async function getDeviceSessionStatus(deviceCodeHash) {
  const collections = await requireMongoCollections();
  const row = await collections.deviceSessions.findOne(
    { deviceCodeHash },
    { projection: { _id: 0, deviceCodeHash: 0, installTokenHash: 0 } },
  );
  return serializeDocument(row);
}

export async function consumeInstallToken(installTokenHash) {
  const collections = await requireMongoCollections();
  const now = new Date();
  const result = await collections.deviceSessions.findOneAndUpdate(
    {
      installTokenHash,
      installTokenExpiresAt: { $gt: now },
      installTokenConsumedAt: null,
      userId: { $ne: null },
    },
    {
      $set: {
        installTokenConsumedAt: now,
        updatedAt: now,
      },
    },
    { returnDocument: "after", projection: { _id: 0, deviceCodeHash: 0, installTokenHash: 0 } },
  );
  return serializeDocument(result?.value || result);
}

export async function saveInstallation({ user, manifest, apiKeyId }) {
  const collections = await requireMongoCollections();
  const now = new Date();
  await collections.installations.updateOne(
    { userId: user.id, routeId: manifest.route_id, harness: manifest.harness },
    {
      $set: {
        userEmail: user.email,
        workspaceName: manifest.workspace_name,
        routeId: manifest.route_id,
        harness: manifest.harness,
        inferenceMode: manifest.inference_mode,
        budget: manifest.budget,
        routeRefreshInterval: manifest.route_refresh_interval,
        apiKeyId,
        updatedAt: now,
      },
      $setOnInsert: {
        id: manifest.installation_id,
        userId: user.id,
        createdAt: now,
      },
    },
    { upsert: true },
  );
}
