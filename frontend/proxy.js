import { trackAICrawlerRequest } from "@datafast/ai-crawl";
import { NextResponse } from "next/server";

export function proxy(request, event) {
  const datafastAICrawlWebsiteId = process.env.DATAFAST_AI_CRAWL_WEBSITE_ID;

  if (datafastAICrawlWebsiteId) {
    trackAICrawlerRequest(request, event, {
      websiteId: datafastAICrawlWebsiteId,
    });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};
