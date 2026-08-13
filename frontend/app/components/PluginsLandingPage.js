import { PluginFooter, PluginHeader, githubUrl } from "./PluginSiteChrome";
import PluginHowItWorks from "./PluginHowItWorks";
import PluginLandingPricing from "./PluginLandingPricing";
import PluginScrollStatement from "./PluginScrollStatement";

function ArrowIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4 10h11M11 6l4 4-4 4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </svg>
  );
}

export default function PluginsLandingPage() {
  return (
    <main className="plugins-page">
      <PluginHeader />

      <section className="plugin-hero">
        <div className="plugin-hero-copy">
          <h1>
            Get{" "}
            <em className="plugin-burn-word">
              infinite
              <span className="plugin-burn-dots" aria-hidden="true">
                <span>.</span>
                <span>.</span>
                <span>.</span>
              </span>
            </em>{" "}
            usage
          </h1>
          <p className="plugin-hero-description">
            If you hit your usage limit we refund you. It&apos;s not a joke.
          </p>
          <div className="plugin-hero-actions">
            <a className="plugin-button plugin-button-primary" href="/plugins/onboarding">
              Get Infinite <ArrowIcon />
            </a>
            <a className="plugin-button plugin-button-secondary" href={githubUrl} target="_blank" rel="noreferrer">
              View on GitHub
            </a>
          </div>
        </div>
      </section>

      <PluginScrollStatement />

      <PluginHowItWorks />

      <section className="plugin-paywall" aria-labelledby="plugin-access-title">
        <div className="plugin-paywall-copy">
          <h2 id="plugin-access-title">Infinite access included.</h2>
        </div>
        <PluginLandingPricing />
      </section>

      <PluginFooter />
    </main>
  );
}
