export const PLAN_DAYS = {
  daily: 1,
  day: 1,
  weekly: 7,
  week: 7,
  monthly: 30,
  month: 30,
  quarterly: 91,
  quarter: 91,
  yearly: 365,
  annual: 365,
  year: 365,
};

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function readNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function getPlanTotalDays(planType, totalDays) {
  const explicitTotalDays = readNumber(totalDays, 0);
  if (explicitTotalDays > 0) {
    return explicitTotalDays;
  }

  const normalizedPlan = String(planType || "monthly").toLowerCase();
  return PLAN_DAYS[normalizedPlan] || PLAN_DAYS.monthly;
}

function getElapsedDays({ elapsedDays, startedAt, now }) {
  const explicitElapsedDays = readNumber(elapsedDays, null);
  if (explicitElapsedDays !== null) {
    return Math.max(0, explicitElapsedDays);
  }

  if (!startedAt) {
    return 0;
  }

  const startDate = new Date(startedAt);
  const nowDate = now ? new Date(now) : new Date();
  const elapsedMs = nowDate.getTime() - startDate.getTime();

  if (!Number.isFinite(elapsedMs)) {
    return 0;
  }

  return Math.max(0, elapsedMs / 86_400_000);
}

export function calculateBudgetAdjustment({
  planType = "monthly",
  budget,
  remainingBudget,
  modelGrade,
  pricePerMillionTokens,
  hyperParam = 1,
  elapsedDays,
  totalDays,
  startedAt,
  now,
  minGrade,
  maxGrade,
} = {}) {
  const normalizedBudget = Math.max(0, readNumber(budget, 0));
  const normalizedRemainingBudget = clamp(
    readNumber(remainingBudget, normalizedBudget),
    0,
    normalizedBudget,
  );
  const grade = readNumber(modelGrade, 0);
  const totalPlanDays = getPlanTotalDays(planType, totalDays);
  const elapsedPlanDays = getElapsedDays({ elapsedDays, startedAt, now });
  const elapsedRatio = clamp(elapsedPlanDays / totalPlanDays, 0, 1);

  const targetSpend = normalizedBudget * elapsedRatio;
  const actualSpend = normalizedBudget - normalizedRemainingBudget;
  const spendDeltaRatio = normalizedBudget
    ? clamp((actualSpend - targetSpend) / normalizedBudget, -1, 1)
    : 0;

  const a = Math.abs(spendDeltaRatio);
  const q = Math.max(0, readNumber(pricePerMillionTokens, 0))
    * Math.max(0, readNumber(hyperParam, 1));
  const adjustment = q * a;
  const direction = spendDeltaRatio > 0 ? "penalty" : spendDeltaRatio < 0 ? "bonus" : "neutral";
  const rawFinalGrade = direction === "penalty" ? grade - adjustment : grade + adjustment;

  const lowerBound = minGrade === undefined ? -Infinity : readNumber(minGrade, -Infinity);
  const upperBound = maxGrade === undefined ? Infinity : readNumber(maxGrade, Infinity);
  const finalGrade = clamp(rawFinalGrade, lowerBound, upperBound);

  return {
    planType,
    totalDays: totalPlanDays,
    elapsedDays: elapsedPlanDays,
    elapsedRatio,
    budget: normalizedBudget,
    remainingBudget: normalizedRemainingBudget,
    targetSpend,
    actualSpend,
    spendDeltaRatio,
    a,
    q,
    direction,
    adjustment,
    originalGrade: grade,
    finalGrade,
  };
}

export default calculateBudgetAdjustment;
