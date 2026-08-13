import PublicInfoPage from "../components/PublicInfoPage";

export const metadata = {
  title: "Privacy Policy | PromptRail",
  description: "How PromptRail collects, uses, and protects personal information.",
};

export default function PrivacyPage() {
  return (
    <PublicInfoPage
      eyebrow="Legal"
      title="Privacy Policy"
      description="This policy explains what information PromptRail processes, why we process it, and the choices available to you."
      updated="July 25, 2026"
    >
      <section>
        <h2>Information we collect</h2>
        <p>
          We collect account information such as your name, email address, and
          authentication details. We also process configuration data, API key
          metadata, budget settings, usage records, routing decisions, model
          identifiers, token counts, costs, and diagnostic events generated
          when you use PromptRail.
        </p>
        <p>
          If you purchase a subscription or credits, our payment processor
          collects payment and billing information. PromptRail does not store
          complete payment card numbers.
        </p>
      </section>

      <section>
        <h2>How we use information</h2>
        <p>
          We use information to provide and secure the service, authenticate
          accounts, route AI requests, enforce budgets, calculate usage and
          charges, provide support, prevent abuse, improve reliability, and
          comply with legal obligations.
        </p>
      </section>

      <section>
        <h2>AI request processing</h2>
        <p>
          PromptRail Infinite is a hosted inference service. When you use
          Infinite, PromptRail receives the protocol request from your coding
          client. Depending on the request, this can include system
          instructions, conversation messages, tool definitions, tool results,
          structured-output settings, reasoning controls, and attachments.
          PromptRail transmits the protocol data needed to execute the selected
          declared route to that model provider. Modal hosts the PromptRail
          gateway, and the selected model provider processes the request under
          its own terms and privacy policy.
        </p>
        <p>
          PromptRail processes raw prompts, responses, attachments, and tool
          arguments transiently to route and stream the request. PromptRail does
          not write that raw content, authorization headers, or provider
          credentials to application logs or execution receipts by default.
          Client cancellation is propagated to stop upstream work when the
          protocol and provider permit it.
        </p>
        <p>
          PromptRail Plugins uses a different, local provider-inference path.
          Its narrower data boundary is described in the dedicated{" "}
          <a href="/plugins/privacy">Plugins Privacy Policy</a>.
        </p>
        <p>
          Do not submit personal, confidential, or regulated information unless
          your use of PromptRail, Modal, and the selected provider is appropriate
          for that information.
        </p>
      </section>

      <section>
        <h2>Infinite credentials and routing records</h2>
        <p>
          PromptRail API keys are stored as one-way hashes. User-connected
          provider credentials are encrypted at rest and are available only to
          the authenticated internal execution path for that tenant. They remain
          stored until they are rotated, revoked, deleted with the account, or
          must be retained for a legal or security obligation.
        </p>
        <p>
          Infinite stores sanitized execution receipts for reliability, usage,
          billing, and abuse prevention. A receipt can include tenant and route
          identifiers; policy and catalog versions; selected and executed model
          identifiers; capacity class; premium or degraded-mode status; hashed
          connection identity; attempt result and latency; token counts;
          estimated cost; and routing latency. It does not contain raw prompts,
          full responses, attachments, authorization headers, provider
          credentials, or full tool arguments. Execution receipts are configured
          to expire after 90 days.
        </p>
      </section>

      <section>
        <h2>Service providers and disclosures</h2>
        <p>
          We may share information with vendors that support hosting,
          authentication, payments, email, analytics, and model routing. We may
          also disclose information when required by law, to protect users or
          the service, or as part of a merger, financing, acquisition, or sale
          of assets.
        </p>
      </section>

      <section>
        <h2>Retention and security</h2>
        <p>
          We retain information for as long as needed to operate the service,
          maintain business and security records, resolve disputes, and meet
          legal obligations. We use reasonable administrative and technical
          safeguards, but no method of storage or transmission is completely
          secure.
        </p>
      </section>

      <section>
        <h2>Your choices</h2>
        <p>
          You may request access to, correction of, or deletion of your
          personal information. Depending on where you live, you may have
          additional rights under applicable privacy law. We may need to verify
          your identity before completing a request.
        </p>
      </section>

      <section>
        <h2>Children</h2>
        <p>
          PromptRail is not directed to children under 13, and we do not
          knowingly collect personal information from children under 13.
        </p>
      </section>

      <section>
        <h2>Changes and contact</h2>
        <p>
          We may update this policy as the service changes. The date above
          identifies the latest version. Questions and privacy requests can be
          sent to{" "}
          <a href="mailto:support@promptrail.ai">support@promptrail.ai</a>.
        </p>
      </section>
    </PublicInfoPage>
  );
}
