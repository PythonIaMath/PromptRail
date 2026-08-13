import Stripe from "stripe";
import { serverEnv } from "./serverEnv.js";

let stripeClient;
let pluginPortalConfigurationPromise;

export function getStripe() {
  const secretKey = serverEnv("STRIPE_SECRET_KEY");

  if (!secretKey) {
    throw new Error("STRIPE_SECRET_KEY is not configured.");
  }
  if (process.env.NODE_ENV === "production" && secretKey.startsWith("sk_test_")) {
    throw new Error("Production Stripe is configured with a test-mode secret key.");
  }

  if (!stripeClient) {
    stripeClient = new Stripe(secretKey);
  }

  return stripeClient;
}

export function stripeWebhookSecret() {
  return serverEnv("STRIPE_WEBHOOK_SECRET");
}

export function assertStripeProductionConfiguration(baseUrl = "") {
  if (process.env.NODE_ENV !== "production") {
    return;
  }

  const secretKey = serverEnv("STRIPE_SECRET_KEY");
  if (secretKey.startsWith("sk_test_")) {
    throw new Error("Production Stripe is configured with a test-mode secret key.");
  }
  if (!stripeWebhookSecret()) {
    throw new Error("STRIPE_WEBHOOK_SECRET is not configured in production.");
  }

  try {
    const hostname = new URL(baseUrl).hostname;
    if (hostname === "localhost" || hostname === "127.0.0.1") {
      throw new Error("Production Stripe return URLs cannot use localhost.");
    }
  } catch (error) {
    if (error.message === "Production Stripe return URLs cannot use localhost.") {
      throw error;
    }
    throw new Error("NEXT_PUBLIC_APP_URL must be a valid production URL.");
  }
}

export function stripeCurrency() {
  return serverEnv("STRIPE_CURRENCY", "usd").toLowerCase();
}

export async function ensurePluginPortalConfiguration() {
  if (!pluginPortalConfigurationPromise) {
    pluginPortalConfigurationPromise = (async () => {
      const stripe = getStripe();
      const existing = await stripe.billingPortal.configurations.list({
        active: true,
        limit: 100,
      });
      const configured = existing.data.find(
        (configuration) => configuration.metadata?.kind === "plugin_subscription",
      );
      if (configured) {
        return configured.id;
      }

      const created = await stripe.billingPortal.configurations.create({
        name: "PromptRail Plugins",
        metadata: { kind: "plugin_subscription" },
        business_profile: {
          headline: "Manage your PromptRail Plugins subscription",
        },
        features: {
          customer_update: {
            enabled: true,
            allowed_updates: ["address", "email"],
          },
          invoice_history: { enabled: true },
          payment_method_update: { enabled: true },
          subscription_cancel: {
            enabled: true,
            mode: "at_period_end",
          },
          subscription_update: { enabled: false },
        },
      });
      return created.id;
    })().catch((error) => {
      pluginPortalConfigurationPromise = undefined;
      throw error;
    });
  }
  return pluginPortalConfigurationPromise;
}
