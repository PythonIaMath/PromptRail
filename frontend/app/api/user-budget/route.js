import { auth } from "../../lib/auth.js";
import { normalizeUserBudgetInput } from "../../lib/routeSchemas.js";
import {
  ensureUserDefaults,
  findMongoBudgetUser,
  getBillingSummary,
  updateUserBudget,
} from "../../lib/store.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function getSession(request) {
  return auth.api.getSession({
    headers: request.headers,
  });
}

export async function GET(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const user = await ensureUserDefaults(session.user);
  const mongoUser = await findMongoBudgetUser(session.user);

  return Response.json({
    user,
    mongoUser,
    billing: await getBillingSummary(session.user.id),
  });
}

export async function POST(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json();
  const budget = normalizeUserBudgetInput(body);
  const nextBudget = await updateUserBudget({
    user: session.user,
    budget,
    planType: body.planType || "monthly",
  });

  return Response.json({
    budget: nextBudget,
  });
}
