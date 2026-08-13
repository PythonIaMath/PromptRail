export const PLAN_DAYS = {
  daily: 1,
  weekly: 7,
  monthly: 30,
  quarterly: 91,
  yearly: 365,
};

export const DEFAULT_ROUTE_ID = "default";

export const userBudgetDefaults = {
  routeId: DEFAULT_ROUTE_ID,
  budgetUsd: 0,
  budgetRemainingUsd: 0,
  remainingWeight: null,
  totalPredictedWeight: null,
  medianWeightedTokens: null,
  averageRequestsPerPeriod: null,
  outputTokenWeight: null,
  requestWeightBeta: null,
  requestDifficultyAlpha: null,
  requestWeightMin: null,
  requestWeightCapMultiplier: null,
  difficultyWeightAlpha: null,
  budgetShadowPrice: 0.75,
  budgetControllerLearningRate: 10,
  budgetShadowPriceMin: 0,
  budgetShadowPriceMax: 100,
  budgetCycleDays: PLAN_DAYS.monthly,
  budgetElapsedDays: 0,
  totalRequests: 0,
  totalSpendUsd: 0,
  successfulRequests: 0,
  failedRequests: 0,
  successRate: 0,
  providerCompletions: {},
  labCompletions: {},
};

export const userAdditionalFields = {
  routeId: {
    type: "string",
    required: false,
    defaultValue: userBudgetDefaults.routeId,
  },
  budgetUsd: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.budgetUsd,
  },
  budgetRemainingUsd: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.budgetRemainingUsd,
  },
  remainingWeight: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.remainingWeight,
  },
  totalPredictedWeight: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.totalPredictedWeight,
  },
  medianWeightedTokens: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.medianWeightedTokens,
  },
  averageRequestsPerPeriod: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.averageRequestsPerPeriod,
  },
  outputTokenWeight: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.outputTokenWeight,
  },
  requestWeightBeta: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.requestWeightBeta,
  },
  requestDifficultyAlpha: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.requestDifficultyAlpha,
  },
  requestWeightMin: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.requestWeightMin,
  },
  requestWeightCapMultiplier: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.requestWeightCapMultiplier,
  },
  difficultyWeightAlpha: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.difficultyWeightAlpha,
  },
  budgetShadowPrice: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.budgetShadowPrice,
  },
  budgetControllerLearningRate: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.budgetControllerLearningRate,
  },
  budgetShadowPriceMin: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.budgetShadowPriceMin,
  },
  budgetShadowPriceMax: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.budgetShadowPriceMax,
  },
  budgetCycleDays: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.budgetCycleDays,
  },
  budgetElapsedDays: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.budgetElapsedDays,
  },
  budgetCycleStartedAt: {
    type: "date",
    required: false,
  },
  lastRequestAt: {
    type: "date",
    required: false,
    input: false,
  },
  totalRequests: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.totalRequests,
    input: false,
  },
  totalSpendUsd: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.totalSpendUsd,
    input: false,
  },
  successfulRequests: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.successfulRequests,
    input: false,
  },
  failedRequests: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.failedRequests,
    input: false,
  },
  successRate: {
    type: "number",
    required: false,
    defaultValue: userBudgetDefaults.successRate,
    input: false,
  },
  providerCompletions: {
    type: "json",
    required: false,
    defaultValue: userBudgetDefaults.providerCompletions,
    input: false,
  },
  labCompletions: {
    type: "json",
    required: false,
    defaultValue: userBudgetDefaults.labCompletions,
    input: false,
  },
};

function asNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function optionalNumber(value) {
  if (value === undefined || value === null || value === "") {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function normalizePlanType(planType = "monthly") {
  const normalized = String(planType).toLowerCase();
  return PLAN_DAYS[normalized] ? normalized : "monthly";
}

export function getPlanDays(planType = "monthly") {
  return PLAN_DAYS[normalizePlanType(planType)];
}

export function normalizeUserBudgetInput(input = {}) {
  const planType = normalizePlanType(input.planType);
  const budgetUsd = Math.max(0, asNumber(input.budgetUsd ?? input.budget, 0));
  const budgetRemainingUsd = Math.min(
    budgetUsd,
    Math.max(0, asNumber(input.budgetRemainingUsd ?? input.budgetRemaining, budgetUsd)),
  );
  const budgetShadowPrice = asNumber(
    input.budgetShadowPrice ?? input.budget_shadow_price,
    userBudgetDefaults.budgetShadowPrice,
  );
  const budgetControllerLearningRate = asNumber(
    input.budgetControllerLearningRate ?? input.budget_controller_learning_rate,
    userBudgetDefaults.budgetControllerLearningRate,
  );
  const budgetShadowPriceMin = asNumber(
    input.budgetShadowPriceMin ?? input.budget_shadow_price_min,
    userBudgetDefaults.budgetShadowPriceMin,
  );
  const budgetShadowPriceMax = asNumber(
    input.budgetShadowPriceMax ?? input.budget_shadow_price_max,
    userBudgetDefaults.budgetShadowPriceMax,
  );
  if (
    budgetControllerLearningRate < 0
    || budgetShadowPriceMin < 0
    || budgetShadowPriceMax < budgetShadowPriceMin
    || budgetShadowPrice < budgetShadowPriceMin
    || budgetShadowPrice > budgetShadowPriceMax
  ) {
    throw new Error("Budget shadow-price controller values are inconsistent.");
  }

  return {
    routeId: String(input.routeId || DEFAULT_ROUTE_ID),
    budgetUsd,
    budgetRemainingUsd,
    remainingWeight: optionalNumber(input.remainingWeight ?? input.remaining_weight),
    totalPredictedWeight: optionalNumber(input.totalPredictedWeight ?? input.total_predicted_weight),
    medianWeightedTokens: optionalNumber(input.medianWeightedTokens ?? input.median_weighted_tokens),
    averageRequestsPerPeriod: optionalNumber(
      input.averageRequestsPerPeriod
        ?? input.average_requests_per_period
        ?? input.requestCountLastTimestamp
        ?? input.request_count_last_timestamp
        ?? input.numberOfRequestsLastTimestamp
        ?? input.number_of_request_last_timestamp,
    ),
    outputTokenWeight: optionalNumber(input.outputTokenWeight ?? input.output_token_weight ?? input.k_out),
    requestWeightBeta: optionalNumber(input.requestWeightBeta ?? input.request_weight_beta ?? input.beta),
    requestDifficultyAlpha: optionalNumber(input.requestDifficultyAlpha ?? input.request_difficulty_alpha),
    requestWeightMin: optionalNumber(input.requestWeightMin ?? input.request_weight_min ?? input.r_min),
    requestWeightCapMultiplier: optionalNumber(
      input.requestWeightCapMultiplier ?? input.request_weight_cap_multiplier ?? input.cap_multiplier,
    ),
    difficultyWeightAlpha: optionalNumber(input.difficultyWeightAlpha ?? input.difficulty_weight_alpha ?? input.alpha),
    budgetShadowPrice,
    budgetControllerLearningRate,
    budgetShadowPriceMin,
    budgetShadowPriceMax,
    budgetCycleDays: Math.max(1, asNumber(input.budgetCycleDays, getPlanDays(planType))),
    budgetElapsedDays: Math.max(0, asNumber(input.budgetElapsedDays ?? input.timeSpendDays, 0)),
    budgetCycleStartedAt: input.budgetCycleStartedAt || new Date().toISOString(),
  };
}

export function normalizeRoutePolicyInput(input = {}) {
  const routeId = String(input.routeId || DEFAULT_ROUTE_ID).trim();
  const routes = input.routes || input.route || {};
  const routeDefinitions = input.routeDefinitions || input.route_definitions || {};
  const metadata = input.metadata && typeof input.metadata === "object" && !Array.isArray(input.metadata)
    ? input.metadata
    : {};

  if (!routeId) {
    throw new Error("routeId is required.");
  }

  if (!routes || Array.isArray(routes) || typeof routes !== "object") {
    throw new Error("routes must be an object: { routeName: [modelId, ...] }.");
  }

  const normalizedRoutes = {};
  for (const [routeName, models] of Object.entries(routes)) {
    const cleanRouteName = String(routeName).trim();
    if (!cleanRouteName) {
      continue;
    }

    if (!Array.isArray(models)) {
      throw new Error(`Route "${cleanRouteName}" must be a model list.`);
    }

    normalizedRoutes[cleanRouteName] = models
      .map((model) => String(model).trim())
      .filter(Boolean);
  }

  const normalizedRouteDefinitions = {};
  if (routeDefinitions && typeof routeDefinitions === "object" && !Array.isArray(routeDefinitions)) {
    for (const [routeName, definition] of Object.entries(routeDefinitions)) {
      const cleanRouteName = String(routeName).trim();
      if (!cleanRouteName || !normalizedRoutes[cleanRouteName]) {
        continue;
      }
      if (definition && typeof definition === "object" && !Array.isArray(definition)) {
        const publicDefinition = { ...definition };
        delete publicDefinition.models;
        delete publicDefinition.model_ids;
        delete publicDefinition.candidate_models;
        normalizedRouteDefinitions[cleanRouteName] = publicDefinition;
      } else if (typeof definition === "string") {
        normalizedRouteDefinitions[cleanRouteName] = {
          trigger: definition,
          task: definition,
        };
      }
    }
  }

  return {
    routeId,
    routes: normalizedRoutes,
    routeDefinitions: normalizedRouteDefinitions,
    metadata,
  };
}
