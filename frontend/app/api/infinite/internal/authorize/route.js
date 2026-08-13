import { timingSafeEqual } from "node:crypto";
import {
  boundedJsonErrorResponse,
  readBoundedJson,
} from "../../../../lib/boundedJsonBody.js";
import { authorizeInfiniteRequest } from "../../../../lib/infiniteRequestAuthority.js";
import { serverEnv } from "../../../../lib/serverEnv.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function serviceAuthorized(request) {
  const expected = serverEnv("LEROUTER_INTERNAL_SERVICE_TOKEN");
  const authorization = request.headers.get("authorization") || "";
  if (!expected || !authorization.startsWith("Bearer ")) return false;
  const received = authorization.slice(7);
  if (received.length !== expected.length) return false;
  return timingSafeEqual(Buffer.from(received), Buffer.from(expected));
}

export async function POST(request) {
  if (!serviceAuthorized(request)) {
    return Response.json(
      { error: { code: "unauthorized", message: "Unauthorized" } },
      { status: 401 },
    );
  }
  let body;
  try {
    body = await readBoundedJson(request, { maxBytes: 8 * 1024 });
  } catch (error) {
    return boundedJsonErrorResponse(error);
  }
  const result = await authorizeInfiniteRequest(body.apiKey);
  if (result.status !== 200) {
    return Response.json(
      { error: { code: result.code, message: result.code } },
      { status: result.status, headers: { "Cache-Control": "no-store" } },
    );
  }
  return Response.json(
    {
      schemaVersion: 1,
      tenantId: result.identity.tenantId,
      keyId: result.identity.keyId,
      scopes: result.identity.scopes,
      access: result.access,
      policy: result.policy,
      candidates: result.candidates,
    },
    { headers: { "Cache-Control": "private, max-age=15" } },
  );
}
