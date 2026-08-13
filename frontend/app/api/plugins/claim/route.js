import { auth } from "../../../lib/auth.js";
import { pluginCheckoutClaim } from "../../../lib/pluginCheckout.js";
import { syncInfiniteSubscriptionEntitlement } from "../../../lib/infiniteAccess.js";
import {
  resolvePluginSubscriptionUserId,
  syncPluginSubscription,
} from "../../../lib/pluginAccess.js";
import { getStripe } from "../../../lib/stripe.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json().catch(() => ({}));
  const checkoutSessionId = String(body.checkoutSessionId || "").trim();
  if (!/^cs_(?:test_|live_)?[A-Za-z0-9]+$/.test(checkoutSessionId)) {
    return Response.json({ error: "Invalid Stripe Checkout Session ID." }, { status: 400 });
  }

  try {
    const checkoutSession = await getStripe().checkout.sessions.retrieve(checkoutSessionId, {
      expand: ["subscription"],
    });
    const claim = pluginCheckoutClaim(checkoutSession, session.user);
    const existingOwnerId = await resolvePluginSubscriptionUserId({
      userId: null,
      stripeSubscriptionId: claim.stripeSubscriptionId,
    });
    if (existingOwnerId && existingOwnerId !== session.user.id) {
      return Response.json(
        { error: "This Stripe subscription is already attached to another PromptRail account." },
        { status: 409 },
      );
    }

    const subscription = await getStripe().subscriptions.update(claim.stripeSubscriptionId, {
      metadata: {
        ...(checkoutSession.subscription.metadata || {}),
        accountRequired: "false",
        kind: "plugin_subscription",
        userEmail: session.user.email,
        userId: session.user.id,
      },
    });
    const access = await syncPluginSubscription({
      userId: session.user.id,
      userEmail: session.user.email,
      status: subscription.status,
      stripeCustomerId: typeof subscription.customer === "string"
        ? subscription.customer
        : subscription.customer?.id || claim.stripeCustomerId,
      stripeSubscriptionId: subscription.id,
      cancelAtPeriodEnd: subscription.cancel_at_period_end,
    });
    await syncInfiniteSubscriptionEntitlement({
      userId: session.user.id,
      userEmail: session.user.email,
      status: subscription.status,
    });

    return Response.json({ access });
  } catch (error) {
    return Response.json(
      {
        error: error.message || "Subscription activation failed.",
        code: error.code || "subscription_claim_failed",
      },
      { status: Number(error.status) || 400 },
    );
  }
}
