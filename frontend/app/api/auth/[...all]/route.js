import { toNextJsHandler } from "better-auth/next-js";
import { auth } from "../../../lib/auth.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const handlers = toNextJsHandler(auth);

function authErrorResponse(error) {
  console.error("Better Auth route failed", {
    name: error?.name,
    message: error?.message,
    stack: error?.stack,
  });

  return Response.json({ error: "Authentication service failed." }, { status: 500 });
}

export async function GET(request) {
  try {
    return await handlers.GET(request);
  } catch (error) {
    return authErrorResponse(error);
  }
}

export async function POST(request) {
  try {
    return await handlers.POST(request);
  } catch (error) {
    return authErrorResponse(error);
  }
}
