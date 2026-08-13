import Link from "next/link";
import BlogChrome from "../components/BlogChrome";
import { getPublishedPosts } from "../lib/blog.js";
import "../blog/blog.module.css";

export const metadata = {
  title: "PromptRail Blog | AI Routing, Agent Workflows, and API Cost Management",
  description: "Practical guides to PromptRail, AI model routing, reasoning effort, agent workflows, and AI API cost management.",
  alternates: { canonical: "/blog" },
  openGraph: { title: "PromptRail Blog", description: "Practical guides to AI routing, agent workflows, and API cost management.", type: "website" },
};

export default async function BlogPage() {
  const posts = await getPublishedPosts();
  return (
    <BlogChrome>
      <main className="blog-main">
        <section className="blog-hero">
          <p className="blog-kicker"><span /> The PromptRail field notes</p>
          <h1>Better routes.<br /><em>Better work.</em></h1>
          <p className="blog-hero-copy">Clear writing on AI model routing, agent reliability, reasoning effort, and keeping API spend under control.</p>
        </section>
        <section className="blog-featured" aria-labelledby="featured-title">
          <div className="blog-section-label"><span>01</span><strong id="featured-title">Start here</strong></div>
          {posts.slice(0, 1).map((post) => <PostCard key={post.slug} post={post} featured />)}
        </section>
        <section className="blog-library" aria-labelledby="library-title">
          <div className="blog-section-label"><span>02</span><strong id="library-title">Latest thinking</strong></div>
          <div className="blog-card-grid">{posts.slice(1).map((post) => <PostCard key={post.slug} post={post} />)}</div>
        </section>
      </main>
    </BlogChrome>
  );
}

function PostCard({ post, featured = false }) {
  return (
    <article className={`blog-card${featured ? " blog-card-featured" : ""}`}>
      <div className="blog-card-meta"><span>{post.category}</span><span>{post.readTime}</span></div>
      <h2><Link href={`/blog/${post.slug}`}>{post.title}</Link></h2>
      <p>{post.excerpt}</p>
      <Link className="blog-read-link" href={`/blog/${post.slug}`}>Read the field note <span aria-hidden="true">↗</span></Link>
    </article>
  );
}
