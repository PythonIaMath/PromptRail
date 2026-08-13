import { chmod, mkdir, writeFile } from "node:fs/promises";
import { homedir, platform } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { run } from "./core.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

export const OPENCLAW_USER_MANAGED_SETUP_CONTRACT = [
  "Build model_catalog only from models whose provider key/adapter is available locally.",
  "Include every locally executable provider, including OpenRouter when the user configured it.",
  "Report eligible_not_selected_models separately from models selected into current route pools.",
  "Never describe a credential-blocked model as chooser-selected: models without a usable local key/adapter must be excluded before the chooser runs.",
  "Tell the user which model and provider Rail-1 selected for the current request.",
  "If the selected model cannot execute, surface that exact model/provider failure and stop. Do not select another model, switch providers, or retry through a fallback chain.",
];

export function openClawSetupContract(inferenceMode) {
  if (inferenceMode === "user_managed") {
    return [...OPENCLAW_USER_MANAGED_SETUP_CONTRACT];
  }
  if (inferenceMode === "router_managed") {
    return [];
  }
  throw new Error(`Unsupported OpenClaw inference mode: ${inferenceMode}`);
}

export function inspectProviders(harness, inferenceMode) {
  if (inferenceMode === "router_managed") {
    return { mode: "router_managed", providers: [], note: "LeRouter-managed inference uses server providers." };
  }
  if (harness === "hermes") {
    const envPath = run("hermes", ["config", "env-path"]);
    return { mode: "user_managed", env_path: envPath, providers: ["discovered by Hermes installer"] };
  }
  const raw = run("openclaw", ["models", "status", "--json", "--probe"]);
  const status = JSON.parse(raw);
  return { mode: "user_managed", status };
}

export function installHarness(state) {
  return state.manifest.harness === "hermes" ? installHermes(state) : installOpenClaw(state);
}

function installHermes(state) {
  const installerUrl = `${state.endpoints.dashboard_url.replace(/\/+$/g, "")}/api/hermes-installer`;
  const env = {
    ...process.env,
    LEROUTER_API_URL: state.endpoints.api_url,
    LEROUTER_AGENT_TOKEN: state.credential.api_key,
    LEROUTER_ROUTE_ID: state.manifest.route_id,
    LEROUTER_DASHBOARD_URL: state.endpoints.dashboard_url,
    LEROUTER_INSTALL_BACKGROUND: "0",
    LEROUTER_INFERENCE_MODE: state.manifest.inference_mode,
    LEROUTER_BUDGET_USD: String(state.manifest.budget.amount_usd),
    LEROUTER_BUDGET_CYCLE: state.manifest.budget.cycle,
  };
  const bootstrap = run("curl", ["-fsSL", installerUrl]);
  const result = run("python3", ["-c", bootstrap], { env });
  return result ? JSON.parse(result) : {};
}

function installOpenClaw(state) {
  const packageSpec = process.env.PROMPTRAIL_OPENCLAW_PLUGIN || "@promptrail/openclaw-router";
  run("openclaw", ["plugins", "install", packageSpec, "--pin"]);
  run("openclaw", ["plugins", "enable", "promptrail-router"]);
  run("openclaw", ["config", "set", "plugins.entries.promptrail-router.config.apiUrl", state.endpoints.api_url]);
  run("openclaw", ["config", "set", "plugins.entries.promptrail-router.config.routeId", state.manifest.route_id]);
  run("openclaw", ["config", "set", "plugins.entries.promptrail-router.config.inferenceMode", state.manifest.inference_mode]);
  run("openclaw", ["config", "set", "plugins.entries.promptrail-router.config.apiKey", state.credential.api_key]);
  run("openclaw", ["config", "validate"]);
  run("openclaw", ["gateway", "restart"]);
  const plugin = JSON.parse(run("openclaw", ["plugins", "inspect", "promptrail-router", "--json"]));
  if (!plugin || typeof plugin !== "object" || Array.isArray(plugin)) {
    throw new Error("OpenClaw plugin inspection returned an invalid result.");
  }
  return {
    ...plugin,
    setup_contract: openClawSetupContract(state.manifest.inference_mode),
  };
}

export async function installScheduler(cliPath) {
  if (platform() === "darwin") {
    const path = join(homedir(), "Library", "LaunchAgents", "ai.promptrail.route-refresh.plist");
    const logPath = join(homedir(), ".promptrail", "route-refresh.log");
    const body = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>ai.promptrail.route-refresh</string>
<key>ProgramArguments</key><array><string>${process.execPath}</string><string>${cliPath}</string><string>refresh-routes</string></array>
<key>StartInterval</key><integer>604800</integer>
<key>RunAtLoad</key><false/>
<key>StandardOutPath</key><string>${logPath}</string>
<key>StandardErrorPath</key><string>${logPath}</string>
</dict></plist>
`;
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, body, { mode: 0o644 });
    await chmod(path, 0o644);
    run("launchctl", ["bootout", `gui/${process.getuid()}/ai.promptrail.route-refresh`], { allowFailure: true });
    run("launchctl", ["bootstrap", `gui/${process.getuid()}`, path]);
    return { manager: "launchd", path };
  }
  if (platform() === "linux") {
    const unitDir = join(homedir(), ".config", "systemd", "user");
    const servicePath = join(unitDir, "promptrail-route-refresh.service");
    const timerPath = join(unitDir, "promptrail-route-refresh.timer");
    await mkdir(unitDir, { recursive: true });
    await writeFile(servicePath, `[Unit]\nDescription=Refresh PromptRail routes\n[Service]\nType=oneshot\nExecStart=${process.execPath} ${cliPath} refresh-routes\n`, { mode: 0o644 });
    await writeFile(timerPath, `[Unit]\nDescription=Weekly PromptRail route refresh\n[Timer]\nOnBootSec=15m\nOnUnitActiveSec=7d\nPersistent=true\n[Install]\nWantedBy=timers.target\n`, { mode: 0o644 });
    run("systemctl", ["--user", "daemon-reload"]);
    run("systemctl", ["--user", "enable", "--now", "promptrail-route-refresh.timer"]);
    return { manager: "systemd", path: timerPath };
  }
  throw new Error(`Automatic scheduling is unsupported on ${platform()}.`);
}

export function collectHistory(state) {
  if (state.manifest.harness === "hermes") {
    const script = join(root, "frontend", "app", "lib", "hermes-installer", "setup.py");
    const output = run("python3", [script, "--routes-only"], {
      env: {
        ...process.env,
        LEROUTER_API_URL: state.endpoints.api_url,
        LEROUTER_AGENT_TOKEN: state.credential.api_key,
        LEROUTER_ROUTE_ID: state.manifest.route_id,
        LEROUTER_BUDGET_USD: String(state.manifest.budget.amount_usd),
        LEROUTER_BUDGET_CYCLE: state.manifest.budget.cycle,
      },
    });
    return JSON.parse(output);
  }
  const sessions = JSON.parse(run("openclaw", ["sessions", "--all-agents", "--json"]));
  return { sessions: sessions.sessions || [], stores: sessions.stores || [] };
}

export function refreshHarnessRoutes(state) {
  return installHarness(state);
}
