export const ROUTING_FEE_RATE = 0.03;

export function routingFeeUsd(finalRequestSpendUsd) {
  const amount = Number(finalRequestSpendUsd);
  if (!Number.isFinite(amount) || amount < 0) {
    throw new Error("Final request spend must be a non-negative number.");
  }
  return Math.round(amount * ROUTING_FEE_RATE * 100000000) / 100000000;
}

export function usageBillingMetadata(finalRequestSpendUsd) {
  const routingFee = routingFeeUsd(finalRequestSpendUsd);
  return {
    finalRequestSpendUsd: Number(finalRequestSpendUsd),
    routingFeeUsd: routingFee,
    routingFeeRate: ROUTING_FEE_RATE,
  };
}
