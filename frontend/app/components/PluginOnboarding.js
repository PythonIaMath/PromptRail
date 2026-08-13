"use client";

import { useEffect, useState } from "react";
import { authClient } from "../lib/auth-client.js";
import { pluginOnboardingView } from "../lib/pluginOnboardingState.js";
import PluginPricingCard, { pluginSubscriptionPlans } from "./PluginPricingCard";
import { PluginHeader } from "./PluginSiteChrome";

function CopyButton({ disabled = false, label = "Copy", onCopied, onError, value }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      onCopied?.();
      window.setTimeout(() => setCopied(false), 1600);
    } catch (copyError) {
      setCopied(false);
      onError?.(copyError instanceof Error ? copyError.message : "Clipboard access failed.");
    }
  }

  return (
    <button className="plugin-copy-button" disabled={disabled} type="button" onClick={copy}>
      {copied ? "Copied" : label}
    </button>
  );
}

export default function PluginOnboarding() {
  const { data: session, isPending } = authClient.useSession();
  const [access, setAccess] = useState(null);
  const [accessLoading, setAccessLoading] = useState(true);
  const [billingBusy, setBillingBusy] = useState("");
  const [checkoutSessionId, setCheckoutSessionId] = useState("");
  const [error, setError] = useState("");
  const [plan, setPlan] = useState("month");
  const [subscriptionState, setSubscriptionState] = useState("");
  const [urlPending, setUrlPending] = useState(true);
  const installCommand = "npx @promptrail/plugins";

  async function refreshAccess(signal) {
    setAccessLoading(true);
    setError("");

    try {
      const response = await fetch("/api/plugins/access", {
        cache: "no-store",
        signal,
      });
      const payload = await response.json();
      if (!response.ok || !payload.access) {
        throw new Error(payload.error || "Subscription status failed to load.");
      }
      setAccess(payload.access);
    } catch (requestError) {
      if (requestError?.name !== "AbortError") {
        setError(requestError instanceof Error ? requestError.message : "Subscription status failed to load.");
      }
    } finally {
      if (!signal?.aborted) {
        setAccessLoading(false);
      }
    }
  }

  async function claimCheckout(purchasedCheckoutSessionId, signal) {
    setAccessLoading(true);
    setError("");

    try {
      const response = await fetch("/api/plugins/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ checkoutSessionId: purchasedCheckoutSessionId }),
        signal,
      });
      const payload = await response.json();
      if (!response.ok || !payload.access) {
        throw new Error(payload.error || "Paid subscription activation failed.");
      }
      setAccess(payload.access);
      setCheckoutSessionId("");
      const cleanUrl = new URL(window.location.href);
      cleanUrl.searchParams.delete("checkout_session_id");
      window.history.replaceState(null, "", `${cleanUrl.pathname}${cleanUrl.search}${cleanUrl.hash}`);
    } catch (requestError) {
      if (requestError?.name !== "AbortError") {
        setError(requestError instanceof Error ? requestError.message : "Paid subscription activation failed.");
      }
    } finally {
      if (!signal?.aborted) {
        setAccessLoading(false);
      }
    }
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requestedPlan = params.get("plan");
    if (requestedPlan === "month" || requestedPlan === "year") {
      setPlan(requestedPlan);
    }
    const nextSubscriptionState = params.get("subscription") || "";
    const nextCheckoutSessionId = params.get("checkout_session_id") || "";
    setSubscriptionState(nextSubscriptionState);
    setCheckoutSessionId(nextCheckoutSessionId);
    setUrlPending(false);

    if (!session?.user) {
      setAccess(null);
      setAccessLoading(false);
      return undefined;
    }

    const controller = new AbortController();
    if (nextSubscriptionState === "success" && nextCheckoutSessionId) {
      void claimCheckout(nextCheckoutSessionId, controller.signal);
    } else {
      void refreshAccess(controller.signal);
    }
    return () => controller.abort();
  }, [session?.user?.id]);

  async function openBilling(path, body = {}, busyState = "billing") {
    if (billingBusy) {
      return;
    }

    setBillingBusy(busyState);
    setError("");

    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "Billing request failed.");
      }
      if (!payload.url) {
        throw new Error("Billing did not return a destination URL.");
      }
      window.location.assign(payload.url);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Billing request failed.");
      setBillingBusy("");
    }
  }

  function planAction(option) {
    return (
      <button
        className={`plugin-button ${option.interval === "month" ? "plugin-button-primary" : "plugin-button-secondary"}`}
        disabled={Boolean(billingBusy) || Boolean(error)}
        type="button"
        onClick={() => {
          setPlan(option.interval);
          void openBilling("/api/plugins/checkout", { interval: option.interval }, option.interval);
        }}
      >
        {billingBusy === option.interval ? "Opening..." : `Choose ${option.label}`}
      </button>
    );
  }

  const view = pluginOnboardingView({
    access,
    accessLoading,
    hasCheckoutSession: Boolean(checkoutSessionId),
    hasUser: Boolean(session?.user),
    isSessionPending: isPending,
    isUrlPending: urlPending,
    subscriptionState,
  });

  if (view === "loading") {
    return (
      <main className="plugins-page plugin-onboarding-page">
        <PluginHeader />
        <div className="plugin-onboarding-loading" aria-live="polite">
          <span />
          <p>Loading your plugin setup...</p>
        </div>
      </main>
    );
  }

  if (view === "paymentPending") {
    return (
      <main className="plugins-page plugin-onboarding-page">
        <PluginHeader />
        <section className="plugin-onboarding-shell">
          <header className="plugin-onboarding-heading">
            <div className="plugin-onboarding-heading-copy">
              <p className="plugin-kicker"><span /> Payment received</p>
              <h1>Activating Infinite.</h1>
              <p>Stripe returned your payment successfully. PromptRail is waiting for the subscription confirmation before enabling browser authorization.</p>
            </div>
            <dl className="plugin-onboarding-outcome">
              <div><dt>Payment</dt><dd>Complete</dd></div>
              <div><dt>Access</dt><dd>Awaiting confirmation</dd></div>
              <div><dt>Next</dt><dd>Install and authorize</dd></div>
            </dl>
          </header>
          {error ? <p className="plugin-onboarding-error" role="alert">{error}</p> : null}
          <button
            className="plugin-button plugin-button-primary plugin-confirm-payment"
            disabled={accessLoading}
            type="button"
            onClick={() => {
              if (checkoutSessionId) {
                void claimCheckout(checkoutSessionId);
              } else {
                void refreshAccess();
              }
            }}
          >
            {accessLoading ? "Checking..." : "Check activation"}
          </button>
        </section>
      </main>
    );
  }

  if (view === "accountRequired") {
    const returnPath = `/plugins/onboarding?subscription=success&checkout_session_id=${encodeURIComponent(checkoutSessionId)}`;
    const loginPath = `/login?mode=signup&source=checkout&next=${encodeURIComponent(returnPath)}`;
    return (
      <main className="plugins-page plugin-onboarding-page">
        <PluginHeader />
        <section className="plugin-onboarding-shell">
          <header className="plugin-onboarding-heading">
            <div className="plugin-onboarding-heading-copy">
              <p className="plugin-kicker"><span /> Payment confirmed</p>
              <h1>Create your PromptRail account.</h1>
              <p>Use the same email address you entered in Stripe. We will attach your paid Infinite subscription after you sign in.</p>
            </div>
            <dl className="plugin-onboarding-outcome">
              <div><dt>Payment</dt><dd>Complete</dd></div>
              <div><dt>Account</dt><dd>Required</dd></div>
              <div><dt>Next</dt><dd>Authorize the CLI</dd></div>
            </dl>
          </header>
          <a className="plugin-button plugin-button-primary plugin-confirm-payment" href={loginPath}>
            Create account
          </a>
        </section>
      </main>
    );
  }

  if (view === "pricing") {
    return (
      <main className="plugins-page plugin-onboarding-page">
        <PluginHeader />
        <section className="plugin-onboarding-minimal">
          <h1>Taste infinity.</h1>
          {subscriptionState === "cancelled" ? (
            <p className="plugin-onboarding-notice">Checkout was cancelled. No subscription was created.</p>
          ) : null}
          {error ? <p className="plugin-onboarding-error" role="alert">{error}</p> : null}
          <div className="plugin-onboarding-pricing" role="group" aria-label="Subscription plan">
            {pluginSubscriptionPlans.map((option) => (
              <PluginPricingCard key={option.interval} option={option} selected={plan === option.interval}>
                {planAction(option)}
              </PluginPricingCard>
            ))}
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="plugins-page plugin-onboarding-page">
      <PluginHeader />
      <section className="plugin-install-popup-wrap">
        <div className="plugin-install-popup" role="dialog" aria-labelledby="plugin-install-title">
          <p className="plugin-kicker"><span /> Infinite is active</p>
          <h1 id="plugin-install-title">Install PromptRail.</h1>
          <p>Copy this command into your terminal. The installer opens your browser to authorize Codex and Claude Code.</p>

          <div className="plugin-command-block">
            <div className="plugin-command-head">
              <span>Terminal command</span>
              <CopyButton label="Copy command" onError={setError} value={installCommand} />
            </div>
            <code><span>$</span> {installCommand}</code>
          </div>

          {subscriptionState === "success" ? (
            <p className="plugin-install-popup-status">Payment confirmed. Your Infinite access is ready.</p>
          ) : null}
          {error ? <p className="plugin-onboarding-error" role="alert">{error}</p> : null}
        </div>
      </section>
    </main>
  );
}
