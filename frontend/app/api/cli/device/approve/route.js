import { auth } from "../../../../lib/auth.js";
import {
  decideDeviceSession,
  getDeviceSessionByUserCode,
} from "../../../../lib/store.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function getSession(request) {
  return auth.api.getSession({ headers: request.headers });
}

export async function GET(request) {
  const session = await getSession(request);
  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }
  const code = new URL(request.url).searchParams.get("code");
  if (!code) {
    return Response.json({ error: "Missing device code." }, { status: 400 });
  }
  const device = await getDeviceSessionByUserCode(code.toUpperCase());
  if (!device) {
    return Response.json({ error: "Device session not found." }, { status: 404 });
  }
  return Response.json({ device });
}

export async function POST(request) {
  const session = await getSession(request);
  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }
  const body = await request.json().catch(() => ({}));
  const code = String(body.code || "").trim().toUpperCase();
  const approved = body.decision === "approve";
  if (!code || !["approve", "deny"].includes(body.decision)) {
    return Response.json({ error: "Invalid approval request." }, { status: 400 });
  }
  const device = await decideDeviceSession({ userCode: code, user: session.user, approved });
  if (!device) {
    return Response.json({ error: "Device session is expired or already decided." }, { status: 409 });
  }
  return Response.json({ device });
}
