import { timingSafeEqual } from "node:crypto";
import {
  boundedJsonErrorResponse,
  readBoundedJson,
} from "../../../../lib/boundedJsonBody.js";
import { hydrateInfiniteProviderConnections } from "../../../../lib/infiniteProviderConnections.js";
import { serverEnv } from "../../../../lib/serverEnv.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function authorized(request) {
  const expected = serverEnv("LEROUTER_INTERNAL_SERVICE_TOKEN");
  const authorization = request.headers.get("authorization") || "";
  const prefix = "Bearer ";
  if (!expected || !authorization.startsWith(prefix)) return false;
  const received = authorization.slice(prefix.length);
  if (received.length !== expected.length) return false;
  return timingSafeEqual(Buffer.from(received), Buffer.from(expected));
}

export async function POST(request) {
  if (!authorized(request)) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }
  let body;
  try {
    body = await readBoundedJson(request, { maxBytes: 16 * 1024 });
  } catch (error) {
    return boundedJsonErrorResponse(error);
  }
  try {
    const connections = await hydrateInfiniteProviderConnections({
      userId: String(body.tenantId || ""),
      connectionIds: Array.isArray(body.connectionIds) ? body.connectionIds : [],
    });
    return Response.json(
      { schemaVersion: 1, connections },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch (error) {
    return Response.json(
      { error: error.message || "Provider credential hydration failed." },
      { status: 404, headers: { "Cache-Control": "no-store" } },
    );
  }
}
