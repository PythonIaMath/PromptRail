import { auth } from "../../../lib/auth.js";
import { getPluginAccess } from "../../../lib/pluginAccess.js";
import { ensurePluginPortalConfiguration, getStripe } from "../../../lib/stripe.js";
import { serverEnv } from "../../../lib/serverEnv.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const access = await getPluginAccess(session.user.id);
  if (!access.stripeCustomerId) {
    return Response.json({ error: "No plugin billing account exists for this user." }, { status: 404 });
  }

  const baseUrl = serverEnv(
    "NEXT_PUBLIC_APP_URL",
    serverEnv("BETTER_AUTH_URL", new URL(request.url).origin),
  ).replace(/\/+$/g, "");
  const configuration = await ensurePluginPortalConfiguration();
  const portal = await getStripe().billingPortal.sessions.create({
    configuration,
    customer: access.stripeCustomerId,
    return_url: `${baseUrl}/plugins/onboarding`,
  });

  return Response.json({ url: portal.url });
}
