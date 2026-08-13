function PromptRailMark() {
  return (
    <img
      className="check-email-mark"
      src="/PromptRail-logo.png"
      alt=""
      aria-hidden="true"
    />
  );
}

export default function CheckEmail({ email }) {
  return (
    <main className="check-email-page">
      <section className="check-email-card" aria-labelledby="check-email-title">
        <div className="check-email-brand">
          <PromptRailMark />
          <span>PromptRail</span>
        </div>

        <h1 id="check-email-title">Check your email</h1>
        <p>
          A sign in link has been sent to{" "}
          {email ? <strong>{email}</strong> : "your email address"}.
        </p>

        <span className="check-email-domain">promptrail.ai</span>
      </section>
    </main>
  );
}
