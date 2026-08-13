import { notFound } from "next/navigation";
import Link from "next/link";
import BlogChrome from "../../components/BlogChrome";
import { getPublishedPost, getPublishedPosts } from "../../lib/blog.js";
import "../blog.module.css";

const siteUrl = process.env.NEXT_PUBLIC_APP_URL || "https://www.promptrail.ai";

export async function generateStaticParams() {
  const posts = await getPublishedPosts();
  return posts.map((post) => ({ slug: post.slug }));
}

export async function generateMetadata({ params }) {
  const post = await getPublishedPost((await params).slug);
  if (!post) return {};
  return {
    title: `${post.title} | PromptRail Blog`,
    description: post.description,
    keywords: [post.primaryKeyword, "PromptRail", "AI routing", "AI model router"],
    alternates: { canonical: `/blog/${post.slug}` },
    openGraph: { title: post.title, description: post.description, type: "article", publishedTime: post.publishedAt, url: `${siteUrl}/blog/${post.slug}` },
  };
}

export default async function BlogPostPage({ params }) {
  const post = await getPublishedPost((await params).slug);
  if (!post) notFound();
  const articleUrl = `${siteUrl}/blog/${post.slug}`;
  const faqs = post.sections.flatMap((section) => section.faqs || []);
  const articleJsonLd = { "@context": "https://schema.org", "@type": "Article", headline: post.title, description: post.description, datePublished: post.publishedAt, dateModified: post.updatedAt || post.publishedAt, mainEntityOfPage: articleUrl, author: { "@type": "Organization", name: "PromptRail", url: siteUrl }, publisher: { "@type": "Organization", name: "PromptRail", url: siteUrl } };
  const faqJsonLd = faqs.length ? { "@context": "https://schema.org", "@type": "FAQPage", mainEntity: faqs.map((faq) => ({ "@type": "Question", name: faq.question, acceptedAnswer: { "@type": "Answer", text: faq.answer } })) } : null;
  return (
    <BlogChrome>
      <main className="blog-article-page">
        <article className="blog-article">
          <header className="blog-article-header">
            <Link className="blog-back-link" href="/blog">← All field notes</Link>
            <p className="blog-kicker"><span /> {post.eyebrow}</p>
            <h1>{post.title}</h1>
            <p className="blog-article-dek">{post.description}</p>
            <div className="blog-article-meta"><span>{post.category}</span><span>{post.readTime}</span><time dateTime={post.publishedAt}>{new Date(post.publishedAt).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" })}</time></div>
          </header>
          <div className="blog-article-layout">
            <aside className="blog-outline" aria-label="Article outline"><span>In this note</span>{post.sections.map((section) => <a key={section.heading} href={`#${slugify(section.heading)}`}>{section.heading}</a>)}</aside>
            <div className="blog-prose">
              {post.sections.map((section) => <section key={section.heading} id={slugify(section.heading)}><h2>{section.heading}</h2>{section.paragraphs?.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}{section.faqs?.map((faq) => <div className="blog-faq" key={faq.question}><h3>{faq.question}</h3><p>{faq.answer}</p></div>)}</section>)}
              {post.references?.length ? <section><h2>Sources and further reading</h2><ul className="blog-reference-list">{post.references.map((reference) => <li key={reference.url}><a href={reference.url} target="_blank" rel="noreferrer">{reference.label}</a><span>{reference.reason}</span></li>)}</ul></section> : null}
              {post.internalLinks?.length ? <section><h2>Continue with PromptRail</h2><ul className="blog-reference-list">{post.internalLinks.map((reference) => <li key={reference.url}><Link href={reference.url}>{reference.label}</Link><span>{reference.reason}</span></li>)}</ul></section> : null}
              <div className="blog-article-cta"><strong>Route the work, not the guess.</strong><p>See how PromptRail chooses reasoning effort for Codex and Claude Code.</p><Link href="/plugins/onboarding">Explore PromptRail plugins <span aria-hidden="true">↗</span></Link></div>
            </div>
          </div>
        </article>
      </main>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(articleJsonLd) }} />
      {faqJsonLd ? <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(faqJsonLd) }} /> : null}
    </BlogChrome>
  );
}

function slugify(value) { return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, ""); }
