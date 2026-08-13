import { auth } from "../../../lib/auth.js";
import {
  boundedJsonErrorResponse,
  readBoundedJson,
} from "../../../lib/boundedJsonBody.js";
import { getInfiniteAccess } from "../../../lib/infiniteAccess.js";
import {
  createInfiniteProviderConnection,
  listInfiniteProviderConnections,
  revokeInfiniteProviderConnection,
  rotateInfiniteProviderConnection,
} from "../../../lib/infiniteProviderConnections.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function entitledSession(request) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) {
    return { response: Response.json({ error: "Unauthorized" }, { status: 401 }) };
  }
  const access = await getInfiniteAccess(session.user.id);
  if (!access.allowed || !access.scopes.includes("providers:connect")) {
    return {
      response: Response.json(
        { error: "PromptRail Infinite provider access is required." },
        { status: 402 },
      ),
    };
  }
  return { session };
}

export async function GET(request) {
  const checked = await entitledSession(request);
  if (checked.response) return checked.response;
  return Response.json({
    connections: await listInfiniteProviderConnections(checked.session.user.id),
  });
}

export async function POST(request) {
  const checked = await entitledSession(request);
  if (checked.response) return checked.response;
  let input;
  try {
    input = await readBoundedJson(request, { maxBytes: 32 * 1024 });
  } catch (error) {
    return boundedJsonErrorResponse(error);
  }
  try {
    const connection = await createInfiniteProviderConnection({
      userId: checked.session.user.id,
      input,
    });
    return Response.json({ connection }, { status: 201 });
  } catch (error) {
    return Response.json({ error: error.message || "Provider connection failed." }, { status: 400 });
  }
}

export async function DELETE(request) {
  const checked = await entitledSession(request);
  if (checked.response) return checked.response;
  const connectionId = new URL(request.url).searchParams.get("id");
  if (!connectionId) {
    return Response.json({ error: "Missing provider connection id." }, { status: 400 });
  }
  return Response.json({
    revoked: await revokeInfiniteProviderConnection({
      userId: checked.session.user.id,
      connectionId,
    }),
  });
}

export async function PATCH(request) {
  const checked = await entitledSession(request);
  if (checked.response) return checked.response;
  let body;
  try {
    body = await readBoundedJson(request, { maxBytes: 32 * 1024 });
  } catch (error) {
    return boundedJsonErrorResponse(error);
  }
  const connectionId = String(body.id || "").trim();
  if (!connectionId) {
    return Response.json({ error: "Missing provider connection id." }, { status: 400 });
  }
  try {
    const connection = await rotateInfiniteProviderConnection({
      userId: checked.session.user.id,
      connectionId,
      apiKey: body.apiKey,
    });
    return Response.json({ connection });
  } catch (error) {
    return Response.json({ error: error.message || "Provider credential rotation failed." }, { status: 409 });
  }
}
