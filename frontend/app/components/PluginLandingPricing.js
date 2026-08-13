"use client";

import { useEffect, useRef, useState } from "react";
import PluginPricingCard, { pluginSubscriptionPlans } from "./PluginPricingCard";

function isPlanInterval(value) {
  return value === "month" || value === "year";
}

export default function PluginLandingPricing() {
  const checkoutStartedRef = useRef(false);
  const [busyInterval, setBusyInterval] = useState("");
  const [error, setError] = useState("");

  async function openCheckout(interval) {
    if (checkoutStartedRef.current) {
      return;
    }

    checkoutStartedRef.current = true;
    setBusyInterval(interval);
    setError("");

    try {
      const response = await fetch("/api/plugins/checkout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interval }),
      });
      const payload = await response.json();

      if (response.status === 401) {
        const returnPath = `/plugins?checkout=${interval}`;
        window.location.assign(`/login?mode=signup&next=${encodeURIComponent(returnPath)}`);
        return;
      }

      if (!response.ok) {
        throw new Error(payload.error || `Stripe Checkout failed with status ${response.status}.`);
      }

      if (!payload.url) {
        throw new Error("Stripe Checkout did not return a URL.");
      }

      window.location.assign(payload.url);
    } catch (requestError) {
      checkoutStartedRef.current = false;
      setBusyInterval("");
      setError(requestError instanceof Error ? requestError.message : String(requestError));
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const interval = params.get("checkout");
    if (!isPlanInterval(interval)) {
      return;
    }

    params.delete("checkout");
    const remainingQuery = params.toString();
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${remainingQuery ? `?${remainingQuery}` : ""}${window.location.hash}`,
    );
    void openCheckout(interval);
  }, []);

  return (
    <div className="plugin-landing-pricing-shell">
      <div className="plugin-onboarding-pricing plugin-landing-pricing" role="group" aria-label="Subscription plan">
        {pluginSubscriptionPlans.map((plan) => (
          <PluginPricingCard
            key={plan.interval}
            option={plan}
            selected={plan.interval === "month"}
          >
            <button
              className={`plugin-button ${plan.interval === "month" ? "plugin-button-primary" : "plugin-button-secondary"}`}
              disabled={Boolean(busyInterval)}
              type="button"
              onClick={() => openCheckout(plan.interval)}
            >
              {busyInterval === plan.interval ? "Opening Stripe..." : `Choose ${plan.label}`}
            </button>
          </PluginPricingCard>
        ))}
      </div>
      {error ? <p className="plugin-landing-pricing-error" role="alert">{error}</p> : null}
    </div>
  );
}
