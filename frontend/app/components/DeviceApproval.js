"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { authClient } from "../lib/auth-client.js";

function PromptRailMark() {
  return (
    <div className="device-approval-brand" aria-label="PromptRail Plugins">
      <img src="/PromptRail-logo.png" alt="" aria-hidden="true" />
      <span>PromptRail</span>
      <span className="device-approval-brand-product">Plugins</span>
    </div>
  );
}

function LoadingApproval() {
  return (
    <main className="device-approval-page">
      <section className="device-approval-panel device-approval-loading" aria-live="polite">
        <PromptRailMark />
        <div className="device-approval-loader" aria-hidden="true"><span /><span /><span /></div>
        <p>Preparing secure authorization</p>
      </section>
    </main>
  );
}

export default function DeviceApproval() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { data: session, isPending } = authClient.useSession();
  const [code, setCode] = useState(searchParams.get("code") || "");
  const [device, setDevice] = useState(null);
  const [error, setError] = useState("");
  const [complete, setComplete] = useState(false);

  useEffect(() => {
    if (!isPending && !session?.user) {
      router.replace(`/login?next=${encodeURIComponent(`/device?code=${code}`)}`);
    }
  }, [code, isPending, router, session]);

  async function lookup(event) {
    event?.preventDefault();
    setError("");
    const response = await fetch(`/api/cli/device/approve?code=${encodeURIComponent(code)}`);
    const payload = await response.json();
    if (!response.ok) {
      setDevice(null);
      setError(payload.error || "Device session lookup failed.");
      return;
    }
    setDevice(payload.device);
  }

  useEffect(() => {
    if (session?.user && code) {
      lookup();
    }
  }, [session?.user]);

  async function decide(decision) {
    setError("");
    const response = await fetch("/api/cli/device/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, decision }),
    });
    const payload = await response.json();
    if (!response.ok) {
      setError(payload.error || "Device authorization failed.");
      return;
    }
    setComplete(true);
  }

  if (isPending || !session?.user) {
    return <LoadingApproval />;
  }

  return (
    <main className="device-approval-page">
      <section className="device-approval-panel">
        <PromptRailMark />
        <div className="device-approval-heading">
          <p className="device-approval-kicker"><span aria-hidden="true" /> Secure browser authorization</p>
          <h1>{complete ? "Device connected" : "Authorize this device"}</h1>
          <p className="device-approval-intro">
            {complete
              ? "PromptRail has securely linked this installation to your account."
              : "Confirm the installation opened by the PromptRail plugin for Codex and Claude Code."}
          </p>
        </div>
        {complete ? (
          <div className="device-approval-complete">
            <span className="device-approval-success-icon" aria-hidden="true">✓</span>
            <div>
              <strong>Authorization recorded</strong>
              <p>You can close this window and return to your terminal.</p>
            </div>
          </div>
        ) : (
          <>
            <form onSubmit={lookup}>
              <label>
                <span>Device code</span>
                <input
                  value={code}
                  onChange={(event) => setCode(event.target.value.toUpperCase())}
                  placeholder="XXXX-XXXX"
                  autoComplete="one-time-code"
                  spellCheck="false"
                  aria-describedby="device-code-hint"
                />
              </label>
              <p id="device-code-hint" className="device-approval-hint">This code was generated locally by the installer.</p>
              <button type="submit" className="device-approval-lookup">Find device <span aria-hidden="true">→</span></button>
            </form>
            {device ? (
              <div className="device-approval-details">
                <p className="device-approval-details-label">Authorization request</p>
                <dl>
                  <div><dt>Device</dt><dd>{device.deviceName}</dd></div>
                  <div><dt>Product</dt><dd>{device.product === "infinite" ? "PromptRail Infinite" : "PromptRail router"}</dd></div>
                  <div><dt>Detected</dt><dd>{device.detectedHarnesses?.join(", ") || "No harness reported"}</dd></div>
                  <div><dt>Account</dt><dd>{session.user.email}</dd></div>
                </dl>
                <div className="device-approval-actions">
                  <button type="button" onClick={() => decide("deny")}>Deny</button>
                  <button type="button" className="is-primary" onClick={() => decide("approve")}>Approve device <span aria-hidden="true">→</span></button>
                </div>
              </div>
            ) : null}
          </>
        )}
        {error ? <p className="auth-error">{error}</p> : null}
        <p className="device-approval-security">PromptRail never receives your Codex or Claude Code credentials.</p>
      </section>
    </main>
  );
}
