import { createHash } from "node:crypto";
import { access, chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir, hostname, platform } from "node:os";
import { dirname, join } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import readline from "node:readline/promises";

export const APP_URL = (process.env.PROMPTRAIL_APP_URL || "https://promptrail.ai").replace(/\/+$/g, "");
export const STATE_DIR = join(homedir(), ".promptrail");
export const STATE_PATH = join(STATE_DIR, "installation.json");
const HARNESSES = ["hermes", "openclaw"];

export function commandExists(command) {
  const result = spawnSync(command, ["--version"], { encoding: "utf8" });
  return !result.error && result.status === 0;
}

export function detectHarnesses() {
  return HARNESSES.filter(commandExists);
}

export function option(argv, name) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : undefined;
}

export function routeIdFromWorkspace(workspace) {
  const seed = String(workspace || "workspace")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 32);
  return `route_${seed || "workspace"}`;
}

export function normalizeAnswers(input) {
  const workspaceName = String(input.workspace_name || "").trim();
  const budgetAmount = Number(input.budget?.amount_usd);
  const cycle = String(input.budget?.cycle || "").toLowerCase();
  const inferenceMode = String(input.inference_mode || "").toLowerCase();
  const harness = String(input.harness || "").toLowerCase();
  if (!workspaceName || workspaceName.length > 80) throw new Error("Workspace name must contain 1 to 80 characters.");
  if (!Number.isFinite(budgetAmount) || budgetAmount <= 0) throw new Error("Budget must be a positive USD amount.");
  if (!["weekly", "monthly", "quarterly", "yearly"].includes(cycle)) throw new Error("Invalid budget cycle.");
  if (!["user_managed", "router_managed"].includes(inferenceMode)) throw new Error("Invalid inference mode.");
  if (!HARNESSES.includes(harness)) throw new Error("Invalid harness.");
  return {
    workspace_name: workspaceName,
    route_id: routeIdFromWorkspace(workspaceName),
    budget: { amount_usd: Math.round(budgetAmount * 100) / 100, cycle },
    inference_mode: inferenceMode,
    harness,
    route_refresh_interval: "7d",
  };
}

async function promptChoice(rl, label, choices, defaultValue) {
  const rendered = choices.map((entry, index) => `${index + 1}) ${entry.label}`).join("  ");
  const answer = (await rl.question(`${label}\n${rendered}\n> `)).trim();
  if (!answer) return defaultValue;
  const byIndex = choices[Number(answer) - 1];
  const byValue = choices.find((entry) => entry.value === answer.toLowerCase());
  if (!byIndex && !byValue) throw new Error(`Invalid choice for ${label}.`);
  return (byIndex || byValue).value;
}

export async function collectAnswers({ argv, detectedHarnesses, input = process.stdin, output = process.stdout }) {
  const nonInteractive = argv.includes("--non-interactive");
  const flags = {
    workspace_name: option(argv, "--workspace"),
    budget: {
      amount_usd: option(argv, "--budget"),
      cycle: option(argv, "--cycle"),
    },
    inference_mode: option(argv, "--inference"),
    harness: option(argv, "--harness"),
  };
  if (nonInteractive) {
    const missing = [
      !flags.workspace_name && "--workspace",
      !flags.budget.amount_usd && "--budget",
      !flags.budget.cycle && "--cycle",
      !flags.inference_mode && "--inference",
      !flags.harness && "--harness",
    ].filter(Boolean);
    if (missing.length) throw new Error(`--non-interactive requires ${missing.join(", ")}.`);
    if (!detectedHarnesses.includes(String(flags.harness).toLowerCase())) {
      throw new Error(`Requested harness ${flags.harness} was not detected.`);
    }
    return normalizeAnswers(flags);
  }

  const rl = readline.createInterface({ input, output });
  try {
    output.write(`Detected harnesses: ${detectedHarnesses.join(", ")}\n\n`);
    const harness = flags.harness || await promptChoice(
      rl,
      "Harness",
      detectedHarnesses.map((value) => ({ label: value === "hermes" ? "Hermes" : "OpenClaw", value })),
      detectedHarnesses[0],
    );
    const workspaceName = flags.workspace_name
      || (await rl.question(`Workspace name [${hostname()} ${harness}]: `)).trim()
      || `${hostname()} ${harness}`;
    const budgetAmount = flags.budget.amount_usd
      || (await rl.question("Budget in USD [500]: ")).trim()
      || "500";
    const cycle = flags.budget.cycle || await promptChoice(rl, "Budget cycle", [
      { label: "Weekly", value: "weekly" },
      { label: "Monthly", value: "monthly" },
      { label: "Quarterly", value: "quarterly" },
      { label: "Yearly", value: "yearly" },
    ], "monthly");
    const inferenceMode = flags.inference_mode || await promptChoice(rl, "Inference owner", [
      { label: "User managed: local provider keys execute requests", value: "user_managed" },
      { label: "LeRouter managed: LeRouter executes requests", value: "router_managed" },
    ], "user_managed");
    const answers = normalizeAnswers({
      workspace_name: workspaceName,
      budget: { amount_usd: budgetAmount, cycle },
      inference_mode: inferenceMode,
      harness,
    });
    output.write("\nConfiguration\n");
    output.write(`  Workspace: ${answers.workspace_name}\n`);
    output.write(`  Harness: ${answers.harness}\n`);
    output.write(`  Budget: $${answers.budget.amount_usd} ${answers.budget.cycle}\n`);
    output.write(`  Inference: ${answers.inference_mode}\n`);
    output.write("  Route refresh: every 7 days\n");
    const confirm = (await rl.question("\nApply this configuration? [Y/n] ")).trim().toLowerCase();
    if (confirm && confirm !== "y" && confirm !== "yes") throw new Error("Installation cancelled.");
    return answers;
  } finally {
    rl.close();
  }
}

async function jsonRequest(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  return { response, payload };
}

export async function authorizeDevice(detectedHarnesses, output = process.stdout) {
  const { response, payload } = await jsonRequest(`${APP_URL}/api/cli/device`, {
    method: "POST",
    body: JSON.stringify({
      device_name: `${hostname()} (${platform()})`,
      detected_harnesses: detectedHarnesses,
    }),
  });
  if (!response.ok) throw new Error(payload.error || "Could not start device authorization.");
  output.write(`Authorize PromptRail with code ${payload.user_code}\n${payload.verification_uri_complete}\n`);
  openBrowser(payload.verification_uri_complete);
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, Number(payload.interval || 2) * 1000));
    const polled = await jsonRequest(`${APP_URL}/api/cli/device`, {
      method: "PUT",
      body: JSON.stringify({ device_code: payload.device_code }),
    });
    if (polled.response.ok) return polled.payload.install_token;
    if (polled.payload.error === "authorization_pending") continue;
    throw new Error(polled.payload.error || "Device authorization failed.");
  }
}

function openBrowser(url) {
  const command = platform() === "darwin" ? "open" : "xdg-open";
  const child = spawn(command, [url], { detached: true, stdio: "ignore" });
  child.unref();
}

export async function exchangeInstallation(installToken, answers) {
  const { response, payload } = await jsonRequest(`${APP_URL}/api/cli/install`, {
    method: "POST",
    headers: { Authorization: `Bearer ${installToken}` },
    body: JSON.stringify(answers),
  });
  if (!response.ok) throw new Error(payload.error || "Installation configuration failed.");
  return payload;
}

export async function writeState(state) {
  await mkdir(STATE_DIR, { recursive: true, mode: 0o700 });
  await writeFile(STATE_PATH, `${JSON.stringify(state, null, 2)}\n`, { mode: 0o600 });
  await chmod(STATE_PATH, 0o600);
}

export async function readState() {
  return JSON.parse(await readFile(STATE_PATH, "utf8"));
}

export async function pathExists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

export function run(command, args, options = {}) {
  const { allowFailure = false, ...spawnOptions } = options;
  const result = spawnSync(command, args, { encoding: "utf8", ...spawnOptions });
  if (result.error) throw result.error;
  if (result.status !== 0 && !allowFailure) {
    throw new Error(`${command} ${args.join(" ")} failed: ${(result.stderr || result.stdout || "").trim()}`);
  }
  return (result.stdout || "").trim();
}

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function safeState(state) {
  const copy = structuredClone(state);
  if (copy.credential?.api_key) copy.credential.api_key = "<redacted>";
  return copy;
}
