import { getPublishedPosts } from "./lib/blog.js";

const siteUrl = (process.env.NEXT_PUBLIC_APP_URL || "https://www.promptrail.ai").replace(/\/$/, "");

export default async function sitemap() {
  const posts = await getPublishedPosts();
  return [
    { url: siteUrl, changeFrequency: "weekly", priority: 1 },
    { url: `${siteUrl}/plugins`, changeFrequency: "weekly", priority: 0.9 },
    { url: `${siteUrl}/docs/sdk`, changeFrequency: "weekly", priority: 0.9 },
    { url: `${siteUrl}/connect`, changeFrequency: "monthly", priority: 0.8 },
    { url: `${siteUrl}/blog`, changeFrequency: "daily", priority: 0.9 },
    { url: `${siteUrl}/support`, changeFrequency: "monthly", priority: 0.5 },
    { url: `${siteUrl}/privacy`, changeFrequency: "yearly", priority: 0.4 },
    { url: `${siteUrl}/terms`, changeFrequency: "yearly", priority: 0.4 },
    ...posts.map((post) => ({ url: `${siteUrl}/blog/${post.slug}`, lastModified: post.updatedAt || post.publishedAt, changeFrequency: "monthly", priority: 0.8 })),
  ];
}
