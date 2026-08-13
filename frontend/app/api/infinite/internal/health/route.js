import { timingSafeEqual } from "node:crypto";
import { requireMongoDatabase } from "../../../../lib/mongo.js";
import { serverEnv } from "../../../../lib/serverEnv.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function authorized(request) {
  const expected = serverEnv("LEROUTER_INTERNAL_SERVICE_TOKEN");
  const authorization = request.headers.get("authorization") || "";
  if (!expected || !authorization.startsWith("Bearer ")) return false;
  const received = authorization.slice(7);
  if (received.length !== expected.length) return false;
  return timingSafeEqual(Buffer.from(received), Buffer.from(expected));
}

export async function GET(request) {
  if (!authorized(request)) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }
  try {
    const database = await requireMongoDatabase();
    await database.command({ ping: 1 });
    if (!serverEnv("PROMPTRAIL_PROVIDER_CREDENTIAL_KEK_V1")) {
      throw new Error("Provider credential key is not configured.");
    }
    return Response.json({ status: "ready", schemaVersion: 1 });
  } catch {
    return Response.json({ status: "unavailable" }, { status: 503 });
  }
}
