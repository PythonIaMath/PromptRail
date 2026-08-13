import { assertStripeProductionConfiguration, getStripe, stripeWebhookSecret } from "../../../lib/stripe.js";
import { syncInfiniteSubscriptionEntitlement } from "../../../lib/infiniteAccess.js";
import {
  resolvePluginSubscriptionUserId,
  syncPluginSubscription,
} from "../../../lib/pluginAccess.js";
import {
  creditAutoTopUpPaymentIntent,
  creditCheckoutSession as creditCheckoutSessionInStore,
  finishAutoTopUpAttempt,
  getCheckoutSession,
  upsertCheckoutSession,
} from "../../../lib/store.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function asNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function objectId(value) {
  if (!value) {
    return null;
  }

  return typeof value === "string" ? value : value.id || null;
}

function sessionUserId(checkoutSession, localSession) {
  return (
    localSession?.userId
    || checkoutSession.client_reference_id
    || checkoutSession.metadata?.userId
    || null
  );
}

function sessionCreditUsd(checkoutSession, localSession) {
  if (localSession?.amountUsd) {
    return Number(localSession.amountUsd);
  }

  const metadataCredit = asNumber(checkoutSession.metadata?.creditUsd, 0);
  if (metadataCredit > 0) {
    return metadataCredit;
  }

  return asNumber(checkoutSession.amount_total, 0) / 100;
}

function paymentIntentUserId(paymentIntent) {
  return paymentIntent.metadata?.userId || null;
}

function paymentIntentCreditUsd(paymentIntent) {
  const metadataCredit = asNumber(paymentIntent.metadata?.creditUsd, 0);
  if (metadataCredit > 0) {
    return metadataCredit;
  }

  return asNumber(paymentIntent.amount_received || paymentIntent.amount, 0) / 100;
}

function isPluginSubscription(value) {
  return value?.metadata?.kind === "plugin_subscription";
}

async function syncStripePluginSubscription(subscription, fallbackUserId = null) {
  const userId = await resolvePluginSubscriptionUserId({
    userId: subscription.metadata?.userId || fallbackUserId,
    stripeSubscriptionId: subscription.id,
  });
  if (!userId) {
    if (subscription.metadata?.accountRequired === "true") {
      return null;
    }
    throw new Error("Stripe plugin subscription is missing PromptRail user metadata.");
  }

  const access = await syncPluginSubscription({
    userId,
    userEmail: subscription.metadata?.userEmail || null,
    status: subscription.status,
    stripeCustomerId: objectId(subscription.customer),
    stripeSubscriptionId: subscription.id,
    cancelAtPeriodEnd: subscription.cancel_at_period_end,
  });
  await syncInfiniteSubscriptionEntitlement({
    userId,
    userEmail: subscription.metadata?.userEmail || null,
    status: subscription.status,
  });
  return access;
}

async function completePluginSubscriptionCheckout(checkoutSession) {
  const subscriptionId = objectId(checkoutSession.subscription);
  if (!subscriptionId) {
    throw new Error("Plugin Checkout Session is missing a Stripe subscription.");
  }
  const subscription = await getStripe().subscriptions.retrieve(subscriptionId);
  return syncStripePluginSubscription(
    subscription,
    checkoutSession.client_reference_id || checkoutSession.metadata?.userId,
  );
}

async function checkoutPaymentIntent(checkoutSession) {
  const paymentIntentId = objectId(checkoutSession.payment_intent);

  if (!paymentIntentId) {
    return null;
  }

  return getStripe().paymentIntents.retrieve(paymentIntentId);
}

async function upsertSessionStatus(checkoutSession, status) {
  const now = new Date().toISOString();
  const localSession = await getCheckoutSession(checkoutSession.id);
  const userId = sessionUserId(checkoutSession, localSession);

  if (!userId) {
    throw new Error("Checkout session is missing user metadata.");
  }

  const amountUsd = sessionCreditUsd(checkoutSession, localSession);
  const amountCents = Math.round(amountUsd * 100);

  await upsertCheckoutSession({
    id: checkoutSession.id,
    userId,
    amountUsd,
    amountCents,
    currency: String(checkoutSession.currency || "usd").toLowerCase(),
    status,
    paymentStatus: checkoutSession.payment_status || null,
    stripeCustomerId: objectId(checkoutSession.customer),
    stripePaymentIntentId: objectId(checkoutSession.payment_intent),
    creditedAt: localSession?.creditedAt || null,
    createdAt: localSession?.createdAt || now,
    updatedAt: now,
  });
}

async function creditCheckoutSession(checkoutSession) {
  const localSession = await getCheckoutSession(checkoutSession.id);
  const userId = sessionUserId(checkoutSession, localSession);

  if (!userId) {
    throw new Error("Checkout session is missing user metadata.");
  }

  const amountUsd = sessionCreditUsd(checkoutSession, localSession);
  if (amountUsd <= 0) {
    throw new Error("Checkout session has no credit amount.");
  }

  const paymentIntent = await checkoutPaymentIntent(checkoutSession);
  const stripePaymentMethodId = typeof paymentIntent?.payment_method === "string"
    ? paymentIntent.payment_method
    : paymentIntent?.payment_method?.id || null;

  return creditCheckoutSessionInStore({
    checkoutSession,
    userId,
    amountUsd,
    stripeCustomerId: objectId(checkoutSession.customer),
    stripePaymentIntentId: objectId(checkoutSession.payment_intent),
    stripePaymentMethodId,
    status: checkoutSession.status || "complete",
    paymentStatus: checkoutSession.payment_status || "paid",
    currency: checkoutSession.currency,
  });
}

async function creditAutoTopUp(paymentIntent) {
  if (paymentIntent.metadata?.kind !== "auto_top_up") {
    return null;
  }

  const userId = paymentIntentUserId(paymentIntent);
  const amountUsd = paymentIntentCreditUsd(paymentIntent);

  if (!userId) {
    throw new Error("Auto top-up PaymentIntent is missing user metadata.");
  }

  if (amountUsd <= 0) {
    throw new Error("Auto top-up PaymentIntent has no credit amount.");
  }

  return creditAutoTopUpPaymentIntent({
    paymentIntent,
    userId,
    amountUsd,
  });
}

async function markAutoTopUpFailed(paymentIntent) {
  if (paymentIntent.metadata?.kind !== "auto_top_up") {
    return null;
  }

  const userId = paymentIntentUserId(paymentIntent);
  if (!userId) {
    throw new Error("Auto top-up PaymentIntent is missing user metadata.");
  }

  await upsertCheckoutSession({
    id: paymentIntent.id,
    userId,
    amountUsd: paymentIntentCreditUsd(paymentIntent),
    amountCents: paymentIntent.amount || 0,
    currency: paymentIntent.currency,
    status: paymentIntent.status || "failed",
    paymentStatus: paymentIntent.status || "failed",
    stripeCustomerId: objectId(paymentIntent.customer),
    stripePaymentIntentId: paymentIntent.id,
    stripePaymentMethodId: objectId(paymentIntent.payment_method),
    kind: "auto_top_up",
    metadata: paymentIntent.metadata || {},
    creditedAt: null,
    createdAt: new Date(),
    updatedAt: new Date(),
  });

  await finishAutoTopUpAttempt(userId, {
    autoTopUpLastFailure: paymentIntent.last_payment_error?.message || `Stripe returned ${paymentIntent.status}.`,
  });

  return { failed: true };
}

export async function POST(request) {
  const signature = request.headers.get("stripe-signature");
  const webhookSecret = stripeWebhookSecret();

  if (!webhookSecret) {
    return Response.json({ error: "STRIPE_WEBHOOK_SECRET is not configured." }, { status: 500 });
  }

  if (!signature) {
    return Response.json({ error: "Missing Stripe signature." }, { status: 400 });
  }

  let event;
  try {
    const rawBody = await request.text();
    assertStripeProductionConfiguration(new URL(request.url).origin);
    event = getStripe().webhooks.constructEvent(rawBody, signature, webhookSecret);
  } catch (error) {
    return Response.json({ error: `Webhook signature verification failed: ${error.message}` }, { status: 400 });
  }

  try {
    if (
      event.type === "checkout.session.completed"
      || event.type === "checkout.session.async_payment_succeeded"
    ) {
      const checkoutSession = event.data.object;
      if (isPluginSubscription(checkoutSession)) {
        await completePluginSubscriptionCheckout(checkoutSession);
      } else if (checkoutSession.payment_status === "paid") {
        await creditCheckoutSession(checkoutSession);
      } else {
        await upsertSessionStatus(checkoutSession, checkoutSession.status || "complete");
      }
    }

    if (
      event.type === "checkout.session.expired"
      || event.type === "checkout.session.async_payment_failed"
    ) {
      if (!isPluginSubscription(event.data.object)) {
        await upsertSessionStatus(event.data.object, event.data.object.status || "failed");
      }
    }

    if (
      event.type === "customer.subscription.created"
      || event.type === "customer.subscription.updated"
      || event.type === "customer.subscription.deleted"
    ) {
      if (isPluginSubscription(event.data.object)) {
        await syncStripePluginSubscription(event.data.object);
      }
    }

    if (event.type === "payment_intent.succeeded") {
      await creditAutoTopUp(event.data.object);
    }

    if (event.type === "payment_intent.payment_failed") {
      await markAutoTopUpFailed(event.data.object);
    }
  } catch (error) {
    return Response.json({ error: error.message || "Webhook handling failed." }, { status: 400 });
  }

  return Response.json({ received: true });
}
