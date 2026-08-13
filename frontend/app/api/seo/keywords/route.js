import { serverEnv } from "../../../lib/serverEnv.js";
import { requireMongoDatabase } from "../../../lib/mongo.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const modes = new Set(["standard", "comparison", "review", "list"]);

function authorized(request) {
  const secret = serverEnv("SEO_ADMIN_SECRET", serverEnv("CRON_SECRET"));
  return Boolean(secret) && request.headers.get("authorization") === `Bearer ${secret}`;
}

function keywordKey(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

export async function GET(request) {
  if (!authorized(request)) return new Response("Unauthorized", { status: 401 });
  const database = await requireMongoDatabase();
  const queue = await database.collection(serverEnv("LEROUTER_BLOG_KEYWORD_COLLECTION", "blog_keyword_queue"))
    .find({}, { projection: { keyword: 1, cluster: 1, mode: 1, priority: 1, angle: 1, status: 1, slug: 1, runId: 1, createdAt: 1, completedAt: 1, error: 1 } })
    .sort({ status: 1, priority: -1, createdAt: 1 })
    .toArray();
  return Response.json({ queue: queue.map(({ _id, ...item }) => ({ id: String(_id), ...item })) });
}

export async function POST(request) {
  if (!authorized(request)) return new Response("Unauthorized", { status: 401 });
  const body = await request.json().catch(() => ({}));
  const keyword = String(body.keyword || "").trim();
  const mode = String(body.mode || "standard");
  const priority = Number(body.priority ?? 50);
  if (keyword.length < 3 || keyword.length > 120) return Response.json({ error: "keyword must contain 3-120 characters" }, { status: 422 });
  if (!modes.has(mode)) return Response.json({ error: "mode must be standard, comparison, review, or list" }, { status: 422 });
  if (!Number.isFinite(priority) || priority < 0 || priority > 1000) return Response.json({ error: "priority must be between 0 and 1000" }, { status: 422 });

  const database = await requireMongoDatabase();
  const collection = database.collection(serverEnv("LEROUTER_BLOG_KEYWORD_COLLECTION", "blog_keyword_queue"));
  await collection.createIndex({ keywordKey: 1 }, { unique: true });
  const key = keywordKey(keyword);
  const existing = await collection.findOne({ keywordKey: key });
  if (existing) return Response.json({ error: "keyword already exists", id: String(existing._id), status: existing.status }, { status: 409 });
  const document = { keyword, keywordKey: key, cluster: String(body.cluster || "Uncategorized").trim(), mode, priority, angle: String(body.angle || "").trim(), status: "queued", source: "manual_keyword_strategy", createdAt: new Date() };
  const result = await collection.insertOne(document);
  return Response.json({ ok: true, id: String(result.insertedId), ...document }, { status: 201 });
}
