import { MongoClient } from "mongodb";
import { serverEnv } from "./serverEnv.js";

const DB_NAME = serverEnv("LEROUTER_MODEL_PROFILE_DB", "lerouter");
const USER_BUDGET_COLLECTION = serverEnv("LEROUTER_USER_BUDGET_COLLECTION", "user_budget_states");
const ROUTE_POLICY_COLLECTION = serverEnv("LEROUTER_ROUTE_POLICY_COLLECTION", "route_policies");
const ROUTE_USAGE_COLLECTION = serverEnv("LEROUTER_ROUTE_USAGE_COLLECTION", "route_usage_logs");
const API_KEY_COLLECTION = serverEnv("LEROUTER_API_KEY_COLLECTION", "api_keys");
const CHECKOUT_SESSION_COLLECTION = serverEnv("LEROUTER_CHECKOUT_SESSION_COLLECTION", "stripe_checkout_sessions");
const SETUP_LINK_COLLECTION = serverEnv("LEROUTER_SETUP_LINK_COLLECTION", "setup_links");
const DEVICE_SESSION_COLLECTION = serverEnv("LEROUTER_DEVICE_SESSION_COLLECTION", "device_sessions");
const INSTALLATION_COLLECTION = serverEnv("LEROUTER_INSTALLATION_COLLECTION", "installations");
const BLOG_POST_COLLECTION = serverEnv("LEROUTER_BLOG_POST_COLLECTION", "blog_posts");

let mongoClientPromise;
let mongoClient;

const mongoOptions = {
  serverSelectionTimeoutMS: 8000,
  connectTimeoutMS: 8000,
  socketTimeoutMS: 8000,
};

export function isMongoConfigured() {
  return Boolean(serverEnv("MONGODB_URI"));
}

export function getMongoClientInstance() {
  const uri = serverEnv("MONGODB_URI");
  if (!uri) {
    return null;
  }

  if (!mongoClient) {
    mongoClient = new MongoClient(uri, mongoOptions);
  }

  return mongoClient;
}

export function requireMongoClientInstance() {
  const client = getMongoClientInstance();
  if (!client) {
    throw new Error("MONGODB_URI is required. PromptRail is configured to store runtime data in MongoDB.");
  }
  return client;
}

export async function getMongoClient() {
  const client = getMongoClientInstance();
  if (!client) {
    return null;
  }

  if (!mongoClientPromise) {
    mongoClientPromise = client.connect().catch((error) => {
      mongoClientPromise = undefined;
      throw error;
    });
  }
  return mongoClientPromise;
}

export function getMongoDatabaseHandle() {
  const client = getMongoClientInstance();
  return client ? client.db(DB_NAME) : null;
}

export async function getMongoDatabase() {
  return getMongoDatabaseHandle();
}

export function requireMongoDatabaseHandle() {
  const database = getMongoDatabaseHandle();
  if (!database) {
    throw new Error("MONGODB_URI is required. PromptRail is configured to store runtime data in MongoDB.");
  }
  return database;
}

export async function requireMongoDatabase() {
  const database = await getMongoDatabase();
  if (!database) {
    throw new Error("MONGODB_URI is required. PromptRail is configured to store runtime data in MongoDB.");
  }
  return database;
}

export async function getMongoCollections() {
  const database = await getMongoDatabase();
  if (!database) {
    return null;
  }

  return {
    userBudgets: database.collection(USER_BUDGET_COLLECTION),
    routePolicies: database.collection(ROUTE_POLICY_COLLECTION),
    usageLogs: database.collection(ROUTE_USAGE_COLLECTION),
    apiKeys: database.collection(API_KEY_COLLECTION),
    checkoutSessions: database.collection(CHECKOUT_SESSION_COLLECTION),
    setupLinks: database.collection(SETUP_LINK_COLLECTION),
    deviceSessions: database.collection(DEVICE_SESSION_COLLECTION),
    installations: database.collection(INSTALLATION_COLLECTION),
    blogPosts: database.collection(BLOG_POST_COLLECTION),
  };
}

export async function requireMongoCollections() {
  const database = await requireMongoDatabase();
  return {
    users: database.collection("user"),
    sessions: database.collection("session"),
    accounts: database.collection("account"),
    verifications: database.collection("verification"),
    userBudgets: database.collection(USER_BUDGET_COLLECTION),
    routePolicies: database.collection(ROUTE_POLICY_COLLECTION),
    usageLogs: database.collection(ROUTE_USAGE_COLLECTION),
    apiKeys: database.collection(API_KEY_COLLECTION),
    checkoutSessions: database.collection(CHECKOUT_SESSION_COLLECTION),
    setupLinks: database.collection(SETUP_LINK_COLLECTION),
    deviceSessions: database.collection(DEVICE_SESSION_COLLECTION),
    installations: database.collection(INSTALLATION_COLLECTION),
    blogPosts: database.collection(BLOG_POST_COLLECTION),
  };
}

export function serializeMongoDocument(document) {
  if (!document) {
    return null;
  }

  const { _id, ...rest } = document;
  return JSON.parse(JSON.stringify(rest));
}
