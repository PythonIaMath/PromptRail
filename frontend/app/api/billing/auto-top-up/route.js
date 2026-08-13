import { auth } from "../../../lib/auth.js";
import { ensureUserDefaults, updateAutoTopUpSettings } from "../../../lib/store.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function getSession(request) {
  return auth.api.getSession({
    headers: request.headers,
  });
}

export async function PATCH(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  try {
    await ensureUserDefaults(session.user);
    const user = await updateAutoTopUpSettings(session.user, {
      enabled: body.enabled,
      thresholdUsd: body.thresholdUsd,
      amountUsd: body.amountUsd,
    });

    return Response.json({ user });
  } catch (error) {
    return Response.json({ error: error.message || "Auto top-up settings could not be saved." }, { status: 400 });
  }
}
