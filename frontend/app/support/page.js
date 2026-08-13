import PublicInfoPage from "../components/PublicInfoPage";

export const metadata = {
  title: "Support | PromptRail",
  description: "Get help with PromptRail accounts, billing, setup, and routing.",
};

export default function SupportPage() {
  return (
    <PublicInfoPage
      eyebrow="Help"
      title="PromptRail Support"
      description="Get help with your account, billing, agent setup, model providers, or routing behavior."
    >
      <section className="public-info-contact">
        <h2>Email support</h2>
        <p>
          Send your request to{" "}
          <a href="mailto:support@promptrail.ai">support@promptrail.ai</a>.
          Include the email address on your account and a short description of
          what happened.
        </p>
        <a
          className="public-info-contact-button"
          href="mailto:support@promptrail.ai?subject=PromptRail%20support%20request"
        >
          Contact support
        </a>
      </section>

      <section>
        <h2>For routing or setup issues</h2>
        <p>
          Include the agent or integration you use, the approximate time of
          the issue, the model provider involved, and any relevant error
          message or request identifier. Remove API keys, passwords, payment
          details, and sensitive prompt content before sending logs.
        </p>
      </section>

      <section>
        <h2>For billing issues</h2>
        <p>
          Include the invoice date or charge date, the amount, and the last
          four digits of the payment method if relevant. Do not send a complete
          card number or bank account number.
        </p>
      </section>

      <section>
        <h2>Security reports</h2>
        <p>
          Report suspected vulnerabilities privately to{" "}
          <a href="mailto:support@promptrail.ai?subject=Security%20report">
            support@promptrail.ai
          </a>
          . Do not access, modify, or retain other users&apos; data while
          investigating an issue.
        </p>
      </section>
    </PublicInfoPage>
  );
}
