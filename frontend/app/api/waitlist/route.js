import { requireMongoDatabase } from "../../lib/mongo.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const WAITLIST_COLLECTION = "Wait List";
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

let indexReady;

function normalizeEmail(value) {
  return String(value || "").trim().toLowerCase();
}

async function waitlistCollection() {
  const database = await requireMongoDatabase();
  const collection = database.collection(WAITLIST_COLLECTION);

  if (!indexReady) {
    indexReady = collection.createIndex({ email: 1 }, { unique: true });
  }

  await indexReady;
  return collection;
}

export async function POST(request) {
  let body;

  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "Invalid JSON body." }, { status: 400 });
  }

  const email = normalizeEmail(body?.email);

  if (!EMAIL_PATTERN.test(email)) {
    return Response.json({ error: "Enter a valid email address." }, { status: 400 });
  }

  const now = new Date();
  const collection = await waitlistCollection();

  await collection.updateOne(
    { email },
    {
      $set: {
        email,
        updatedAt: now,
        source: "landing_page",
      },
      $setOnInsert: {
        createdAt: now,
      },
    },
    { upsert: true },
  );

  return Response.json({ ok: true });
}
