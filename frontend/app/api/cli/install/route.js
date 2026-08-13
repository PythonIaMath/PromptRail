import { createHash, randomBytes, randomUUID } from "node:crypto";
import {
  apiKeyHash,
  consumeInstallToken,
  insertApiKey,
  saveInstallation,
  updateUserBudget,
} from "../../../lib/store.js";
import {
  budgetInputFromManifest,
  validateInstallationManifest,
} from "../../../lib/installation.js";

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
  const grant = await consumeInstallToken(apiKeyHash(installToken));
  if (!grant?.userId || (grant.product && grant.product !== "router")) {
    return Response.json({ error: "Installation token is invalid, expired, or already used." }, { status: 401 });
  }

  let manifest;
  try {
    manifest = validateInstallationManifest(await request.json());
  } catch (error) {
    return Response.json({ error: error.message }, { status: 400 });
  }

  const user = {
    id: grant.userId,
    email: grant.userEmail,
    name: grant.userEmail?.split("@")[0] || "PromptRail user",
    emailVerified: true,
    routeId: manifest.route_id,
  };
  const budget = budgetInputFromManifest(manifest);
  await updateUserBudget({ user, budget, planType: manifest.budget.cycle });

  const key = makeApiKey();
  const now = new Date().toISOString();
  const keyRow = {
    id: randomUUID(),
    name: `${manifest.workspace_name} ${manifest.harness} installer`,
    keyHash: createHash("sha256").update(key, "utf8").digest("hex"),
    keyPrefix: key.slice(0, 12),
    keySuffix: key.slice(-6),
    routeId: manifest.route_id,
    revokedAt: null,
    lastUsedAt: null,
    createdAt: now,
    updatedAt: now,
  };
  const created = await insertApiKey({ row: keyRow, key, user });
  await saveInstallation({ user, manifest, apiKeyId: keyRow.id });

  return Response.json({
    manifest,
    credential: {
      api_key: created.key,
      api_key_id: created.apiKey.id,
    },
    endpoints: {
      api_url: process.env.LEROUTER_PUBLIC_BASE_URL
        || process.env.NEXT_PUBLIC_LEROUTER_API_URL
        || "https://promptrail--lerouter-api-fastapi-app.modal.run",
      dashboard_url: process.env.NEXT_PUBLIC_APP_URL || new URL(request.url).origin,
    },
  });
}
