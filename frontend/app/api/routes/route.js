import { auth } from "../../lib/auth.js";
import { DEFAULT_ROUTE_ID, normalizeRoutePolicyInput } from "../../lib/routeSchemas.js";
import { getRoutePolicy, upsertRoutePolicy } from "../../lib/store.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function getSession(request) {
  return auth.api.getSession({
    headers: request.headers,
  });
}

export async function GET(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(request.url);
  const routeId = searchParams.get("routeId") || session.user.routeId || DEFAULT_ROUTE_ID;

  return Response.json({
    policy: await getRoutePolicy({ userId: session.user.id, routeId }),
  });
}

export async function POST(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  let policy;
  try {
    policy = normalizeRoutePolicyInput(await request.json());
  } catch (error) {
    return Response.json({ error: error.message }, { status: 400 });
  }

  return Response.json({ policy: await upsertRoutePolicy({ user: session.user, policy }) });
}
