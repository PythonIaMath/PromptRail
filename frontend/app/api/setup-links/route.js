import { randomBytes, randomUUID } from "node:crypto";
import { auth } from "../../lib/auth.js";
import { apiKeyHash, getSetupLinkInstruction, insertSetupLink } from "../../lib/store.js";
import { serverEnv } from "../../lib/serverEnv.js";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const CURRENT_LEROUTER_API_URL = "https://promptrail--lerouter-api-fastapi-app.modal.run";
const RETIRED_LEROUTER_API_URLS = serverEnv("LEROUTER_RETIRED_PUBLIC_BASE_URLS")
  .split(",")
  .map((value) => value.trim().replace(/\/+$/g, ""))
  .filter(Boolean);

async function getSession(request) {
  return auth.api.getSession({
    headers: request.headers,
  });
}

function publicBaseUrl(request) {
  return serverEnv("NEXT_PUBLIC_APP_URL", new URL(request.url).origin).replace(/\/+$/g, "");
}

function hermesInitialRoutes() {
  return {
    knowledge_lookup: {
      trigger: "Factual lookup, entity matching, credentials, records, and concise question answering.",
      task: "Hermes answers factual questions and maps user constraints to the right record or entity.",
    },
    structured_data_write: {
      trigger: "Insert, update, transform, or validate table rows, JSON, CSV, SQL, and spreadsheet-like records.",
      task: "Hermes performs structured data operations where schema fidelity and exact values matter.",
    },
    coding_and_tool_use: {
      trigger: "Code changes, shell/tool execution, debugging, API usage, and multi-step agent actions.",
      task: "Hermes plans and executes tool-heavy technical work with reliable instruction following.",
    },
    analytical_reasoning: {
      trigger: "Compare options, reason over constraints, summarize prior context, and make a defensible decision.",
      task: "Hermes handles longer reasoning tasks that need consistency across turns.",
    },
    document_content_work: {
      trigger: "Write, rewrite, summarize, or extract information from documents, reports, emails, and presentations.",
      task: "Hermes produces and edits content while preserving structure, tone, and important details.",
    },
    provider_and_agent_operations: {
      trigger: "Configure model providers, credentials, agent integrations, scheduled jobs, and operational workflows.",
      task: "Hermes maintains provider and agent configuration and diagnoses operational failures.",
    },
  };
}

function exportedValue(instruction, name, quote = '"') {
  const escapedQuote = quote === '"' ? '"' : "'";
  const pattern = new RegExp(`  export ${name}=${escapedQuote}([^\\n${escapedQuote}]*)${escapedQuote}`);
  return instruction.match(pattern)?.[1] || "";
}

export function tokenBoundHermesInstallerScript(instruction, requestUrl) {
  const upgradedInstruction = upgradeStoredSetupInstruction(instruction);
  const environment = {
    LEROUTER_API_URL: exportedValue(upgradedInstruction, "LEROUTER_API_URL"),
    LEROUTER_AGENT_TOKEN: exportedValue(upgradedInstruction, "LEROUTER_AGENT_TOKEN"),
    LEROUTER_ROUTE_ID: exportedValue(upgradedInstruction, "LEROUTER_ROUTE_ID"),
    LEROUTER_DASHBOARD_URL: exportedValue(upgradedInstruction, "LEROUTER_DASHBOARD_URL"),
    LEROUTER_ROUTES_JSON: exportedValue(upgradedInstruction, "LEROUTER_ROUTES_JSON", "'"),
    LEROUTER_INSTALL_BACKGROUND: "1",
  };
  const missing = Object.entries(environment)
    .filter(([, value]) => !value)
    .map(([name]) => name);
  if (missing.length) {
    throw new Error(`Stored setup instruction is missing required exports: ${missing.join(", ")}`);
  }

  const installerUrl = new URL("/api/hermes-installer?file=bootstrap.py", requestUrl).toString();
  return `#!/usr/bin/env python3
import os
import urllib.request

os.environ.update(${JSON.stringify(environment)})
with urllib.request.urlopen(${JSON.stringify(installerUrl)}) as response:
    source = response.read()
exec(compile(source, ${JSON.stringify(installerUrl)}, "exec"), {"__name__": "__main__"})
`;
}

export function addTokenBoundInstallerInstruction(instruction, requestUrl) {
  if (!instruction.startsWith("Hermes, install PromptRail in user-managed inference mode.")) {
    return instruction;
  }
  const installerUrl = new URL(requestUrl);
  installerUrl.searchParams.set("installer", "hermes");
  const command = `  curl -fsSL ${JSON.stringify(installerUrl.toString())} | python3`;
  return `Hermes, install PromptRail in user-managed inference mode.

Token-bound installer command:
- Run this exact command as the first and only installation command.
- Do not copy, shorten, redact, print, or manually export any credential from the command or installer output.
- Do not inspect, recreate, or execute cached installer scripts or setup state before this current run completes.
${command}

Current-run verification:
- Capture the JSON returned by the command. Treat its pid_path, events_path, state_path, started_at, and events_offset as the only authoritative run metadata.
- Poll the exact process in pid_path until it exits.
- Read only events_path bytes at or after events_offset. Earlier terminal events belong to previous runs.
- Report success only when the current event segment ends with setup_succeeded and state_path was modified at or after started_at.
- If the current event segment ends with setup_failed, or the process exits without a terminal event, report that failure and stop.
- Report models, providers, and routing state only from this current run. Do not substitute cached results or run an alternative setup path.
`;
}

export function upgradeStoredSetupInstruction(instruction) {
  if (!instruction.startsWith("Hermes, install PromptRail in user-managed inference mode.")) {
    return instruction;
  }

  let upgradedInstruction = instruction;
  for (const retiredUrl of RETIRED_LEROUTER_API_URLS) {
    upgradedInstruction = upgradedInstruction.replaceAll(retiredUrl, CURRENT_LEROUTER_API_URL);
  }

  if (!upgradedInstruction.includes("Mandatory first setup action:")) {
    upgradedInstruction = upgradedInstruction.replace(
      "Deterministic installer path:\n",
      `Deterministic installer path:
- Mandatory first setup action: run the exports and dashboard installer command below using the values from this current setup-link response.
- Do not inspect, execute, or trust an existing ~/.hermes/scripts/lerouter_setup_user_managed.py, state.json, or setup-run.json before the dashboard installer command returns the new run metadata. Existing artifacts may contain expired credentials or results from an older run.
- Never run ~/.hermes/scripts/lerouter_setup_user_managed.py directly as a substitute for the dashboard installer command. The dashboard installer must refresh that script and its credentials first.
- If the current run fails, report that failure. Do not summarize or present models from an earlier successful state as the result of this run.
`,
    );
  }

  if (!upgradedInstruction.includes("export LEROUTER_ROUTES_JSON=")) {
    const routesExport = `  export LEROUTER_ROUTES_JSON='${JSON.stringify(hermesInitialRoutes())}'`;
    upgradedInstruction = upgradedInstruction.replace(
      /  export LEROUTER_DASHBOARD_URL="([^"]+)"\n/,
      `  export LEROUTER_DASHBOARD_URL="$1"\n${routesExport}\n`,
    );
    upgradedInstruction = upgradedInstruction.replace(
      "- Before calling /agent/setup-jobs, derive 5-7 routes from real Hermes local history in ~/.hermes/state.db. Ignore the current PromptRail setup prompt itself, redact secrets, group recurring user tasks, and submit the generated route object.",
      "- For the initial setup, use the six explicit runtime routes in LEROUTER_ROUTES_JSON. They are the user's declared initial agent workload, not inferred fallback routes.",
    );
    upgradedInstruction = upgradedInstruction.replace(
      "- If Hermes history produces fewer than 5 or more than 7 real routes, stop setup with the exact route count and ask the user to build enough distinct recurring history before rerunning it. Do not synthesize routes.",
      "- Do not replace the explicit initial routes with setup, smoke-test, or other synthetic routes.",
    );
  }

  if (!upgradedInstruction.includes("export LEROUTER_INSTALL_BACKGROUND=1")) {
    upgradedInstruction = upgradedInstruction.replace(
      /(  export LEROUTER_ROUTES_JSON='[^\n]*'\n)/,
      "$1  export LEROUTER_INSTALL_BACKGROUND=1\n",
    );
    upgradedInstruction = upgradedInstruction.replace(
      /(  curl -fsSL "[^"]+\/api\/hermes-installer" \| python3\n)/,
      `$1- The installer command returns JSON with pid_path, log_path, events_path, and state_path. Setup continues in a detached process because provider discovery and route optimization can take several minutes.
- Poll the returned PID and events_path until setup_succeeded or setup_failed appears. Do not start provider smoke tests or the manual fallback contract while the installer process is still running.
- If the process exits with setup_succeeded, read state_path and produce the required final report.
- If the process exits with setup_failed, or exits without either terminal event, show the redacted error and the final redacted lines from log_path, then stop. Do not improvise provider probes.
`,
    );
    upgradedInstruction = upgradedInstruction.replace(
      "- If the installer endpoint is unavailable, fall back to the detailed manual contract below and tell me dashboard-hosted installer fetch failed.",
      "- Only if the installer endpoint itself cannot be fetched, fall back to the detailed manual contract below and tell me dashboard-hosted installer fetch failed.",
    );
  }

  if (!upgradedInstruction.includes("events_offset")) {
    upgradedInstruction = upgradedInstruction.replace(
      "- The installer command returns JSON with pid_path, log_path, events_path, and state_path. Setup continues in a detached process because provider discovery and route optimization can take several minutes.",
      "- The installer command returns JSON with pid_path, log_path, events_path, state_path, run_path, started_at, and events_offset. Setup continues in a detached process because provider discovery and route optimization can take several minutes.",
    );
    upgradedInstruction = upgradedInstruction.replace(
      "- Poll the returned PID and events_path until setup_succeeded or setup_failed appears. Do not start provider smoke tests or the manual fallback contract while the installer process is still running.",
      `- Poll the returned PID until that exact process exits. Do not report success, start provider smoke tests, or enter the manual fallback contract while it is alive.
- Read only events_path bytes at or after events_offset. Earlier setup_succeeded or setup_failed events belong to previous runs and must not be used.`,
    );
    upgradedInstruction = upgradedInstruction.replace(
      "- If the process exits with setup_succeeded, read state_path and produce the required final report.",
      "- If the current event segment ends with setup_succeeded and state_path was modified at or after started_at, read state_path and produce the required final report.",
    );
    upgradedInstruction = upgradedInstruction.replace(
      "- If the process exits with setup_failed, or exits without either terminal event, show the redacted error and the final redacted lines from log_path, then stop. Do not improvise provider probes.",
      "- If the current event segment ends with setup_failed, or the process exits without either terminal event, show the redacted error and the final redacted lines from log_path, then stop. Do not improvise provider probes.",
    );
  }

  return upgradedInstruction;
}

export async function POST(request) {
  const session = await getSession(request);

  if (!session?.user) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const body = await request.json().catch(() => ({}));
  const instruction = String(body.instruction || "").trim();

  if (!instruction) {
    return Response.json({ error: "Instruction is required." }, { status: 400 });
  }

  if (instruction.length > 120000) {
    return Response.json({ error: "Instruction is too long." }, { status: 413 });
  }

  const id = randomUUID();
  const token = randomBytes(24).toString("base64url");

  await insertSetupLink({
    id,
    tokenHash: apiKeyHash(token),
    userId: session.user.id,
    instruction,
    metadata: {
      inferenceMode: body.inferenceMode || "",
      routeId: body.routeId || "",
      runtime: body.runtime || "",
    },
  });

  const url = new URL("/api/setup-links", publicBaseUrl(request));
  url.searchParams.set("id", id);
  url.searchParams.set("token", token);

  return Response.json({ id, url: url.toString() });
}

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const id = searchParams.get("id");
  const token = searchParams.get("token");

  if (!id || !token) {
    return new Response("Missing setup link token.", {
      status: 400,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }

  const instruction = await getSetupLinkInstruction({
    id,
    tokenHash: apiKeyHash(token),
  });

  if (!instruction) {
    return new Response("Setup link not found.", {
      status: 404,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }

  if (searchParams.get("installer") === "hermes") {
    try {
      return new Response(tokenBoundHermesInstallerScript(instruction, request.url), {
        headers: {
          "Cache-Control": "no-store",
          "Content-Type": "text/x-python; charset=utf-8",
        },
      });
    } catch (error) {
      return new Response(error.message || "Stored setup instruction cannot produce a Hermes installer.", {
        status: 422,
        headers: {
          "Cache-Control": "no-store",
          "Content-Type": "text/plain; charset=utf-8",
        },
      });
    }
  }

  const upgradedInstruction = upgradeStoredSetupInstruction(instruction);
  return new Response(addTokenBoundInstallerInstruction(upgradedInstruction, request.url), {
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}
