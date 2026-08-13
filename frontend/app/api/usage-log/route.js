import { auth } from "../../lib/auth.js";
import { triggerAutoTopUp } from "../../lib/autoTopUp.js";
import { usageBillingMetadata } from "../../lib/billing.js";
import {
  apiKeyHash,
  ensureUserDefaults,
  getApiKeyAccess,
  insertUsageAndUpdateUser,
  listUsageLogs,
} from "../../lib/store.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function asNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

async function getSession(request) {
  return auth.api.getSession({
    headers: request.headers,
  });
}

function bearerToken(request) {
  const header = request.headers.get("authorization") || "";
  const match = header.match(/^Bearer\s+(.+)$/i);
  return match ? match[1].trim() : "";
}

async function getAccessContext(request) {
  const session = await getSession(request);

  if (session?.user) {
    return {
      type: "session",
      user: session.user,
      routeId: session.user.routeId || "default",
    };
  }

  const token = bearerToken(request);
  if (!token) {
    return null;
  }

  return getApiKeyAccess(apiKeyHash(token));
}

function modelLabFromModel(modelId) {
  if (!modelId) {
    return null;
  }

  const normalized = String(modelId).toLowerCase();
  const owner = normalized.split("/", 1)[0].replace(/^~/, "");
  const ownerLabels = {
    "ai21": "AI21",
    "amazon": "Amazon",
    "anthropic": "Anthropic",
    "cohere": "Cohere",
    "deepseek": "DeepSeek",
    "deepseek-ai": "DeepSeek",
    "google": "Google",
    "meta": "Meta",
    "meta-llama": "Meta",
    "microsoft": "Microsoft",
    "mistral": "Mistral",
    "mistralai": "Mistral",
    "moonshot": "Moonshot",
    "moonshotai": "Moonshot",
    "nvidia": "NVIDIA",
    "openai": "OpenAI",
    "qwen": "Qwen",
    "qwenlm": "Qwen",
    "x-ai": "xAI",
    "z-ai": "Z.ai",
  };

  return ownerLabels[owner] || owner || null;
}

function companyFromProvider(provider) {
  const labels = {
    anthropic: "Anthropic",
    gemini: "Google",
    google: "Google",
    lerouter: "PromptRail",
    openai: "OpenAI",
  };

  return labels[String(provider || "").toLowerCase()] || null;
}

function providerFromModel(modelId) {
  return modelLabFromModel(modelId) || "unknown";
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function parseMetadata(value) {
  if (!value) {
    return null;
  }

  if (typeof value === "object") {
    return value;
  }

  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

function bodyValue(body, camelName, snakeName, fallback = undefined) {
  if (body?.[camelName] !== undefined) {
    return body[camelName];
  }
  if (body?.[snakeName] !== undefined) {
    return body[snakeName];
  }
  return fallback;
}

function shouldStoreUsageLog({ kind, operation, metadata }) {
  if (metadata.status === "started") {
    return false;
  }
  if (kind !== "routing_operation") {
    return true;
  }

  const operationName = String(operation || "");
  const source = String(metadata.source || "");
  if (operationName.endsWith("_started")) {
    return false;
  }
  if (source === "hermes_lerouter_setup") {
    return false;
  }
  return true;
}

function enrichUsageLog(row) {
  const metadata = parseMetadata(row.metadata);
  const attempts = Array.isArray(metadata?.provider_attempts) ? metadata.provider_attempts : [];
  const labs = unique(
    attempts.map((attempt) => (
      modelLabFromModel(attempt?.model_id || attempt?.modelId)
      || companyFromProvider(attempt?.provider)
    )),
  );
  const modelLab = modelLabFromModel(row.modelId) || companyFromProvider(row.provider);

  return {
    ...row,
    success: Boolean(row.success),
    metadata,
    modelLab,
    modelCompany: modelLab,
    triggeredLabs: labs.length ? labs : [modelLab].filter(Boolean),
    triggeredProviders: labs.length ? labs : [modelLab].filter(Boolean),
  };
}

export async function GET(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(request.url);
  const limit = Math.min(5000, Math.max(1, asNumber(searchParams.get("limit"), 20)));
  const logs = (await listUsageLogs({ userId: session.user.id, limit })).map(enrichUsageLog);

  return Response.json({ logs, localLogs: [], mongoLogs: logs });
}

export async function POST(request) {
  const access = await getAccessContext(request);

  if (!access?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json();
  const metadata = body.metadata && typeof body.metadata === "object" ? body.metadata : {};
  const operation = bodyValue(body, "operation", "operation", metadata.operation || metadata.event || null);
  const kind = metadata.kind || (operation ? "routing_operation" : "completion");
  const isRoutingOperation = kind === "routing_operation";
  const finalRequestSpendUsd = isRoutingOperation
    ? 0
    : Math.max(0, asNumber(bodyValue(body, "spendUsd", "spend_usd", 0), 0));
  const billing = usageBillingMetadata(finalRequestSpendUsd);
  const spendUsd = isRoutingOperation ? 0 : billing.routingFeeUsd;
  const routeId = String(bodyValue(body, "routeId", "route_id", access.routeId || access.user.routeId || "default"));
  const routeNameValue = bodyValue(body, "routeName", "route_name", null);
  const routeName = routeNameValue ? String(routeNameValue) : null;
  const modelIdValue = bodyValue(body, "modelId", "model_id", null);
  const modelId = modelIdValue ? String(modelIdValue) : null;
  const provider = body.provider ? String(body.provider).trim().toLowerCase() : providerFromModel(modelId);
  const success = body.success === undefined ? true : Boolean(body.success);
  const user = await ensureUserDefaults(access.user);
  const modelLab = modelLabFromModel(modelId);

  if (!user) {
    return Response.json({ error: "User not found." }, { status: 404 });
  }

  const nextMetadata = {
    ...metadata,
    kind,
    operation,
    modelLab,
    billing: {
      ...(metadata.billing && typeof metadata.billing === "object" ? metadata.billing : {}),
      ...billing,
    },
    source: metadata.source || (access.type === "api_key" ? "api_key" : "dashboard"),
  };

  if (!shouldStoreUsageLog({ kind, operation, metadata: nextMetadata })) {
    return Response.json({ ignored: true, reason: "internal_routing_operation" });
  }

  try {
    const preUsageAutoTopUp = spendUsd > 0 && Number(user.budgetRemainingUsd || 0) + 0.000001 < spendUsd
      ? await triggerAutoTopUp(user.id)
      : { triggered: false };
    const usage = await insertUsageAndUpdateUser({
      user: access.user,
      routeId,
      routeName,
      provider,
      modelId,
      success,
      spendUsd,
      metadata: nextMetadata,
      isRoutingOperation,
    });

    const postUsageAutoTopUp = spendUsd > 0 ? await triggerAutoTopUp(access.user.id) : { triggered: false };
    const autoTopUp = postUsageAutoTopUp.triggered ? postUsageAutoTopUp : preUsageAutoTopUp;

    return Response.json({ usage, autoTopUp });
  } catch (error) {
    if (error.status === 402) {
      return Response.json(
        {
          error: error.message,
          ...error.details,
        },
        { status: 402 },
      );
    }
    throw error;
  }
}
