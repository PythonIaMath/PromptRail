import { randomUUID } from "node:crypto";
import { serverEnv } from "./serverEnv.js";
import { requireMongoDatabase } from "./mongo.js";
import {
  decryptProviderCredentials,
  encryptProviderCredentials,
} from "./providerCredentialEnvelope.js";

export const CAPACITY_CLASSES = new Set([
  "managed_free",
  "user_free",
  "user_subscription",
]);
export const ADMISSION_STATUSES = new Set([
  "hosted_allowed",
  "byok_only",
  "personal_only",
  "disabled",
]);

function collection(database) {
  return database.collection(
    serverEnv(
      "LEROUTER_INFINITE_PROVIDER_CONNECTION_COLLECTION",
      "infinite_provider_connections",
    ),
  );
}

function providerAllowlist() {
  const providers = serverEnv("PROMPTRAIL_INFINITE_PROVIDER_ALLOWLIST")
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
  return new Set(providers);
}

export function validateProviderConnectionInput(input) {
  const provider = String(input?.provider || "")
    .trim()
    .toLowerCase();
  const capacityClass = String(input?.capacityClass || "");
  const admissionStatus = String(input?.admissionStatus || "");
  const apiKey = String(input?.apiKey || "").trim();
  if (!provider || !providerAllowlist().has(provider)) {
    throw new Error("Provider is not admitted for PromptRail Infinite.");
  }
  if (!CAPACITY_CLASSES.has(capacityClass)) {
    if (capacityClass === "user_paid") {
      throw new Error("Paid API capacity is not supported by PromptRail Infinite.");
    }
    throw new Error("Invalid provider capacity class.");
  }
  if (!ADMISSION_STATUSES.has(admissionStatus)) {
    throw new Error("Invalid hosted admission status.");
  }
  if (admissionStatus === "personal_only" || admissionStatus === "disabled") {
    throw new Error(
      "Provider admission status is not valid for hosted Infinite execution.",
    );
  }
  if (capacityClass === "managed_free") {
    throw new Error(
      "Managed capacity cannot be created through a user connection API.",
    );
  }
  if (capacityClass === "user_subscription") {
    throw new Error(
      "Subscription credentials require a reviewed OAuth connection flow.",
    );
  }
  if (capacityClass !== "user_free") {
    throw new Error("User API-key connections must use verified free capacity.");
  }
  if (admissionStatus !== "byok_only") {
    throw new Error("User API-key connections must use byok_only admission.");
  }
  if (!apiKey || apiKey.length > 16384) {
    throw new Error("A valid provider API key is required.");
  }
  return { provider, capacityClass, admissionStatus, apiKey };
}

export function validateReviewedSubscriptionInput(input) {
  const provider = String(input?.provider || "")
    .trim()
    .toLowerCase();
  const credentials = input?.credentials;
  if (!provider || !providerAllowlist().has(provider)) {
    throw new Error("Provider is not admitted for PromptRail Infinite.");
  }
  if (
    !credentials ||
    typeof credentials !== "object" ||
    Array.isArray(credentials)
  ) {
    throw new Error("A reviewed subscription credential object is required.");
  }
  const serialized = JSON.stringify(credentials);
  if (
    serialized.length > 65_536 ||
    /"(?:__proto__|constructor|prototype)"\s*:/.test(serialized)
  ) {
    throw new Error("Subscription credential object is invalid.");
  }
  if (!["oauth", "access_token"].includes(String(credentials.authType || ""))) {
    throw new Error(
      "Subscription credentials must use oauth or access_token authType.",
    );
  }
  if (!String(credentials.accessToken || "").trim()) {
    throw new Error("Subscription credentials require an access token.");
  }
  return { provider, credentials: JSON.parse(serialized) };
}

export function publicProviderConnection(row) {
  return {
    id: row.id,
    provider: row.provider,
    capacityClass: row.capacityClass,
    admissionStatus: row.admissionStatus,
    status: row.status,
    credentialVersion: row.credentialVersion,
    createdAt: row.createdAt,
    updatedAt: row.updatedAt,
  };
}

export async function createInfiniteProviderConnection({ userId, input }) {
  const normalized = validateProviderConnectionInput(input);
  const database = await requireMongoDatabase();
  const id = `connection_${randomUUID()}`;
  const now = new Date();
  const credentialVersion = 1;
  const credentialEnvelope = encryptProviderCredentials({
    connectionId: id,
    userId,
    provider: normalized.provider,
    credentials: { apiKey: normalized.apiKey },
  });
  const row = {
    id,
    userId,
    provider: normalized.provider,
    capacityClass: normalized.capacityClass,
    admissionStatus: normalized.admissionStatus,
    status: "active",
    credentialVersion,
    credentialEnvelope,
    createdAt: now,
    updatedAt: now,
  };
  await collection(database).insertOne(row);
  return publicProviderConnection(row);
}

export async function createReviewedSubscriptionConnection({ userId, input }) {
  if (!userId) throw new Error("A PromptRail tenant is required.");
  const normalized = validateReviewedSubscriptionInput(input);
  const database = await requireMongoDatabase();
  const id = `connection_${randomUUID()}`;
  const now = new Date();
  const credentialVersion = 1;
  const credentialEnvelope = encryptProviderCredentials({
    connectionId: id,
    userId,
    provider: normalized.provider,
    credentials: normalized.credentials,
  });
  const row = {
    id,
    userId,
    provider: normalized.provider,
    capacityClass: "user_subscription",
    admissionStatus: "hosted_allowed",
    status: "active",
    credentialVersion,
    credentialEnvelope,
    source: "reviewed_operator_import",
    createdAt: now,
    updatedAt: now,
  };
  await collection(database).insertOne(row);
  return publicProviderConnection(row);
}

export async function listInfiniteProviderConnections(userId) {
  const database = await requireMongoDatabase();
  const rows = await collection(database)
    .find({ userId }, { projection: { _id: 0, credentialEnvelope: 0 } })
    .sort({ createdAt: -1 })
    .toArray();
  return rows.map(publicProviderConnection);
}

export async function revokeInfiniteProviderConnection({
  userId,
  connectionId,
}) {
  const database = await requireMongoDatabase();
  const now = new Date();
  const result = await collection(database).updateOne(
    { id: connectionId, userId, status: "active" },
    { $set: { status: "revoked", revokedAt: now, updatedAt: now } },
  );
  return result.modifiedCount === 1;
}

export async function rotateInfiniteProviderConnection({
  userId,
  connectionId,
  apiKey,
}) {
  const normalizedApiKey = String(apiKey || "").trim();
  if (!normalizedApiKey || normalizedApiKey.length > 16384) {
    throw new Error("A valid provider API key is required.");
  }
  const database = await requireMongoDatabase();
  const connections = collection(database);
  const row = await connections.findOne(
    { id: connectionId, userId, status: "active" },
    { projection: { _id: 0 } },
  );
  if (!row) {
    throw new Error("Provider connection is unavailable.");
  }
  const credentialVersion = Number(row.credentialVersion || 0) + 1;
  const credentialEnvelope = encryptProviderCredentials({
    connectionId: row.id,
    userId: row.userId,
    provider: row.provider,
    credentials: { apiKey: normalizedApiKey },
  });
  const now = new Date();
  const result = await connections.findOneAndUpdate(
    {
      id: connectionId,
      userId,
      status: "active",
      credentialVersion: row.credentialVersion,
    },
    {
      $set: {
        credentialVersion,
        credentialEnvelope,
        rotatedAt: now,
        updatedAt: now,
      },
    },
    { projection: { _id: 0, credentialEnvelope: 0 }, returnDocument: "after" },
  );
  const updated = result?.value || result;
  if (!updated) {
    throw new Error("Provider connection changed during credential rotation.");
  }
  return publicProviderConnection(updated);
}

export async function hydrateInfiniteProviderConnections({
  userId,
  connectionIds,
}) {
  const uniqueIds = [...new Set(connectionIds.map(String))];
  if (!userId || uniqueIds.length === 0 || uniqueIds.length > 20) {
    throw new Error(
      "A tenant and between 1 and 20 connection IDs are required.",
    );
  }
  const database = await requireMongoDatabase();
  const rows = await collection(database)
    .find(
      {
        id: { $in: uniqueIds },
        status: "active",
        capacityClass: {
          $in: ["managed_free", "user_free", "user_subscription"],
        },
        $or: [
          { userId },
          {
            userId: null,
            capacityClass: "managed_free",
            admissionStatus: "hosted_allowed",
          },
        ],
      },
      { projection: { _id: 0 } },
    )
    .toArray();
  if (rows.length !== uniqueIds.length) {
    throw new Error("One or more provider connections are unavailable.");
  }
  const byId = new Map(rows.map((row) => [row.id, row]));
  return uniqueIds.map((id) => {
    const row = byId.get(id);
    const credentials = decryptProviderCredentials({
      connectionId: row.id,
      userId: row.userId,
      provider: row.provider,
      envelope: row.credentialEnvelope,
    });
    return {
      id: row.id,
      provider: row.provider,
      capacityClass: row.capacityClass,
      admissionStatus: row.admissionStatus,
      credentialVersion: row.credentialVersion,
      authType: String(credentials.authType || "apikey"),
      credentials,
      ...(credentials.apiKey ? { apiKey: credentials.apiKey } : {}),
    };
  });
}
