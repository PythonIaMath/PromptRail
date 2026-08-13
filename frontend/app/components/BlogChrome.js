import Link from "next/link";

export default function BlogChrome({ children }) {
  return (
    <div className="blog-shell">
      <header className="blog-header">
        <Link className="blog-brand" href="/" aria-label="PromptRail home">
          <img src="/PromptRail-logo.png" alt="" aria-hidden="true" />
          <span>PromptRail</span>
        </Link>
        <nav aria-label="Blog navigation">
          <Link href="/blog">Blog</Link>
          <Link href="/plugins">Plugins</Link>
          <Link className="blog-nav-cta" href="/plugins/onboarding">Try PromptRail</Link>
        </nav>
      </header>
      {children}
      <footer className="blog-footer">
        <span>PromptRail, routing for the work that matters.</span>
        <div><Link href="/privacy">Privacy</Link><Link href="/terms">Terms</Link><Link href="/support">Support</Link></div>
      </footer>
    </div>
  );
}
