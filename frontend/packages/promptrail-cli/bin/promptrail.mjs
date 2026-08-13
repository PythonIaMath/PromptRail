#!/usr/bin/env node

import { fileURLToPath } from "node:url";
import {
  authorizeDevice,
  collectAnswers,
  detectHarnesses,
  exchangeInstallation,
  readState,
  safeState,
  sha256,
  writeState,
} from "../lib/core.mjs";
import {
  collectHistory,
  inspectProviders,
  installHarness,
  installScheduler,
  refreshHarnessRoutes,
} from "../lib/harnesses.mjs";

async function install() {
  const detectedHarnesses = detectHarnesses();
  if (!detectedHarnesses.length) {
    throw new Error("No supported harness detected. Install Hermes or OpenClaw and rerun.");
  }
  const answers = await collectAnswers({ argv: process.argv.slice(3), detectedHarnesses });
  if (!detectedHarnesses.includes(answers.harness)) {
    throw new Error(`Selected harness ${answers.harness} is not installed.`);
  }
  const providerCheck = inspectProviders(answers.harness, answers.inference_mode);
  const installToken = await authorizeDevice(detectedHarnesses);
  const state = await exchangeInstallation(installToken, answers);
  state.provider_check = providerCheck;
  await writeState(state);
  state.harness_result = installHarness(state);
  state.scheduler = await installScheduler(fileURLToPath(import.meta.url));
  await writeState(state);
  process.stdout.write(`${JSON.stringify(safeState(state), null, 2)}\n`);
}

async function status() {
  const state = await readState();
  process.stdout.write(`${JSON.stringify(safeState(state), null, 2)}\n`);
}

async function refreshRoutes() {
  const state = await readState();
  const history = collectHistory(state);
  const nextHash = sha256(JSON.stringify(history));
  if (state.last_history_hash === nextHash) {
    process.stdout.write("PromptRail routes are unchanged.\n");
    return;
  }
  state.harness_result = refreshHarnessRoutes(state);
  state.last_history_hash = nextHash;
  state.last_route_refresh_at = new Date().toISOString();
  await writeState(state);
  process.stdout.write("PromptRail routes refreshed.\n");
}

async function main() {
  const command = process.argv[2] || "install";
  if (command === "install" || command === "configure" || command === "repair") return install();
  if (command === "status") return status();
  if (command === "refresh-routes") return refreshRoutes();
  throw new Error("Usage: promptrail <install|configure|status|repair|refresh-routes>");
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
