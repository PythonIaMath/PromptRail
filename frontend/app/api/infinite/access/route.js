import { auth } from "../../../lib/auth.js";
import {
  boundedJsonErrorResponse,
  readBoundedJson,
} from "../../../lib/boundedJsonBody.js";
import {
  getInfiniteAccess,
  optIntoInfiniteBeta,
} from "../../../lib/infiniteAccess.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function sessionFor(request) {
  return auth.api.getSession({ headers: request.headers });
}

export async function GET(request) {
  const session = await sessionFor(request);
  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }
  return Response.json({ access: await getInfiniteAccess(session.user.id) });
}

export async function POST(request) {
  const session = await sessionFor(request);
  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }
  let body;
  try {
    body = await readBoundedJson(request, { maxBytes: 1024 });
  } catch (error) {
    return boundedJsonErrorResponse(error);
  }
  if (body.confirm !== true) {
    return Response.json({ error: "Explicit Infinite activation confirmation is required." }, { status: 400 });
  }
  try {
    return Response.json({ access: await optIntoInfiniteBeta(session.user) });
  } catch (error) {
    return Response.json({ error: error.message || "Infinite activation failed." }, { status: 403 });
  }
}
