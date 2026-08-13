import Link from "next/link";

const githubUrl = "https://github.com/PythonIaMath/PromptRail-Plugins";
const footerButtonPath = [
  "M 20 6",
  "C 68 6 164 6 212 6",
  "C 219.73 6 226 12.27 226 20",
  "C 226 25 226 39 226 44",
  "C 226 51.73 219.73 58 212 58",
  "C 164 58 68 58 20 58",
  "C 12.27 58 6 51.73 6 44",
  "C 6 39 6 25 6 20",
  "C 6 12.27 12.27 6 20 6",
].join(" ");

export function PluginHeader() {
  return (
    <header className="plugin-header">
      <Link className="plugin-brand" href="/plugins" aria-label="PromptRail Plugins home">
        <img src="/PromptRail-logo.png" alt="" aria-hidden="true" />
        <span>PromptRail</span>
        <span className="plugin-brand-product">Plugins</span>
      </Link>
      <nav aria-label="Plugin navigation">
        <Link href="/plugins/onboarding">Setup</Link>
        <Link href="/plugins/privacy">Privacy</Link>
        <a className="plugin-header-github" href={githubUrl} target="_blank" rel="noreferrer">
          GitHub
          <span aria-hidden="true">↗</span>
        </a>
        <Link className="plugin-header-signup" href="/plugins/onboarding">
          Get Infinite
        </Link>
      </nav>
    </header>
  );
}

export function PluginFooter() {
  return (
    <footer className="final-production-cta plugin-main-footer" aria-label="Get PromptRail Infinite">
      <strong>Pay less, Build more.</strong>
      <a className="liquid-signup-button" href="/plugins/onboarding">
        <svg className="liquid-signup-bg" viewBox="0 0 232 64" aria-hidden="true">
          <path d={`${footerButtonPath} Z`} />
        </svg>
        <span>Get Infinite</span>
      </a>
      <div className="final-production-legal">
        <span>© 2026 PromptRail</span>
        <Link href="/plugins">Plugins</Link>
        <Link href="/privacy">Privacy</Link>
        <Link href="/terms">Terms</Link>
        <Link href="/support">Support</Link>
      </div>
    </footer>
  );
}

export { githubUrl };
