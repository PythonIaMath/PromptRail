import { auth } from "../../../lib/auth.js";
import { pluginCheckoutMetadata, pluginCheckoutSuccessUrl } from "../../../lib/pluginCheckout.js";
import { getPluginAccess, pluginSubscriptionPriceUsd } from "../../../lib/pluginAccess.js";
import { assertStripeProductionConfiguration, getStripe, stripeCurrency } from "../../../lib/stripe.js";
import { ensureUserDefaults } from "../../../lib/store.js";
import { serverEnv } from "../../../lib/serverEnv.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function appBaseUrl(request) {
  const configured = serverEnv("NEXT_PUBLIC_APP_URL", serverEnv("BETTER_AUTH_URL"));
  const baseUrl = new URL(configured || new URL(request.url).origin);
  if (baseUrl.hostname === "promptrail.ai") {
    baseUrl.hostname = "www.promptrail.ai";
  }
  return baseUrl.origin;
}

export async function POST(request) {
  const session = await auth.api.getSession({ headers: request.headers });
  const access = session?.user ? await getPluginAccess(session.user.id) : null;
  if (access?.tier === "subscriber") {
    return Response.json({ error: "This account already has an active subscription." }, { status: 409 });
  }
  const body = await request.json().catch(() => ({}));
  const interval = body.interval === "year" ? "year" : "month";

  const user = session?.user ? await ensureUserDefaults(session.user) : null;
  if (session?.user && !user) {
    return Response.json({ error: "User not found." }, { status: 404 });
  }

  const amountCents = Math.round(pluginSubscriptionPriceUsd(interval) * 100);
  const baseUrl = appBaseUrl(request);
  const metadata = pluginCheckoutMetadata({ interval, user });
  assertStripeProductionConfiguration(baseUrl);
  const checkout = await getStripe().checkout.sessions.create({
    mode: "subscription",
    customer: access?.stripeCustomerId || user?.stripeCustomerId || undefined,
    customer_email: user && !access?.stripeCustomerId && !user.stripeCustomerId ? user.email : undefined,
    client_reference_id: user?.id || undefined,
    metadata,
    subscription_data: {
      metadata,
    },
    line_items: [{
      price_data: {
        currency: stripeCurrency(),
        product_data: { name: "PromptRail Plugins" },
        recurring: { interval },
        unit_amount: amountCents,
      },
      quantity: 1,
    }],
    success_url: pluginCheckoutSuccessUrl(baseUrl),
    cancel_url: `${baseUrl}/plugins/onboarding?subscription=cancelled`,
  });

  return Response.json({ id: checkout.id, url: checkout.url });
}
