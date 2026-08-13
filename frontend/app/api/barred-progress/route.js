import fs from "node:fs/promises";
import path from "node:path";
import { auth } from "../../lib/auth.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ROOT = path.resolve(process.cwd(), "..");
const DATASET_ROOT = path.join(ROOT, "datasets", "e5_sources");
const LOG_ROOT = path.join(ROOT, "logs");

async function getSession(request) {
  return auth.api.getSession({
    headers: request.headers,
  });
}

function safeRunName(value) {
  const run = String(value || "").trim();
  return /^[A-Za-z0-9_.-]{3,160}$/.test(run) ? run : "";
}

async function exists(filePath) {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function readJson(filePath, fallback = null) {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

async function countLines(filePath) {
  try {
    const text = await fs.readFile(filePath, "utf8");
    if (!text.trim()) {
      return 0;
    }
    return text.trimEnd().split(/\r?\n/).length;
  } catch {
    return 0;
  }
}

async function latestRunName() {
  const entries = await fs.readdir(DATASET_ROOT, { withFileTypes: true }).catch(() => []);
  const candidates = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || !entry.name.endsWith("_barred_contrast")) {
      continue;
    }
    const statePath = path.join(DATASET_ROOT, entry.name, "barred_state.json");
    const stat = await fs.stat(statePath).catch(() => null);
    if (stat) {
      candidates.push({ name: entry.name.replace(/_barred_contrast$/, ""), mtimeMs: stat.mtimeMs });
    }
  }
  candidates.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return candidates[0]?.name || "";
}

function parseBarredEvents(logText) {
  return logText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("{") && line.includes('"barred_progress"'))
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

export async function GET(request) {
  const session = await getSession(request);
  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { searchParams } = new URL(request.url);
  const requestedRun = safeRunName(searchParams.get("run"));
  const runName = requestedRun || (await latestRunName());
  if (!runName) {
    return Response.json({ error: "No BARRED run found." }, { status: 404 });
  }

  const barredDir = path.join(DATASET_ROOT, `${runName}_barred_contrast`);
  const statePath = path.join(barredDir, "barred_state.json");
  const metadataPath = path.join(barredDir, "metadata.json");
  const rowsPath = path.join(barredDir, "source_rows.jsonl");
  const rejectedPath = path.join(barredDir, "barred_rejected.json");
  const logPath = path.join(LOG_ROOT, `${runName}.log`);

  const [state, metadata, rowCount, rejected, logExists] = await Promise.all([
    readJson(statePath, null),
    readJson(metadataPath, null),
    countLines(rowsPath),
    readJson(rejectedPath, []),
    exists(logPath),
  ]);
  const logText = logExists ? await fs.readFile(logPath, "utf8").catch(() => "") : "";
  const events = parseBarredEvents(logText);
  const latest = state || events.at(-1) || {};
  const target = Number(metadata?.rows || latest.target || 2000);

  return Response.json({
    runName,
    barredDir,
    logPath: logExists ? logPath : "",
    target,
    rowCount,
    rejectedCount: Array.isArray(rejected) ? rejected.length : Number(latest.rejected || 0),
    latest,
    metadata,
    events: events.slice(-300),
    updatedAt: new Date().toISOString(),
  });
}
