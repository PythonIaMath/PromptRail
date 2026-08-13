import { auth } from "../../../lib/auth.js";
import { getStripe, stripeCurrency } from "../../../lib/stripe.js";
import { serverEnv } from "../../../lib/serverEnv.js";
import { ensureUserDefaults, upsertCheckoutSession } from "../../../lib/store.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MIN_TOP_UP_USD = 5;
const MAX_TOP_UP_USD = 1000;

async function getSession(request) {
  return auth.api.getSession({
    headers: request.headers,
  });
}

function appBaseUrl(request) {
  const configured = serverEnv("NEXT_PUBLIC_APP_URL", serverEnv("BETTER_AUTH_URL"));
  if (configured) {
    return configured.replace(/\/+$/g, "");
  }

  const url = new URL(request.url);
  return `${url.protocol}//${url.host}`;
}

function normalizeAmountUsd(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    throw new Error("Amount must be a number.");
  }

  const rounded = Math.round(parsed * 100) / 100;
  if (rounded < MIN_TOP_UP_USD || rounded > MAX_TOP_UP_USD) {
    throw new Error(`Amount must be between $${MIN_TOP_UP_USD} and $${MAX_TOP_UP_USD}.`);
  }

  return rounded;
}

export async function POST(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  let amountUsd;
  try {
    const body = await request.json();
    amountUsd = normalizeAmountUsd(body.amountUsd);
  } catch (error) {
    return Response.json({ error: error.message || "Invalid top-up amount." }, { status: 400 });
  }

  const user = await ensureUserDefaults(session.user);

  if (!user) {
    return Response.json({ error: "User not found." }, { status: 404 });
  }

  const stripe = getStripe();
  const baseUrl = appBaseUrl(request);
  const amountCents = Math.round(amountUsd * 100);
  const currency = stripeCurrency();
  const now = new Date().toISOString();

  const checkoutSession = await stripe.checkout.sessions.create({
    mode: "payment",
    customer: user.stripeCustomerId || undefined,
    customer_creation: user.stripeCustomerId ? undefined : "always",
    customer_email: user.stripeCustomerId ? undefined : user.email,
    client_reference_id: session.user.id,
    metadata: {
      userId: session.user.id,
      creditUsd: amountUsd.toFixed(2),
      kind: "manual_top_up",
    },
    payment_intent_data: {
      setup_future_usage: "off_session",
      metadata: {
        userId: session.user.id,
        creditUsd: amountUsd.toFixed(2),
        kind: "manual_top_up",
      },
    },
    line_items: [
      {
        price_data: {
          currency,
          product_data: {
            name: "PromptRail credits",
          },
          unit_amount: amountCents,
        },
        quantity: 1,
      },
    ],
    success_url: `${baseUrl}/dashboard?credits=success&session_id={CHECKOUT_SESSION_ID}`,
    cancel_url: `${baseUrl}/dashboard?credits=cancelled`,
  });

  await upsertCheckoutSession({
    id: checkoutSession.id,
    userId: session.user.id,
    amountUsd,
    amountCents,
    currency,
    status: checkoutSession.status || "open",
    paymentStatus: checkoutSession.payment_status || null,
    creditedAt: null,
    createdAt: now,
    updatedAt: now,
  });

  return Response.json({
    id: checkoutSession.id,
    url: checkoutSession.url,
  });
}
