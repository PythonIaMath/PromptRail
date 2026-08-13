import Link from "next/link";

const publicLinks = [
  { href: "/plugins", label: "Plugins" },
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
  { href: "/support", label: "Support" },
];

export function PublicFooter() {
  return (
    <footer className="public-footer">
      <p>© 2026 PromptRail</p>
      <nav aria-label="Legal and support">
        {publicLinks.map((link) => (
          <Link href={link.href} key={link.href}>
            {link.label}
          </Link>
        ))}
      </nav>
    </footer>
  );
}

export default function PublicInfoPage({
  eyebrow,
  title,
  description,
  updated,
  children,
}) {
  return (
    <main className="public-info-page">
      <header className="public-info-header">
        <Link className="public-info-brand" href="/" aria-label="PromptRail home">
          <img src="/PromptRail-logo.png" alt="" aria-hidden="true" />
          <span>PromptRail</span>
        </Link>
        <Link className="public-info-home-link" href="/">
          Back to home
        </Link>
      </header>

      <article className="public-info-article">
        <div className="public-info-intro">
          <p className="public-info-eyebrow">{eyebrow}</p>
          <h1>{title}</h1>
          <p className="public-info-description">{description}</p>
          {updated ? <p className="public-info-updated">Last updated: {updated}</p> : null}
        </div>
        <div className="public-info-content">{children}</div>
      </article>

      <PublicFooter />
    </main>
  );
}
