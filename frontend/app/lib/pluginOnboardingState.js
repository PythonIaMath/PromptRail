export function pluginOnboardingView({
  access,
  accessLoading,
  hasCheckoutSession = false,
  hasUser,
  isSessionPending,
  isUrlPending = false,
  subscriptionState,
}) {
  if (isSessionPending || isUrlPending || (hasUser && accessLoading)) {
    return "loading";
  }

  if (hasUser && access?.allowed) {
    return "setup";
  }

  if (!hasUser && hasCheckoutSession && subscriptionState === "success") {
    return "accountRequired";
  }

  if (hasUser && subscriptionState === "success") {
    return "paymentPending";
  }

  return "pricing";
}
