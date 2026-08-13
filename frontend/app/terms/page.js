import PublicInfoPage from "../components/PublicInfoPage";

export const metadata = {
  title: "Terms of Service | PromptRail",
  description: "Terms governing access to and use of PromptRail.",
};

export default function TermsPage() {
  return (
    <PublicInfoPage
      eyebrow="Legal"
      title="Terms of Service"
      description="These terms govern your access to and use of PromptRail. By using the service, you agree to them."
      updated="July 10, 2026"
    >
      <section>
        <h2>Eligibility and accounts</h2>
        <p>
          You must be able to form a binding contract and be at least 18 years
          old to use PromptRail. You are responsible for your account,
          credentials, API keys, and all activity performed through them. Keep
          credentials confidential and notify us promptly of unauthorized use.
        </p>
      </section>

      <section>
        <h2>The service</h2>
        <p>
          PromptRail provides software for routing requests to third-party AI
          models, managing budgets, and reporting usage. Model availability,
          output, latency, pricing, and capabilities may change and may depend
          on third-party providers. You are responsible for reviewing model
          output before relying on it.
        </p>
      </section>

      <section>
        <h2>Acceptable use</h2>
        <p>
          You may not use PromptRail to violate law or third-party rights,
          distribute malware, interfere with the service, bypass usage or
          security controls, obtain unauthorized access, or facilitate harmful
          or fraudulent activity. You must also comply with the terms of every
          model provider you connect or use.
        </p>
      </section>

      <section>
        <h2>Subscriptions, credits, and taxes</h2>
        <p>
          Paid features are billed according to the pricing and billing terms
          shown at purchase. Charges are non-refundable except where required
          by law or expressly stated otherwise. You authorize us and our
          payment processor to charge applicable recurring fees, usage charges,
          and taxes. You may cancel a subscription before its next renewal.
        </p>
      </section>

      <section>
        <h2>Your content and feedback</h2>
        <p>
          You retain ownership of content you submit. You grant PromptRail the
          limited rights needed to process that content and operate the
          service. You represent that you have the necessary rights to submit
          it. Feedback may be used without restriction or compensation.
        </p>
      </section>

      <section>
        <h2>Third-party services</h2>
        <p>
          PromptRail integrates with services we do not control, including AI
          model and payment providers. Your use of those services is governed
          by their terms. PromptRail is not responsible for third-party
          services, content, availability, or changes.
        </p>
      </section>

      <section>
        <h2>Suspension and termination</h2>
        <p>
          You may stop using PromptRail at any time. We may suspend or
          terminate access for nonpayment, security risk, violation of these
          terms, legal requirements, or conduct that could harm the service or
          others. Provisions that by their nature should survive termination
          will survive.
        </p>
      </section>

      <section>
        <h2>Disclaimers and liability</h2>
        <p>
          The service is provided “as is” and “as available.” To the fullest
          extent permitted by law, PromptRail disclaims implied warranties and
          is not liable for indirect, incidental, special, consequential, or
          punitive damages, lost profits, lost data, or model output. Our total
          liability will not exceed the amount you paid PromptRail in the
          twelve months before the event giving rise to the claim.
        </p>
      </section>

      <section>
        <h2>Changes and contact</h2>
        <p>
          We may update these terms and will post the revised version with a
          new effective date. Continued use after an update means you accept
          the revised terms. Questions can be sent to{" "}
          <a href="mailto:support@promptrail.ai">support@promptrail.ai</a>.
        </p>
      </section>
    </PublicInfoPage>
  );
}
