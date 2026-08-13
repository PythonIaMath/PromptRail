import { requireMongoDatabase } from "../../../lib/mongo.js";
import { serverEnv } from "../../../lib/serverEnv.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  if (process.env.NODE_ENV !== "development") {
    return Response.json({ error: "Not found" }, { status: 404 });
  }
  const database = await requireMongoDatabase();
  const receipts = await database.collection(serverEnv("LEROUTER_INFINITE_RECEIPT_COLLECTION", "infinite_execution_receipts"))
    .find({}, { projection: { _id: 0, tenant_id: 0, "attempts.connection_id_hash": 0 } }).sort({ created_at: -1 }).limit(200).toArray();
  return Response.json({ receipts }, { headers: { "Cache-Control": "no-store" } });
}
