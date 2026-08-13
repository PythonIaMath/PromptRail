import { createHash, randomBytes, randomUUID } from "node:crypto";
import { auth } from "../../lib/auth.js";
import { getPluginAccess } from "../../lib/pluginAccess.js";
import { getInfiniteAccess } from "../../lib/infiniteAccess.js";
import { insertApiKey, listApiKeys, renameApiKey, revokeApiKey } from "../../lib/store.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function getSession(request) {
  return auth.api.getSession({
    headers: request.headers,
  });
}

function keyHash(value) {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function makeApiKey() {
  return `lr_live_${randomBytes(24).toString("base64url")}`;
}

export async function GET(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const keys = await listApiKeys(session.user.id);

  return Response.json({
    keys,
    localKeys: [],
    mongoKeys: keys,
    unsyncedLocalKeys: [],
  });
}

export async function POST(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json().catch(() => ({}));
  const key = makeApiKey();
  const now = new Date().toISOString();
  const routeId = String(body.routeId || session.user.routeId || "default");
  const name = String(body.name || "Hermes provider key").trim() || "Hermes provider key";
  const isInfiniteKey = body.product === "infinite" || routeId === "infinite";
  const isPluginKey = routeId.startsWith("plugin_");
  if (isInfiniteKey) {
    const access = await getInfiniteAccess(session.user.id);
    if (!access.allowed) {
      return Response.json(
        {
          error: "PromptRail Infinite entitlement is required.",
          code: "infinite_entitlement_required",
          access,
        },
        { status: 402 },
      );
    }
  } else if (isPluginKey) {
    const access = await getPluginAccess(session.user.id);
    if (!access.allowed) {
      return Response.json(
        {
          error: "An active PromptRail Plugins subscription is required.",
          code: "plugin_subscription_required",
          access,
        },
        { status: 402 },
      );
    }
  }
  const row = {
    id: randomUUID(),
    name,
    keyHash: keyHash(key),
    keyPrefix: key.slice(0, 12),
    keySuffix: key.slice(-6),
    routeId,
    kind: isInfiniteKey ? "infinite" : isPluginKey ? "plugin" : "general",
    scopes: isInfiniteKey
      ? ["infinite:infer", "providers:connect", "usage:read"]
      : isPluginKey
        ? ["plugins:route"]
        : [],
    revokedAt: null,
    lastUsedAt: null,
    createdAt: now,
    updatedAt: now,
  };

  return Response.json(await insertApiKey({ row, key, user: session.user }));
}

export async function DELETE(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(request.url);
  const keyId = searchParams.get("id");

  if (!keyId) {
    return Response.json({ error: "Missing key id." }, { status: 400 });
  }

  return Response.json({ revoked: await revokeApiKey({ userId: session.user.id, keyId }) });
}

export async function PATCH(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json().catch(() => ({}));
  const keyId = String(body.id || "").trim();
  const name = String(body.name || "").trim();

  if (!keyId) {
    return Response.json({ error: "Missing key id." }, { status: 400 });
  }

  if (!name) {
    return Response.json({ error: "Key name is required." }, { status: 400 });
  }

  if (name.length > 80) {
    return Response.json({ error: "Key name must be 80 characters or less." }, { status: 400 });
  }

  const apiKey = await renameApiKey({
    userId: session.user.id,
    keyId,
    name,
  });

  if (!apiKey) {
    return Response.json({ error: "API key not found." }, { status: 404 });
  }

  return Response.json({ apiKey });
}
