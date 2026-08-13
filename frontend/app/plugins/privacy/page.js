import Link from "next/link";
import { PluginFooter, PluginHeader } from "../../components/PluginSiteChrome";

export const metadata = {
  title: "Plugin Privacy Policy | PromptRail",
  description: "The privacy and security boundary for PromptRail's Codex and Claude Code plugins.",
};

export default function PluginPrivacyPage() {
  return (
    <main className="plugins-page plugin-policy-page">
      <PluginHeader />
      <article className="plugin-policy-article">
        <header>
          <p className="plugin-kicker"><span /> Privacy &amp; security</p>
          <h1>A narrow route.<br />A clear boundary.</h1>
          <p>
            This policy explains what the PromptRail plugins for Codex and Claude Code process,
            what stays local, and what goes directly to your model provider.
          </p>
          <span>Last updated July 10, 2026</span>
        </header>

        <div className="plugin-policy-summary" aria-label="Privacy summary">
          <div><strong>Latest prompt</strong><span>Sent to PromptRail</span></div>
          <div><strong>Provider credentials</strong><span>Stay local</span></div>
          <div><strong>Model response</strong><span>Not sent to PromptRail</span></div>
        </div>

        <div className="plugin-policy-content">
          <section>
            <span>01</span>
            <div>
              <h2>Scope</h2>
              <p>
                This policy applies to the open-source PromptRail Codex and Claude Code client
                integrations. It supplements the main <Link href="/privacy">PromptRail Privacy Policy</Link>,
                which governs the hosted PromptRail service and account information.
              </p>
            </div>
          </section>
          <section>
            <span>02</span>
            <div>
              <h2>Data sent to PromptRail</h2>
              <p>
                The UserPromptSubmit hook sends the latest user-submitted prompt to PromptRail’s hosted
                routing service so it can select an effort grade. The request is authenticated with the
                private machine authorization created during browser approval.
              </p>
              <p>
                Codex routing requests include the selected Codex model identifier. Claude Code routing
                requests do not send the Claude session ID or model identifier to PromptRail.
              </p>
            </div>
          </section>
          <section>
            <span>03</span>
            <div>
              <h2>Data that stays local</h2>
              <p>
                Provider authentication data, provider account identifiers, system and developer instructions,
                full conversation transcripts, attachments, tool definitions, and model responses are not
                sent to PromptRail.
              </p>
              <p>
                Claude Code uses its session identifier only on your machine to match a hook decision with
                the corresponding local model request.
              </p>
            </div>
          </section>
          <section>
            <span>04</span>
            <div>
              <h2>Local proxy and logs</h2>
              <p>
                The local proxy forwards provider requests directly from your machine to OpenAI or Anthropic.
                It binds only to <code>127.0.0.1</code>, restricts forwarded paths, and does not log prompts or
                provider credentials. Operational logs contain only the selected grade, effort, and routing latency.
              </p>
            </div>
          </section>
          <section>
            <span>05</span>
            <div>
              <h2>Local files and removal</h2>
              <p>
                Router configuration is stored under <code>~/.codex/promptrail-router</code> or
                <code>~/.claude/promptrail-router</code> with user-only permissions. Uninstall removes the local
                authorization file and restores the provider configuration when it is safe to do so.
              </p>
            </div>
          </section>
          <section>
            <span>06</span>
            <div>
              <h2>Security and user responsibility</h2>
              <p>
                Do not share PromptRail authorization or router configuration files. The clients reject unsupported
                provider authentication on subscription-only routes and do not follow upstream redirects automatically.
              </p>
            </div>
          </section>
          <section>
            <span>07</span>
            <div>
              <h2>Questions and security reports</h2>
              <p>
                For privacy questions or data requests, email <a href="mailto:support@promptrail.ai">support@promptrail.ai</a>.
                Do not open a public issue for a credential, authentication, local proxy, or cross-user data vulnerability;
                email us with the subject “Security report.”
              </p>
            </div>
          </section>
        </div>
      </article>
      <PluginFooter />
    </main>
  );
}
