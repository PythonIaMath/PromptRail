import { beginAutoTopUpAttempt, creditAutoTopUpPaymentIntent, finishAutoTopUpAttempt, upsertCheckoutSession } from "./store.js";
import { getStripe, stripeCurrency } from "./stripe.js";

export async function triggerAutoTopUp(userId) {
  const attempt = await beginAutoTopUpAttempt(userId);

  if (!attempt) {
    return { triggered: false };
  }

  const stripe = getStripe();
  const amountCents = Math.round(attempt.amountUsd * 100);
  const currency = stripeCurrency();
  const now = new Date();

  try {
    const paymentIntent = await stripe.paymentIntents.create(
      {
        amount: amountCents,
        currency,
        customer: attempt.user.stripeCustomerId,
        payment_method: attempt.user.stripePaymentMethodId,
        off_session: true,
        confirm: true,
        description: "PromptRail auto top-up credits",
        metadata: {
          kind: "auto_top_up",
          userId,
          creditUsd: attempt.amountUsd.toFixed(2),
          thresholdUsd: attempt.thresholdUsd.toFixed(2),
          balanceUsd: attempt.balanceUsd.toFixed(2),
        },
      },
      {
        idempotencyKey: `auto_top_up_${userId}_${Math.floor(now.getTime() / 900000)}_${amountCents}`,
      },
    );

    await upsertCheckoutSession({
      id: paymentIntent.id,
      userId,
      amountUsd: attempt.amountUsd,
      amountCents,
      currency,
      status: paymentIntent.status,
      paymentStatus: paymentIntent.status === "succeeded" ? "paid" : paymentIntent.status,
      stripeCustomerId: attempt.user.stripeCustomerId,
      stripePaymentIntentId: paymentIntent.id,
      stripePaymentMethodId: attempt.user.stripePaymentMethodId,
      kind: "auto_top_up",
      metadata: paymentIntent.metadata || {},
      creditedAt: null,
      createdAt: now,
      updatedAt: now,
    });

    if (paymentIntent.status === "succeeded") {
      await creditAutoTopUpPaymentIntent({
        paymentIntent,
        userId,
        amountUsd: attempt.amountUsd,
      });
    } else {
      await finishAutoTopUpAttempt(userId, {
        autoTopUpLastFailure: `Stripe returned ${paymentIntent.status}.`,
      });
    }

    return { triggered: true, paymentIntentId: paymentIntent.id, status: paymentIntent.status };
  } catch (error) {
    await finishAutoTopUpAttempt(userId, {
      autoTopUpLastFailure: error.message || "Auto top-up failed.",
    });
    return { triggered: false, error: error.message || "Auto top-up failed." };
  }
}
