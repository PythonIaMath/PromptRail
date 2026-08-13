import { POST as billingWebhook } from "../../billing/webhook/route.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request) {
  return billingWebhook(request);
}
