const siteUrl = process.env.NEXT_PUBLIC_APP_URL || "https://www.promptrail.ai";

export default function robots() {
  return { rules: [{ userAgent: "*", allow: "/", disallow: ["/api/", "/dashboard/", "/login", "/onboarding"] }], sitemap: `${siteUrl}/sitemap.xml` };
}
