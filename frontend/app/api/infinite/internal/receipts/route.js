import { timingSafeEqual } from "node:crypto";
import { randomUUID } from "node:crypto";
import {
  boundedJsonErrorResponse,
  readBoundedJson,
} from "../../../../lib/boundedJsonBody.js";
import { requireMongoDatabase } from "../../../../lib/mongo.js";
import { serverEnv } from "../../../../lib/serverEnv.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CAPACITY_CLASSES = new Set([
  "managed_free",
  "user_free",
  "user_subscription",
]);

function serviceAuthorized(request) {
  const expected = serverEnv("LEROUTER_INTERNAL_SERVICE_TOKEN");
  const authorization = request.headers.get("authorization") || "";
  if (!expected || !authorization.startsWith("Bearer ")) return false;
  const received = authorization.slice(7);
  if (received.length !== expected.length) return false;
  return timingSafeEqual(Buffer.from(received), Buffer.from(expected));
}

function sanitizeShadowDecision(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const candidateIds = Array.isArray(input.candidate_ids)
    ? [...new Set(input.candidate_ids.map(String))].slice(0, 50)
    : [];
  const allowedIds = new Set(candidateIds);
  const capacityClasses = {};
  const predictedSuccess = {};
  const rejectionReasons = {};
  for (const candidateId of candidateIds) {
    const capacityClass = String(input.capacity_classes?.[candidateId] || "");
    if (!CAPACITY_CLASSES.has(capacityClass)) {
      throw new Error("Invalid shadow capacity class.");
    }
    if (Object.hasOwn(input.predicted_success || {}, candidateId)) {
      const prediction = Number(input.predicted_success[candidateId]);
      if (!Number.isFinite(prediction) || prediction < 0 || prediction > 1) {
        throw new Error("Invalid shadow predicted success.");
      }
      predictedSuccess[candidateId] = prediction;
    }
    capacityClasses[candidateId] = capacityClass;
  }
  for (const [candidateId, reasons] of Object.entries(
    input.capability_rejection_reasons || {},
  )) {
    if (!allowedIds.has(candidateId) || !Array.isArray(reasons)) continue;
    rejectionReasons[candidateId] = reasons.map(String).slice(0, 20);
  }
  const selected = String(input.selected_hypothetical_route || "");
  if (!allowedIds.has(selected)) {
    throw new Error("Invalid shadow selected route.");
  }
  return {
    policy_version: String(input.policy_version || ""),
    candidate_ids: candidateIds,
    capacity_classes: capacityClasses,
    predicted_success: predictedSuccess,
    selected_hypothetical_route: selected,
    decision_latency_ms: Math.max(0, Number(input.decision_latency_ms || 0)),
    capability_rejection_reasons: rejectionReasons,
  };
}

function sanitizeReceipt(input) {
  const finiteNonNegative = (value, field) => {
    const parsed = Number(value ?? 0);
    if (!Number.isFinite(parsed) || parsed < 0) {
      throw new Error(`Invalid ${field}.`);
    }
    return parsed;
  };
  const capacityClass = String(input?.capacity_class || "");
  if (!String(input?.route_id || "").startsWith("route_")) {
    throw new Error("Invalid route ID.");
  }
  if (!input?.tenant_id || !input?.policy_version || !input?.catalog_version) {
    throw new Error("Receipt identity and versions are required.");
  }
  if (!CAPACITY_CLASSES.has(capacityClass)) {
    throw new Error("Invalid capacity class.");
  }
  const attempts = Array.isArray(input.attempts)
    ? input.attempts.slice(0, 3)
    : [];
  const shadowDecision = sanitizeShadowDecision(input.shadow_decision);
  return {
    id: `receipt_${randomUUID()}`,
    route_id: String(input.route_id),
    tenant_id: String(input.tenant_id),
    policy_version: String(input.policy_version),
    catalog_version: String(input.catalog_version),
    virtual_model: "promptrail/infinite",
    selected_model: String(input.selected_model || ""),
    executed_model: String(input.executed_model || ""),
    capacity_class: capacityClass,
    premium_used: Boolean(input.premium_used),
    degraded_free_only: Boolean(input.degraded_free_only),
    attempts: attempts.map((attempt) => ({
      provider: String(attempt?.provider || ""),
      connection_id_hash: String(attempt?.connection_id_hash || ""),
      result: String(attempt?.result || "unknown"),
      dispatched: attempt?.dispatched === true,
      latency_ms: finiteNonNegative(attempt?.latency_ms, "attempt latency"),
    })),
    input_tokens: finiteNonNegative(input.input_tokens, "input token count"),
    output_tokens: finiteNonNegative(input.output_tokens, "output token count"),
    estimated_cost_usd: finiteNonNegative(input.estimated_cost_usd, "estimated cost"),
    decision_latency_ms: finiteNonNegative(input.decision_latency_ms, "decision latency"),
    pre_dispatch_latency_ms: finiteNonNegative(input.pre_dispatch_latency_ms, "pre-dispatch latency"),
    total_latency_ms: finiteNonNegative(input.total_latency_ms, "total latency"),
    ...(shadowDecision ? { shadow_decision: shadowDecision } : {}),
    created_at: new Date(),
  };
}

export async function POST(request) {
  if (!serviceAuthorized(request)) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }
  let body;
  try {
    body = await readBoundedJson(request, { maxBytes: 128 * 1024 });
  } catch (error) {
    return boundedJsonErrorResponse(error);
  }
  let receipt;
  try {
    receipt = sanitizeReceipt(body);
  } catch {
    return Response.json(
      { error: "Invalid receipt." },
      { status: 400, headers: { "Cache-Control": "no-store" } },
    );
  }
  try {
    const database = await requireMongoDatabase();
    await database
      .collection(
        serverEnv(
          "LEROUTER_INFINITE_RECEIPT_COLLECTION",
          "infinite_execution_receipts",
        ),
      )
      .insertOne(receipt);
    return Response.json({ accepted: true }, { status: 202 });
  } catch (error) {
    if (error?.code === 11000) {
      return Response.json(
        { accepted: true, duplicate: true },
        { status: 202, headers: { "Cache-Control": "no-store" } },
      );
    }
    return Response.json(
      { error: "Receipt storage unavailable." },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }
}

export { sanitizeReceipt };
