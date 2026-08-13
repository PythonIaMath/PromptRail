import { randomUUID } from "node:crypto";
import {
  apiKeyHash,
  consumeApprovedDeviceSession,
  createDeviceSession,
  getDeviceSessionStatus,
} from "../../../lib/store.js";
import {
  DEVICE_SESSION_SECONDS,
  INSTALL_TOKEN_SECONDS,
  randomToken,
  userCode,
} from "../../../lib/installation.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function publicBaseUrl(request) {
  return new URL(request.url).origin.replace(/\/+$/g, "");
}

export async function POST(request) {
  const body = await request.json().catch(() => ({}));
  const product = String(body.product || "router").trim().toLowerCase();
  if (!new Set(["router", "infinite"]).has(product)) {
    return Response.json({ error: "unsupported_product" }, { status: 400 });
  }
  const deviceCode = randomToken();
  const code = userCode();
  const expiresAt = new Date(Date.now() + DEVICE_SESSION_SECONDS * 1000);
  const detectedHarnesses = Array.isArray(body.detected_harnesses)
    ? body.detected_harnesses.map((value) => String(value)).filter(Boolean).slice(0, 4)
    : [];

  await createDeviceSession({
    id: randomUUID(),
    deviceCodeHash: apiKeyHash(deviceCode),
    userCode: code,
    product,
    deviceName: String(body.device_name || "PromptRail CLI").slice(0, 120),
    detectedHarnesses,
    expiresAt,
  });

  const verificationUri = `${publicBaseUrl(request)}/device`;
  return Response.json({
    device_code: deviceCode,
    user_code: code,
    verification_uri: verificationUri,
    verification_uri_complete: `${verificationUri}?code=${encodeURIComponent(code)}`,
    expires_in: DEVICE_SESSION_SECONDS,
    interval: 2,
  });
}

export async function PUT(request) {
  const body = await request.json().catch(() => ({}));
  const deviceCode = String(body.device_code || "");
  if (!deviceCode) {
    return Response.json({ error: "invalid_request" }, { status: 400 });
  }

  const status = await getDeviceSessionStatus(apiKeyHash(deviceCode));
  if (!status) {
    return Response.json({ error: "invalid_device_code" }, { status: 400 });
  }
  if (new Date(status.expiresAt).getTime() <= Date.now()) {
    return Response.json({ error: "expired_token" }, { status: 400 });
  }
  if (status.status === "denied") {
    return Response.json({ error: "access_denied" }, { status: 400 });
  }
  if (status.status === "consumed") {
    return Response.json({ error: "invalid_grant" }, { status: 409 });
  }
  if (status.status !== "approved") {
    return Response.json({ error: "authorization_pending" }, { status: 428 });
  }

  const installToken = randomToken();
  const installTokenExpiresAt = new Date(Date.now() + INSTALL_TOKEN_SECONDS * 1000);
  const consumed = await consumeApprovedDeviceSession({
    deviceCodeHash: apiKeyHash(deviceCode),
    installTokenHash: apiKeyHash(installToken),
    installTokenExpiresAt,
  });
  if (!consumed) {
    return Response.json({ error: "invalid_grant" }, { status: 409 });
  }

  return Response.json({
    install_token: installToken,
    token_type: "Bearer",
    expires_in: INSTALL_TOKEN_SECONDS,
  });
}
