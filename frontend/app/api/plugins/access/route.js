import { auth } from "../../../lib/auth.js";
import { getPluginAccess } from "../../../lib/pluginAccess.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request) {
  const session = await auth.api.getSession({ headers: request.headers });
  const access = await getPluginAccess(session?.user?.id || null);
  return Response.json({ access });
}
