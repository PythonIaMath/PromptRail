import { createHash, randomBytes, randomUUID } from "node:crypto";
import { getInfiniteAccess, INFINITE_INFERENCE_SCOPES } from "../../../../lib/infiniteAccess.js";
import { apiKeyHash, consumeInstallToken, insertApiKey } from "../../../../lib/store.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function bearerToken(request) {
  const value = request.headers.get("authorization") || "";
  return value.startsWith("Bearer ") ? value.slice(7).trim() : "";
}

function makeApiKey() {
  return `lr_live_${randomBytes(24).toString("base64url")}`;
}

export async function POST(request) {
  const installToken = bearerToken(request);
  if (!installToken) {
    return Response.json({ error: "Missing installation token." }, { status: 401 });
  }

  const body = await request.json().catch(() => ({}));
  const deviceName = String(body.device_name || "PromptRail CLI").trim().slice(0, 120)
    || "PromptRail CLI";
  const grant = await consumeInstallToken(apiKeyHash(installToken));
  if (!grant?.userId || grant.product !== "infinite") {
    return Response.json(
      { error: "Installation token is invalid, expired, already used, or for another product." },
      { status: 401 },
    );
  }

  const access = await getInfiniteAccess(grant.userId);
  if (!access.allowed) {
    return Response.json(
      { error: "An active PromptRail Infinite subscription is required." },
      { status: 402 },
    );
  }

  const key = makeApiKey();
  const now = new Date().toISOString();
  const row = {
    id: randomUUID(),
    name: `${deviceName} Infinite`,
    keyHash: createHash("sha256").update(key, "utf8").digest("hex"),
    keyPrefix: key.slice(0, 12),
    keySuffix: key.slice(-6),
    routeId: "infinite",
    kind: "infinite",
    scopes: [...INFINITE_INFERENCE_SCOPES],
    revokedAt: null,
    lastUsedAt: null,
    createdAt: now,
    updatedAt: now,
  };
  const created = await insertApiKey({
    row,
    key,
    user: {
      id: grant.userId,
      email: grant.userEmail,
      name: grant.userEmail?.split("@")[0] || "PromptRail user",
      emailVerified: true,
    },
  });

  return Response.json({
    credential: {
      api_key: created.key,
      api_key_id: created.apiKey.id,
      scopes: row.scopes,
    },
  });
}
