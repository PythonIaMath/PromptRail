import { pluginSubscriptionActive } from "./pluginAccess.js";

export const STRIPE_CHECKOUT_SESSION_TEMPLATE = "{CHECKOUT_SESSION_ID}";

export function pluginCheckoutMetadata({ interval, user = null }) {
  const metadata = {
    kind: "plugin_subscription",
    interval: interval === "year" ? "year" : "month",
    accountRequired: user?.id ? "false" : "true",
  };

  if (user?.id) {
    metadata.userId = user.id;
  }
  if (user?.email) {
    metadata.userEmail = user.email;
  }

  return metadata;
}

export function pluginCheckoutSuccessUrl(baseUrl) {
  return `${String(baseUrl).replace(/\/+$/g, "")}/plugins/onboarding?subscription=success&checkout_session_id=${STRIPE_CHECKOUT_SESSION_TEMPLATE}`;
}

function normalizedEmail(value) {
  return String(value || "").trim().toLowerCase();
}

function claimError(message, code, status) {
  const error = new Error(message);
  error.code = code;
  error.status = status;
  return error;
}

export function pluginCheckoutClaim(checkoutSession, user) {
  if (checkoutSession?.metadata?.kind !== "plugin_subscription") {
    throw claimError("This Stripe Checkout Session is not a PromptRail plugin purchase.", "invalid_checkout", 400);
  }
  if (checkoutSession.status !== "complete" || checkoutSession.payment_status !== "paid") {
    throw claimError("Stripe has not confirmed payment for this subscription.", "payment_not_complete", 409);
  }

  const subscription = checkoutSession.subscription;
  if (!subscription || typeof subscription === "string" || !subscription.id) {
    throw claimError("Stripe did not return the purchased subscription.", "subscription_missing", 409);
  }
  if (!pluginSubscriptionActive(subscription.status)) {
    throw claimError("The purchased subscription is not active.", "subscription_inactive", 409);
  }

  const expectedUserId = checkoutSession.client_reference_id || checkoutSession.metadata?.userId || null;
  if (expectedUserId && expectedUserId !== user?.id) {
    throw claimError("This purchase belongs to a different PromptRail account.", "account_mismatch", 403);
  }

  const purchaseEmail = normalizedEmail(
    checkoutSession.customer_details?.email
      || checkoutSession.customer_email
      || subscription.metadata?.userEmail,
  );
  const accountEmail = normalizedEmail(user?.email);
  if (!expectedUserId && (!purchaseEmail || purchaseEmail !== accountEmail)) {
    throw claimError(
      "Sign in with the same email address used during Stripe Checkout.",
      "email_mismatch",
      403,
    );
  }

  return {
    status: subscription.status,
    stripeCustomerId: typeof subscription.customer === "string"
      ? subscription.customer
      : subscription.customer?.id || null,
    stripeSubscriptionId: subscription.id,
    userEmail: user.email,
    userId: user.id,
  };
}
