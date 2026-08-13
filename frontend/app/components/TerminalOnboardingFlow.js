"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { authClient } from "../lib/auth-client.js";

const installCommand = "curl -fsSL https://promptrail.ai/install | sh";

export default function TerminalOnboardingFlow() {
  const router = useRouter();
  const { data: session, isPending } = authClient.useSession();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!isPending && !session?.user) {
      router.replace("/login?next=/onboarding");
    }
  }, [isPending, router, session]);

  async function copyCommand() {
    await navigator.clipboard.writeText(installCommand);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  if (isPending || !session?.user) {
    return <main className="terminal-onboarding-page"><p>Loading onboarding...</p></main>;
  }

  return (
    <main className="terminal-onboarding-page">
      <section className="terminal-onboarding-shell">
        <header>
          <p className="terminal-onboarding-kicker">PromptRail setup</p>
          <h1>Install from your terminal.</h1>
          <p>
            The installer detects Hermes or OpenClaw, opens secure account authorization,
            and asks for your workspace, budget, billing cycle, inference owner, and harness.
          </p>
        </header>

        <div className="terminal-command">
          <div>
            <span>One-command installer</span>
            <code>{installCommand}</code>
          </div>
          <button type="button" onClick={copyCommand}>{copied ? "Copied" : "Copy"}</button>
        </div>

        <div className="terminal-onboarding-steps">
          <div><span>1</span><strong>Run the command</strong><p>Compatibility checks happen before any local changes.</p></div>
          <div><span>2</span><strong>Approve the device</strong><p>Your browser authenticates the account; the API key stays out of shell history.</p></div>
          <div><span>3</span><strong>Choose routing policy</strong><p>Confirm budget, cycle, inference owner, workspace, and detected harness.</p></div>
          <div><span>4</span><strong>Verify installation</strong><p>The terminal reports providers, models, routes, scheduler, and service health.</p></div>
        </div>

        <p className="terminal-onboarding-note">
          User-managed inference uses provider credentials already configured in the selected harness.
          LeRouter-managed inference does not request local provider keys.
        </p>
      </section>
    </main>
  );
}
