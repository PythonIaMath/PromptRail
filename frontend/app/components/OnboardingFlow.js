"use client";

import { authClient } from "../lib/auth-client.js";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

const flowSteps = ["Workspace", "Instructions"];
export const planOptions = [
  { label: "Weekly", value: "weekly" },
  { label: "Monthly", value: "monthly" },
  { label: "Quarterly", value: "quarterly" },
  { label: "Yearly", value: "yearly" },
];
export const inferenceModeOptions = [
  { label: "User managed", value: "user_managed" },
  { label: "Router managed", value: "router_managed" },
];
export const runtimeOptions = [
  { label: "Hermes", value: "hermes" },
  { label: "OpenClaw", value: "openclaw" },
];

export function makeRouteId(workspaceName) {
  const seed = (workspaceName || "workspace")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 32);
  return `route_${seed || "team"}`;
}

function StepNav({ activeStep, setActiveStep }) {
  return (
    <div className="setup-step-nav" aria-label="Setup progress">
      {flowSteps.map((step, index) => (
        <button
          className={`setup-step ${activeStep === index ? "setup-step-active" : ""}`}
          key={step}
          type="button"
          onClick={() => setActiveStep(index)}
        >
          <span>{index + 1}</span>
          {step}
        </button>
      ))}
    </div>
  );
}

function ApiKeyStep({
  budget,
  inferenceMode,
  isCreatingKey,
  keyError,
  planType,
  runtime,
  setBudget,
  setInferenceMode,
  setPlanType,
  setRuntime,
  setWorkspaceName,
  workspaceName,
  onNext,
}) {
  const canContinue = Number(budget) > 0;

  return (
    <div className="setup-card-grid setup-card-grid-single setup-workspace-grid">
      <form
        className="setup-card setup-config-card"
        onSubmit={(event) => {
          event.preventDefault();
          onNext();
        }}
      >
        <label className="setup-field">
          <span>Workspace</span>
          <input
            value={workspaceName}
            onChange={(event) => setWorkspaceName(event.target.value)}
            placeholder="Acme agents"
          />
        </label>
        <label className="setup-field">
          <span>Budget</span>
          <div className="setup-money-input">
            <span>$</span>
            <input
              min="1"
              type="number"
              value={budget}
              onChange={(event) => setBudget(event.target.value)}
            />
          </div>
        </label>
        <label className="setup-field">
          <span>Budget cycle</span>
          <select value={planType} onChange={(event) => setPlanType(event.target.value)}>
            {planOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <div className="setup-choice-grid">
          <label className="setup-field">
            <span>Inference</span>
            <select value={inferenceMode} onChange={(event) => setInferenceMode(event.target.value)}>
              {inferenceModeOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="setup-field">
            <span>Agent</span>
            <select value={runtime} onChange={(event) => setRuntime(event.target.value)}>
              {runtimeOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        {keyError ? <p className="auth-error">{keyError}</p> : null}
        <button
          className="setup-button setup-button-primary setup-config-submit"
          disabled={!canContinue || isCreatingKey}
          type="submit"
        >
          {isCreatingKey ? "Preparing setup..." : "Continue"}
        </button>
      </form>
    </div>
  );
}

function setupPayload({ budget, inferenceMode, planType, routeId, runtime, includeRoutes = true }) {
  const payload = {
    route_id: routeId || "route_workspace",
    update_schedule: "7d",
    candidates_per_route: 7,
    metadata: {
      source: `${runtime}_setup`,
      inference_mode: inferenceMode,
      budget: {
        amount_usd: Number(budget || 500),
        cycle: planType,
        output_token_weight: 5,
        request_weight_beta: 1,
        request_difficulty_alpha: 2,
        request_weight_cap_multiplier: 4,
        budget_malus_gamma: 75,
      },
      route_update_policy: "Review recent user conversations weekly. Update routes only when the user has a genuinely new recurring agent use case or has clearly changed their usage pattern. If no stable change is detected, make no route changes.",
    },
  };
  if (includeRoutes) {
    payload.routes = agentBootstrapRoutes(runtime);
  }
  return payload;
}

function agentBootstrapRoutes(runtime) {
  const agentName = runtimeLabel(runtime);
  return {
    knowledge_lookup: {
      trigger: "Factual lookup, entity matching, credentials, records, and concise question answering.",
      task: `${agentName} answers factual questions and maps user constraints to the right record or entity.`,
    },
    structured_data_write: {
      trigger: "Insert, update, transform, or validate table rows, JSON, CSV, SQL, and spreadsheet-like records.",
      task: `${agentName} performs structured data operations where schema fidelity and exact values matter.`,
    },
    coding_and_tool_use: {
      trigger: "Code changes, shell/tool execution, debugging, API usage, and multi-step agent actions.",
      task: `${agentName} plans and executes tool-heavy technical work with reliable instruction following.`,
    },
    analytical_reasoning: {
      trigger: "Compare options, reason over constraints, summarize prior context, and make a defensible decision.",
      task: `${agentName} handles longer reasoning tasks that need consistency across turns.`,
    },
    document_content_work: {
      trigger: "Write, rewrite, summarize, or extract information from documents, reports, emails, and presentations.",
      task: `${agentName} produces and edits content while preserving structure, tone, and important details.`,
    },
    provider_and_agent_operations: {
      trigger: "Configure model providers, credentials, agent integrations, scheduled jobs, and operational workflows.",
      task: `${agentName} maintains provider and agent configuration and diagnoses operational failures.`,
    },
  };
}

export function runtimeLabel(runtime) {
  return runtime === "openclaw" ? "OpenClaw" : "Hermes";
}

function runtimeHarness(runtime) {
  return runtime === "openclaw" ? "OpenClaw provider harness" : "Hermes native adapter registry";
}

function setupReportContract({ agentName, inferenceMode }) {
  const executionOwner = inferenceMode === "router_managed"
    ? "Rail-1-managed provider infrastructure"
    : `${agentName}'s configured provider adapters/API keys`;
  return `Final setup report required:
- End setup with a concise user-facing report titled "PromptRail setup result".
- First section: "Models Rail-1 will route across". List every model from job.result.catalog_summary.routable_models exactly as returned. Do not invent, rename, or normalize model IDs.
- Second section: "Provider API keys to add for more models". List missing provider key environment variables that would unlock additional routing coverage. Include provider name, env var names, and where to add them. If running Hermes, include the path returned by "hermes config env-path" when available.
- Third section: "Configured but unusable". List provider keys that exist but did not produce executable models, with the validation error or adapter reason when known.
- Fourth section: "Execution owner". State that inference will run through ${executionOwner}.
- Never print full API key values, OAuth tokens, setup-link tokens, or bearer tokens. Only print env var names and redacted examples.`;
}

function hermesUserManagedInstructions({ apiBaseUrl, apiKey, appBaseUrl, budget, planType, routeId, runtime }) {
  const payload = setupPayload({ budget, inferenceMode: "user_managed", planType, routeId, runtime });
  const initialRoutesJson = JSON.stringify(payload.routes);
  return `Hermes, install PromptRail in user-managed inference mode.

Goal:
- Rail-1 selects the best model for each request.
- Hermes executes the selected model through its native adapter registry.
- This setup must be reproducible from this prompt and the PromptRail API key below.
- Do not clone any repository.
- Do not include any model Hermes cannot execute with the user's current credentials.
- Do not ask me to paste provider API keys into chat. If a provider key is needed, show the env var names and the path returned by "hermes config env-path".
- Never print full API keys or OAuth tokens. Redact secrets in logs and summaries.

PromptRail API:
- Base URL: ${apiBaseUrl}
- Dashboard URL: ${appBaseUrl}
- API key: ${apiKey || "lr_live_..."}
- Route ID: ${routeId || "route_workspace"}
- Inference mode: user_managed

Deterministic installer path:
- Prefer this installer over hand-writing the plugin. It writes the exact reproducible setup script and plugin templates maintained by the PromptRail dashboard.
- The setup-link response adds a token-bound installer command here. Run that exact pipe command first so the full API key goes directly from PromptRail into the installer process without being copied into an agent message or shell command.
- Mandatory first setup action: run the exports and dashboard installer command below using the values from this current setup-link response.
- Do not inspect, execute, or trust an existing ~/.hermes/scripts/lerouter_setup_user_managed.py, state.json, or setup-run.json before the dashboard installer command returns the new run metadata. Existing artifacts may contain expired credentials or results from an older run.
- Never run ~/.hermes/scripts/lerouter_setup_user_managed.py directly as a substitute for the dashboard installer command. The dashboard installer must refresh that script and its credentials first.
- If the current run fails, report that failure. Do not summarize or present models from an earlier successful state as the result of this run.
- Run:
  export LEROUTER_API_URL="${apiBaseUrl}"
  export LEROUTER_AGENT_TOKEN="${apiKey || "lr_live_..."}"
  export LEROUTER_ROUTE_ID="${routeId || "route_workspace"}"
  export LEROUTER_DASHBOARD_URL="${appBaseUrl}"
  export LEROUTER_ROUTES_JSON='${initialRoutesJson}'
  export LEROUTER_INSTALL_BACKGROUND=1
  curl -fsSL "${appBaseUrl}/api/hermes-installer" | python3
- The installer command returns JSON with pid_path, log_path, events_path, state_path, run_path, started_at, and events_offset. Setup continues in a detached process because provider discovery and route optimization can take several minutes.
- Poll the returned PID until that exact process exits. Do not report success, start provider smoke tests, or enter the manual fallback contract while it is alive.
- Read only events_path bytes at or after events_offset. Earlier setup_succeeded or setup_failed events belong to previous runs and must not be used.
- If the current event segment ends with setup_succeeded and state_path was modified at or after started_at, read state_path and produce the required final report.
- If the current event segment ends with setup_failed, or the process exits without either terminal event, show the redacted error and the final redacted lines from log_path, then stop. Do not improvise provider probes.
- If curl is unavailable, fetch ${appBaseUrl}/api/hermes-installer?file=bootstrap.py with any HTTP client and run it with python3 using the same env vars.
- Only if the installer endpoint itself cannot be fetched, fall back to the detailed manual contract below and tell me dashboard-hosted installer fetch failed.

Provider discovery rules:
- Inspect Hermes native model providers/adapters and the user's configured credentials.
- Treat OAuth-backed adapters as usable when Hermes can execute them without an extra API key.
- Validate each provider with a real Hermes one-shot execution before adding its models to model_catalog. During validation, set LEROUTER_DISABLE=1 so the PromptRail plugin does not intercept its own validation calls.
- Do not trust provider_model_ids() alone. A listed model is not executable until the current account can run it successfully.
- Build model_catalog only from models Hermes can execute now.
- Keep the submitted catalog balanced: include at most 5 strong models per provider unless I explicitly ask for more.
- Rail-1's Modal model selector must choose a focused 5-7 model pool for each route from the submitted executable catalog. Do not submit broad or precomputed route pools.
- For Copilot, be conservative: include only Copilot models that the account can actually execute. If Copilot lists non-GPT models but Hermes returns "unauthorized" or "not authorized to use this Copilot feature", exclude those models.
- Enrich every model_catalog entry with provider, native_model_id, quality, input/output price estimates, context_window, tags, supports_tools, and supports_json. Do not submit bare model_id-only entries.
- If a provider is missing or unusable, do not include its models in model_catalog.
- Show me a "Missing provider keys" section with the env vars I can add to unlock more routing coverage.
- Show me a "Configured but unusable" section when a key exists but Hermes cannot execute that provider.
- If no executable model is available, stop and ask me to configure at least one native provider key before calling /agent/setup-jobs.

Suggested provider key checks:
- OpenAI: OPENAI_API_KEY, or an existing Hermes OpenAI/OpenAI Codex OAuth adapter.
- Anthropic: ANTHROPIC_API_KEY.
- Google/Gemini: GOOGLE_API_KEY or GEMINI_API_KEY.
- DeepSeek: DEEPSEEK_API_KEY.
- xAI: XAI_API_KEY.
- Groq: GROQ_API_KEY.
- Mistral: MISTRAL_API_KEY.
- Together: TOGETHER_API_KEY or TOGETHER_AI_API_KEY.
- OpenRouter: OPENROUTER_API_KEY only if Hermes has a native adapter for it.

OpenAI-compatible provider repair:
- If a provider API key exists but Hermes says "Unknown provider", try to configure it as a Hermes user provider before marking it unusable.
- For Together, prefer a real native provider if Hermes has one. If not, add or update this config shape in Hermes config.yaml without destroying unrelated config:
  providers:
    together:
      name: Together.ai
      base_url: https://api.together.ai/v1
      key_env: <TOGETHER_API_KEY_OR_TOGETHER_AI_API_KEY_WHICHEVER_EXISTS>
      transport: openai_chat
      default_model: meta-llama/Llama-3.3-70B-Instruct-Turbo
      models:
        meta-llama/Llama-3.3-70B-Instruct-Turbo: { context_length: 131072 }
        moonshotai/Kimi-K2.7-Code: { context_length: 262144 }
        openai/gpt-oss-120b: { context_length: 131072 }
        zai-org/GLM-5.2: { context_length: 262144 }
        MiniMaxAI/MiniMax-M3: { context_length: 524288 }
- After creating a custom provider, validate it with:
  LEROUTER_DISABLE=1 hermes -z "Reply with exactly OK" --provider together -m meta-llama/Llama-3.3-70B-Instruct-Turbo
- Only include Together models in model_catalog if that validation succeeds.
- Apply the same pattern to other OpenAI-compatible providers when Hermes supports user-defined providers: create a named providers.<provider> entry, choose the env var that actually exists, validate with Hermes, then include only validated models.

Required local artifacts:
- Create or update ~/.hermes/scripts/lerouter_setup_user_managed.py. It must be deterministic and rerunnable.
- Create or update ~/.hermes/plugins/lerouter-user-managed/plugin.yaml.
- Create or update ~/.hermes/plugins/lerouter-user-managed/__init__.py.
- Create or update ~/.hermes/lerouter-user-managed/state.json.
- Never require cloning a repo. Everything must be written locally from this prompt and the user's existing Hermes install.
- Make the setup script idempotent: rerunning it should refresh state.json and /agent/setup-jobs without duplicating plugins, corrupting config.yaml, or deleting unrelated user config.
- The setup script must keep candidates_per_route = 7 and cap submitted models to at most 5 validated models per provider by default.
- For the initial setup, use the six explicit runtime routes in LEROUTER_ROUTES_JSON. They are the user's declared initial agent workload, not inferred fallback routes.
- Before calling /agent/setup-jobs, collect sanitized local Hermes history samples from the selected budget cycle (${planType}) and attach them as metadata.budget.history_queries. PromptRail will run ArchRouter on those history queries, compute W, median_weighted_tokens, average_requests_per_period, and remaining_weight server-side. Do not POST placeholder strings for budget fields.
- Do not replace the explicit initial routes with setup, smoke-test, or other synthetic routes.
- The setup script must store dashboard_url and the last /agent/setup-jobs response in state.json.

Initial PromptRail setup request:

POST ${apiBaseUrl}/agent/setup-jobs
Headers:
  Authorization: Bearer ${apiKey || "lr_live_..."}
  Content-Type: application/json

Body:
${JSON.stringify(
  {
    ...payload,
    model_catalog: "<ONLY_MODELS_HERMES_CAN_EXECUTE_NOW>",
    routes: "<5_TO_7_ROUTES_DERIVED_FROM_REAL_HERMES_HISTORY>",
  },
  null,
  2
)
  .replace('"model_catalog": "<ONLY_MODELS_HERMES_CAN_EXECUTE_NOW>"', '"model_catalog": <ONLY_MODELS_HERMES_CAN_EXECUTE_NOW>')
  .replace('"routes": "<5_TO_7_ROUTES_DERIVED_FROM_REAL_HERMES_HISTORY>"', '"routes": <5_TO_7_ROUTES_DERIVED_FROM_REAL_HERMES_HISTORY>')}

After setup job succeeds:
- Poll the setup job status_path until status is succeeded, then read job.result.catalog_summary.routable_models from the response.
- Show me the exact list of models Rail-1 can route across.
- Show job.result.catalog_summary.providers, model_count, routable_model_count, and the number of routes covered.
- Show provider_api_keys_to_add / missing_provider_keys after the available models so I know exactly which API key env vars unlock more models.
- Show configured_but_unusable_provider_keys / configured_but_unusable_providers after missing keys.
- If models were submitted in model_catalog but are absent from routable_models, explain that they were executable by Hermes but not selected into Rail-1's current per-route candidate pools.
- If a model I expected is missing, explain that Hermes did not detect a usable native adapter/API key for that provider.

${setupReportContract({ agentName: "Hermes", inferenceMode: "user_managed" })}

Required local event files:
- Before enabling the plugin, create ~/.hermes/lerouter-user-managed/events.jsonl as an append-only JSONL audit log.
- Also create ~/.hermes/lerouter-user-managed/eventx.json as a readable snapshot with this shape:
  {
    "version": 1,
    "updated_at": "<iso timestamp>",
    "events": []
  }
- Every plugin event must be appended to events.jsonl and reflected in eventx.json.
- Store dashboard_url = "${appBaseUrl}" in ~/.hermes/lerouter-user-managed/state.json.
- For every plugin event, POST a matching routing-operation row to ${appBaseUrl}/api/usage-log with Authorization: Bearer ${apiKey || "lr_live_..."}.
- The dashboard payload must use metadata.kind = "routing_operation" and metadata.operation = the event name.
- Redact secrets before writing either file. Never write API keys, OAuth tokens, full prompts, or full tool payloads.
- Each event object must include at least: ts, session_id, event, route_id, route_name, provider, model_id, success, latency_ms, error_type, error_message.
- Required event names: setup_started, setup_succeeded, setup_failed, plugin_registered, middleware_entered, select_started, select_succeeded, select_failed, execution_started, execution_succeeded, execution_failed, usage_log_started, usage_log_succeeded, usage_log_failed.
- Use these files as the source of truth when I ask whether Rail-1 is really routing live Hermes turns.

Runtime behavior:
- For every LLM call in the agent process, including tool-loop continuations and retries after the first user input, call ${apiBaseUrl}/lerouter/select with Hermes's current messages, tools, tool_choice, response_format, temperature, max_tokens, and route_id.
- Execute response.selected_model_id or response.native_model_id through Hermes native adapter registry.
- The plugin must not rely only on Hermes CLI's private cli_ref to find the current agent. Some Hermes modes call llm_execution middleware without cli_ref. If needed, recover the current agent from the active conversation_loop stack frame, or use another reliable Hermes-supported current-agent reference.
- After activating the selected provider/model, rebuild Hermes api_kwargs for the newly active native adapter before calling next_call. Do not reuse a payload built for the previous provider, because OpenAI Codex, Copilot, Anthropic, and OpenAI-compatible providers use different request shapes.
- Stream normally from the Hermes native adapter.
- After completion or failure, call ${apiBaseUrl}/lerouter/usage-log with route_name, model_id, provider, success, usage/cost metadata, and inference_mode = "user_managed".
- If the selected model cannot execute for any reason, log execution_failed, report the error, and stop that request. Do not remove catalog entries, rerun setup, select an alternate model, or switch providers automatically.
- Add a LEROUTER_DISABLE=1 bypass so setup/validation/debug commands can call Hermes directly without recursive Rail-1 routing.
- Keep selected raw model IDs out of end-user messages unless I explicitly ask; logs may include provider, route, and model for debugging.
- A normal Hermes answer alone is not enough to prove live routing. To verify routing is active, start a new Hermes session and confirm that events.jsonl and eventx.json contain select_succeeded, execution_succeeded, and usage_log_succeeded for that fresh session.

Required verification before claiming success:
- Run python syntax checks on the setup script and plugin.
- Run the setup script once and show job.result.catalog_summary.providers, job.result.catalog_summary.routable_models, and route coverage.
- Start a fresh Hermes session and ask for an exact sentinel response such as:
  hermes chat -q "Reply with exactly LEROUTER_SMOKE_OK" --quiet --max-turns 5
- Then inspect ~/.hermes/lerouter-user-managed/eventx.json. It must contain, for that fresh session, select_succeeded, execution_succeeded, usage_log_started, and usage_log_succeeded.
- Confirm the dashboard received the same routing-operation events through ${appBaseUrl}/api/usage-log. If the dashboard API is unavailable, report that dashboard verification failed instead of claiming full success.
- If the smoke test selects a failing model/provider, the plugin must log execution_failed and the smoke test must fail visibly. Do not retry with another model or provider.

Weekly route review:
- Register a weekly job.
- Review recent conversations once per week.
- Call /agent/setup-jobs again only when there is a genuinely new recurring use case, a clear usage-pattern change, or a newly available provider key changes the executable model_catalog.
- It is valid for the weekly review to make no changes.`;
}

export function userManagedInstructions({ agentName, apiBaseUrl, apiKey, budget, planType, routeId, runtime }) {
  if (runtime === "hermes") {
    const appBaseUrl = (process.env.NEXT_PUBLIC_APP_URL || "http://localhost:3000").replace(/\/+$/g, "");
    return hermesUserManagedInstructions({ apiBaseUrl, apiKey, appBaseUrl, budget, planType, routeId, runtime });
  }

  const payload = setupPayload({ budget, inferenceMode: "user_managed", planType, routeId, runtime });
  const harness = runtimeHarness(runtime);
  return `${agentName}, install PromptRail in user-managed inference mode.

Goal:
- Rail-1 selects the best model.
- ${agentName} executes the selected model with ${harness} and the user's API keys.
- ${agentName} must only let Rail-1 route across models it can actually execute.

Required PromptRail config:
- LEROUTER_API_URL=${apiBaseUrl}
- LEROUTER_AGENT_TOKEN=${apiKey || "lr_live_..."}
- LEROUTER_INFERENCE_MODE=user_managed
- Route ID: ${routeId || "route_workspace"}

Provider key discovery:
- Inspect ${harness} for configured providers/adapters and available API keys.
- Build model_catalog only from models whose provider key/adapter is available locally.
- Use the embedded routes in the setup request for the first setup. They are the initial runtime routes for this agent, not smoke/demo routes.
- Include any provider ${agentName} can execute with the user's configured credentials, including OpenRouter when the user configured it.
- Do not include Anthropic, OpenAI, Google/Gemini, OpenRouter, Together, or other provider models unless ${agentName} can execute them with the user's configured credentials.
- If no executable model is available, stop setup and ask the user to add at least one provider API key.
- Before POSTing the setup request, collect sanitized local history samples for the selected budget cycle (${planType}) and attach them as metadata.budget.history_queries. PromptRail computes average_requests_per_period, median_weighted_tokens, total_predicted_weight W, and remaining_weight after running ArchRouter over those samples. Do not POST placeholder strings for budget fields.

Initial PromptRail setup request:

POST ${apiBaseUrl}/agent/setup-jobs
Headers:
  Authorization: Bearer ${apiKey || "lr_live_..."}
  Content-Type: application/json

Body:
${JSON.stringify(
  {
    ...payload,
    model_catalog: "<ONLY_MODELS_EXECUTABLE_BY_THIS_AGENT>",
  },
  null,
  2
)
  .replace('"model_catalog": "<ONLY_MODELS_EXECUTABLE_BY_THIS_AGENT>"', '"model_catalog": <ONLY_MODELS_EXECUTABLE_BY_THIS_AGENT>')}

After setup job succeeds:
- Poll the setup job status_path until status is succeeded, then read job.result.catalog_summary.routable_models from the response.
- Show the user the exact list of models Rail-1 can route across.
- Show job.result.catalog_summary.eligible_not_selected_models as models that this agent can execute with current credentials but the chooser did not select into any current route pool.
- Also show job.result.catalog_summary.providers and the number of routes covered.
- Show a "Provider API keys to add for more models" section with missing provider env vars that would unlock more routing coverage.
- Show a "Configured but unusable" section when env vars exist but ${agentName} cannot execute that provider.
- Never describe a credential-blocked model as chooser-selected: models without a usable local key/adapter must be excluded before the chooser runs.
- If a model the user expected is missing, say whether it was excluded before chooser selection because ${agentName} lacked a usable key/adapter, or was eligible but not selected into a route pool.

${setupReportContract({ agentName, inferenceMode: "user_managed" })}

Runtime behavior:
- For every LLM call in the agent process, including tool-loop continuations and retries after the first user input, call ${apiBaseUrl}/lerouter/select with ${agentName}'s current messages, tools, tool_choice, response_format, temperature, max_tokens, and route_id.
- Execute response.selected_model_id or response.native_model_id through ${harness}.
- Tell the user which model and provider Rail-1 selected for the current request.
- Stream normally from ${harness}.
- After completion or failure, call ${apiBaseUrl}/lerouter/usage-log with route_name, model_id, provider, success, usage/cost metadata, and inference_mode = "user_managed".
- If the selected model cannot execute, surface that exact model/provider failure and stop. Do not select another model, switch providers, or retry through a fallback chain.

Weekly route review:
- Register a weekly job.
- Review recent conversations once per week.
- Call /agent/setup-jobs again only when there is a genuinely new recurring use case or a clear usage-pattern change.
- It is valid for the weekly review to make no changes.`;
}

function hermesRouterManagedInstructions({ apiBaseUrl, apiKey, budget, planType, routeId, runtime }) {
  const payload = setupPayload({ budget, inferenceMode: "router_managed", planType, routeId, runtime, includeRoutes: false });
  return `Hermes, install PromptRail as my default model provider.

Requirements:
- This setup must be fully reproducible from this prompt.
- Use only the PromptRail API key below.
- Do not clone any repository.
- Do not ask me to edit files manually.
- Before POSTing the setup request, collect sanitized local Hermes history samples for the selected budget cycle (${planType}) and attach them as metadata.budget.history_queries. PromptRail computes average_requests_per_period, median_weighted_tokens, total_predicted_weight W, and remaining_weight after running ArchRouter over those samples. Do not POST placeholder strings for budget fields.
- Run the bash block exactly, then show me the routable models returned by Rail-1.

PromptRail API:
- Base URL: ${apiBaseUrl}
- API key: ${apiKey || "lr_live_..."}
- Route ID: ${routeId || "route_workspace"}
- Inference mode: router_managed

Run this in your terminal:

\`\`\`bash
set -euo pipefail

HERMES_HOME_DIR="$HERMES_HOME"
if [ -z "$HERMES_HOME_DIR" ]; then
  HERMES_HOME_DIR="$HOME/.hermes"
fi

mkdir -p "$HERMES_HOME_DIR/plugins/model-providers/lerouter"

cat > "$HERMES_HOME_DIR/plugins/model-providers/lerouter/__init__.py" <<'PY'
from providers import register_provider
from providers.base import ProviderProfile

register_provider(ProviderProfile(
    name="lerouter",
    aliases=("le-router",),
    display_name="Rail-1",
    description="Rail-1 OpenAI-compatible routing endpoint",
    signup_url="https://lerouter.ai",
    env_vars=("LEROUTER_AGENT_TOKEN",),
    base_url="${apiBaseUrl}/v1",
    auth_type="api_key",
    default_aux_model="lerouter",
))
PY

python3 - <<'PY'
import os
from pathlib import Path

import yaml

home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
home.mkdir(parents=True, exist_ok=True)

config_path = home / "config.yaml"
config = {}
if config_path.exists():
    config = yaml.safe_load(config_path.read_text()) or {}
if not isinstance(config, dict):
    config = {}

config["model"] = {
    "provider": "lerouter",
    "default": "lerouter",
    "base_url": "${apiBaseUrl}/v1",
    "api_mode": "chat_completions",
}
config.pop("fallback_model", None)

config_path.write_text(yaml.safe_dump(config, sort_keys=False))

env_path = home / ".env"
env_lines = env_path.read_text().splitlines() if env_path.exists() else []
updates = {
    "LEROUTER_AGENT_TOKEN": "${apiKey || "lr_live_..."}",
    "LEROUTER_API_URL": "${apiBaseUrl}",
    "LEROUTER_ROUTE_ID": "${routeId || "route_workspace"}",
    "LEROUTER_INFERENCE_MODE": "router_managed",
}

seen = set()
next_lines = []
for line in env_lines:
    if not line or line.lstrip().startswith("#") or "=" not in line:
        next_lines.append(line)
        continue
    key = line.split("=", 1)[0]
    if key in updates:
        next_lines.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        next_lines.append(line)
for key, value in updates.items():
    if key not in seen:
        next_lines.append(f"{key}={value}")

env_path.write_text("\\n".join(next_lines).rstrip() + "\\n")
print(f"Updated {config_path}")
print(f"Updated {env_path}")
PY

python3 - <<'PY'
from collections import Counter, defaultdict
from pathlib import Path
import json
import re
import sqlite3
import time
import urllib.request

api_base = "${apiBaseUrl}".rstrip("/")
token = "${apiKey || "lr_live_..."}"
payload = ${JSON.stringify(payload, null, 2)}

STOPWORDS = {
    "about", "after", "again", "against", "before", "being", "bring", "bringing",
    "can", "cannot", "does", "done", "each", "from", "has", "have", "into", "listed",
    "make", "more", "new", "record", "records", "should", "that", "the", "this", "through",
    "value", "values", "was", "what", "when", "where", "which", "with", "would", "your",
}
ROUTE_FAMILIES = {
    "structured_data_operations": {
        "keywords": {"csv", "data", "database", "field", "game_results", "insert", "json", "record", "row", "schema", "score", "spreadsheet", "sql", "standings", "table", "update", "values"},
        "label": "structured data operations",
        "suffix": "data_ops",
    },
    "software_engineering": {
        "keywords": {"api", "bug", "cli", "code", "command", "debug", "deploy", "error", "file", "fix", "hermes", "modal", "openclaw", "repo", "script", "setup", "test", "vm"},
        "label": "coding, tools, and debugging",
        "suffix": "engineering",
    },
    "research_lookup": {
        "keywords": {"credential", "credentials", "docs", "find", "lookup", "mission", "presentation", "question", "search", "source", "termination", "who"},
        "label": "factual lookup and record matching",
        "suffix": "lookup",
    },
    "planning_reasoning": {
        "keywords": {"analyze", "architecture", "budget", "compare", "decide", "design", "investigate", "plan", "reason", "route", "strategy", "tradeoff", "why"},
        "label": "planning and analytical reasoning",
        "suffix": "reasoning",
    },
    "document_content": {
        "keywords": {"document", "docx", "extract", "pdf", "presentation", "report", "slides", "summarize", "write"},
        "label": "document and content work",
        "suffix": "documents",
    },
}

def tokenize_text(text):
    return [token for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", text.lower()) if token not in STOPWORDS]

def is_setup_prompt(text):
    lowered = text.lower()
    return any(marker in lowered for marker in ("lerouter_agent_token", "lerouter api", "/agent/setup-jobs", "install lerouter", "hermes, install lerouter", "reply with exactly", "lerouter_smoke_ok", "smoke_ok", "smoke test"))

def collect_history_messages(limit=120):
    state_db = Path.home() / ".hermes" / "state.db"
    if not state_db.exists():
        return []
    try:
        connection = sqlite3.connect(str(state_db))
        rows = connection.execute(
            """
            select content
            from messages
            where role = 'user'
              and content is not null
              and length(trim(content)) > 0
            order by coalesce(timestamp, id) desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    except Exception:
        return []
    finally:
        try:
            connection.close()
        except Exception:
            pass
    messages = []
    for (content,) in rows:
        text = str(content or "").strip()
        text = re.sub(r"lr_live_[A-Za-z0-9_-]+", "lr_live_<redacted>", text)
        text = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-<redacted>", text)
        if len(text) >= 12 and not is_setup_prompt(text):
            messages.append(text[:1200])
    return list(reversed(messages))

def cycle_days():
    budget = payload.get("metadata", {}).get("budget", {})
    return {"weekly": 7, "monthly": 30, "quarterly": 91, "yearly": 365}.get(str(budget.get("cycle", "monthly")).lower(), 30)

def collect_history_samples(limit=500):
    state_db = Path.home() / ".hermes" / "state.db"
    if not state_db.exists():
        return []
    cutoff = time.time() - cycle_days() * 86400
    try:
        connection = sqlite3.connect(str(state_db))
        rows = connection.execute(
            """
            select
              m.content,
              m.timestamp,
              m.token_count,
              s.input_tokens,
              s.output_tokens,
              s.api_call_count
            from messages m
            left join sessions s on s.id = m.session_id
            where m.role = 'user'
              and m.content is not null
              and length(trim(m.content)) > 0
              and coalesce(m.timestamp, s.started_at, 0) >= ?
            order by coalesce(m.timestamp, s.started_at, m.id) asc
            limit ?
            """,
            (cutoff, limit),
        ).fetchall()
    except Exception:
        return []
    finally:
        try:
            connection.close()
        except Exception:
            pass

    samples = []
    for content, timestamp, token_count, session_input_tokens, session_output_tokens, api_call_count in rows:
        text = str(content or "").strip()
        text = re.sub(r"lr_live_[A-Za-z0-9_-]+", "lr_live_<redacted>", text)
        text = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-<redacted>", text)
        text = re.sub(r"Bearer\\s+[A-Za-z0-9._-]+", "Bearer <redacted>", text)
        if len(text) < 12 or is_setup_prompt(text):
            continue
        request_count = max(1.0, float(api_call_count or 0) or 1.0)
        sample = {"content": text[:6000], "timestamp": float(timestamp or time.time())}
        if token_count:
            sample["input_tokens"] = float(token_count)
        elif session_input_tokens:
            sample["input_tokens"] = max(1.0, float(session_input_tokens) / request_count)
        if session_output_tokens:
            sample["output_tokens"] = max(1.0, float(session_output_tokens) / request_count)
        samples.append(sample)
    return samples

def family_for_message(message):
    tokens = set(tokenize_text(message))
    scored = sorted(
        (len(tokens & definition["keywords"]), family)
        for family, definition in ROUTE_FAMILIES.items()
    )
    if scored and scored[-1][0] > 0:
        return scored[-1][1]
    return "research_lookup" if "?" in message else "planning_reasoning"

def route_name_for_family(family, messages):
    definition = ROUTE_FAMILIES[family]
    tokens = [
        token
        for message in messages
        for token in tokenize_text(message)
    ]
    common = [token for token, _count in Counter(tokens).most_common(3) if not token.isdigit()]
    stem = "_".join(common[:2]) if common else family
    stem = re.sub(r"[^a-z0-9_]+", "_", stem).strip("_")[:36] or family
    suffix = definition["suffix"]
    return stem if stem.endswith(suffix) else f"{stem}_{suffix}"[:48]

def derive_routes():
    messages = collect_history_messages()
    grouped = defaultdict(list)
    for message in messages:
        grouped[family_for_message(message)].append(message)
    routes = {}
    for family, family_messages in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True):
        definition = ROUTE_FAMILIES[family]
        route_name = route_name_for_family(family, family_messages)
        top_terms = [
            token
            for token, _count in Counter(
                token
                for message in family_messages
                for token in tokenize_text(message)
            ).most_common(6)
        ]
        routes[route_name] = {
            "trigger": f"Recurring Hermes history pattern: {definition['label']}.",
            "task": f"Route requests like the user's recent {definition['label']} work.",
            "source": "hermes_history",
            "history_message_count": len(family_messages),
            "top_terms": top_terms,
        }
        if len(routes) >= 7:
            break
    route_count = len(routes)
    if not 5 <= route_count <= 7:
        raise RuntimeError(
            f"PromptRail requires 5-7 real history-derived routes; Hermes history produced {route_count}. "
            "Build enough distinct recurring task history, then rerun setup."
        )
    return routes, {"source": "hermes_state_db", "message_count": len(messages), "routes_generated": route_count}

routes, route_generation = derive_routes()
history_samples = collect_history_samples()
if not history_samples:
    raise RuntimeError("Cannot compute W: Hermes local history has no usable requests in the selected budget cycle.")
budget = payload.get("metadata", {}).get("budget", {})
budget = {
    **budget,
    "history_queries": history_samples,
    "history_period_days": cycle_days(),
    "cycle_days": cycle_days(),
}
payload["routes"] = routes
payload["metadata"] = {**payload.get("metadata", {}), "budget": budget, "route_generation": route_generation}
print(f"Generated {len(routes)} Rail-1 routes from Hermes local history source={route_generation.get('source')}")

request = urllib.request.Request(
    api_base + "/agent/setup-jobs",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    },
    method="POST",
)

with urllib.request.urlopen(request) as response:
    setup_job = json.loads(response.read().decode("utf-8"))

job = setup_job.get("job") or {}
job_id = job.get("id")
if not job_id:
    raise RuntimeError(f"PromptRail did not return a setup job id: {setup_job}")

status_url = api_base + f"/agent/setup-jobs/{job_id}"
while True:
    status_request = urllib.request.Request(
        status_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(status_request) as response:
        status = json.loads(response.read().decode("utf-8"))
    job = status.get("job") or {}
    if job.get("status") == "succeeded":
        break
    if job.get("status") == "failed":
        raise RuntimeError(f"PromptRail setup job failed: {job.get('error')}")
    print(f"PromptRail setup job {job_id} status: {job.get('status')}")
    time.sleep(10)

summary = (job.get("result") or {}).get("catalog_summary") or {}
models = summary.get("routable_models") or []
providers = summary.get("providers") or []
routes = summary.get("routes") or {}

print("PromptRail setup complete.")
print("Routable models:")
for model in models:
    print(f"- {model}")
print(f"Providers: {', '.join(providers) if providers else 'unknown'}")
print(f"Routes covered: {len(routes)}")
route_update_job = (job.get("result") or {}).get("route_update_job") or {}
if route_update_job:
    print(f"Weekly route review job: {route_update_job.get('id')} next_run={route_update_job.get('nextRunAt')}")
PY

hermes config show
\`\`\`

After the command succeeds:
- Use Rail-1 as provider "lerouter" with model "lerouter".
- Send normal OpenAI-compatible chat requests to ${apiBaseUrl}/v1/chat/completions.
- Show the "PromptRail setup result" report using the exact routable models printed by the command.
- If the setup response includes provider_api_keys_to_add or missing_provider_keys, show those env vars as keys the user can add for more model coverage.
- Do not mention selected raw model IDs to me unless I ask.
- The weekly route review is server-side from /agent/setup-jobs with update_schedule=7d.

${setupReportContract({ agentName: "Hermes", inferenceMode: "router_managed" })}`;
}

export function routerManagedInstructions({ agentName, apiBaseUrl, apiKey, budget, planType, routeId, runtime }) {
  if (runtime === "hermes") {
    return hermesRouterManagedInstructions({ apiBaseUrl, apiKey, budget, planType, routeId, runtime });
  }

  const payload = setupPayload({ budget, inferenceMode: "router_managed", planType, routeId, runtime });
  const harness = runtimeHarness(runtime);
  return `${agentName}, install PromptRail in router-managed inference mode.

Goal:
- Rail-1 selects the best model and executes inference.
- ${agentName} does not need user provider API keys for model execution.
- ${agentName} sends normal OpenAI-compatible requests to Rail-1.
- Treat Rail-1 as the sole inference provider for this configured runtime. If it fails, surface the error and stop the request.
- Rail-1-managed inference must not use OpenRouter as an execution provider.

Required PromptRail config:
- Provider name: Rail-1
- API base URL: ${apiBaseUrl}/v1
- Chat completions endpoint: ${apiBaseUrl}/v1/chat/completions
- Model name: lerouter
- Auth header: Authorization: Bearer ${apiKey || "lr_live_..."}
- LEROUTER_INFERENCE_MODE=router_managed
- Route ID: ${routeId || "route_workspace"}

Initial PromptRail setup request:

POST ${apiBaseUrl}/agent/setup-jobs
Headers:
  Authorization: Bearer ${apiKey || "lr_live_..."}
  Content-Type: application/json

Body:
${JSON.stringify(
  payload,
  null,
  2
)}

After setup job succeeds:
- Show job.result.catalog_summary.routable_models to the user as the models Rail-1 may route across.
- Show provider_api_keys_to_add / missing_provider_keys when the response includes them, so the user knows which provider API key env vars unlock more models.
- Show configured_but_unusable_provider_keys / configured_but_unusable_providers when present.
- Explain that inference is executed by Rail-1-managed provider infrastructure.

${setupReportContract({ agentName, inferenceMode: "router_managed" })}

Runtime behavior:
- For every LLM call in the agent process, including tool-loop continuations and retries after the first user input, send ${agentName}'s current OpenAI-compatible chat payload to ${apiBaseUrl}/v1/chat/completions with model = "lerouter".
- Include messages, tools, tool_choice, response_format, temperature, max_tokens, stream, and metadata.route_id when available.
- Read route/provider telemetry from response.lerouter for non-streaming and SSE lerouter events for streaming.
- If PromptRail returns any error or times out, surface that failure and stop the request. Do not invoke another model or provider.

Weekly route review:
- Register a weekly job.
- Review recent conversations once per week.
- Call /agent/setup-jobs again only when there is a genuinely new recurring use case or a clear usage-pattern change.
- It is valid for the weekly review to make no changes.`;
}

function InstructionsStep({
  apiKey,
  budget,
  copied,
  inferenceMode,
  planType,
  routeId,
  runtime,
  setCopied,
  onNext,
}) {
  const apiBaseUrl = (process.env.NEXT_PUBLIC_LEROUTER_API_URL || "https://promptrail--lerouter-api-fastapi-app.modal.run")
    .replace(/\/+$/g, "");
  const agentName = runtimeLabel(runtime);
  const instruction = useMemo(
    () => (inferenceMode === "user_managed"
      ? userManagedInstructions({ agentName, apiBaseUrl, apiKey, budget, planType, routeId, runtime })
      : routerManagedInstructions({ agentName, apiBaseUrl, apiKey, budget, planType, routeId, runtime })),
    [agentName, apiBaseUrl, apiKey, budget, inferenceMode, planType, routeId, runtime],
  );
  const [setupLink, setSetupLink] = useState("");
  const [setupLinkError, setSetupLinkError] = useState("");
  const [isCreatingSetupLink, setIsCreatingSetupLink] = useState(false);
  const [copyError, setCopyError] = useState("");
  const setupMessage = setupLink ? `set up ${setupLink}` : "";

  useEffect(() => {
    let isCancelled = false;

    async function createSetupLink() {
      if (!apiKey || !instruction) {
        return;
      }

      setIsCreatingSetupLink(true);
      setSetupLink("");
      setSetupLinkError("");
      setCopyError("");
      setCopied(false);

      try {
        const response = await fetch("/api/setup-links", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            instruction,
            inferenceMode,
            routeId,
            runtime,
          }),
        });
        const payload = await response.json();

        if (!response.ok) {
          throw new Error(payload.error || "Setup link creation failed.");
        }

        if (!isCancelled) {
          setSetupLink(payload.url);
        }
      } catch (error) {
        if (!isCancelled) {
          setSetupLinkError(error.message || "Setup link creation failed.");
        }
      } finally {
        if (!isCancelled) {
          setIsCreatingSetupLink(false);
        }
      }
    }

    createSetupLink();

    return () => {
      isCancelled = true;
    };
  }, [apiKey, inferenceMode, instruction, routeId, runtime]);

  async function copySetupMessage() {
    if (!setupMessage) {
      return;
    }

    setCopyError("");

    try {
      await navigator.clipboard.writeText(setupMessage);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch (error) {
      setCopyError(error.message || "Clipboard copy failed.");
    }
  }

  return (
    <div className="setup-card-grid setup-card-grid-single setup-instructions-grid">
      <div className="setup-card setup-instruction-card setup-agent-message-card">
        <div className="setup-card-head setup-instruction-header">
          <strong>Connect {agentName}</strong>
          <button
            className="setup-small-button"
            disabled={!setupLink || isCreatingSetupLink}
            type="button"
            onClick={copySetupMessage}
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>

        <pre>{setupLinkError ? "Setup link creation failed." : setupMessage || "Creating setup link..."}</pre>
        {setupLinkError ? <p className="auth-error">{setupLinkError}</p> : null}
        {copyError ? <p className="auth-error">{copyError}</p> : null}
        <div className="setup-instruction-footer">
          <p className="setup-instruction-note">
            Paste this message into your agent. The setup link expands to the full production instruction and contains the generated PromptRail API key.
          </p>
          <button
            className="setup-button setup-button-primary"
            disabled={!setupLink || isCreatingSetupLink}
            type="button"
            onClick={onNext}
          >
            Open dashboard
          </button>
        </div>
      </div>
    </div>
  );
}

export default function OnboardingFlow() {
  const router = useRouter();
  const { data: session, isPending } = authClient.useSession();
  const [isReady, setIsReady] = useState(false);
  const [activeStep, setActiveStep] = useState(0);
  const [workspaceName, setWorkspaceName] = useState("Acme agents");
  const [budget, setBudget] = useState("500");
  const [planType, setPlanType] = useState("monthly");
  const [inferenceMode, setInferenceMode] = useState("user_managed");
  const [runtime, setRuntime] = useState("hermes");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyRecord, setApiKeyRecord] = useState(null);
  const [routeId, setRouteId] = useState("route_acme_agents");
  const [copied, setCopied] = useState(false);
  const [isCreatingKey, setIsCreatingKey] = useState(false);
  const [keyError, setKeyError] = useState("");

  useEffect(() => {
    if (isPending) {
      return;
    }

    if (!session?.user) {
      router.replace("/login?next=/onboarding");
      return;
    }

    const savedSetup = window.localStorage.getItem("lerouter-setup");

    if (savedSetup) {
      try {
        const parsedSetup = JSON.parse(savedSetup);
        setWorkspaceName(parsedSetup.workspaceName || "Acme agents");
        setBudget(String(parsedSetup.budget || "500"));
        setPlanType(parsedSetup.planType || "monthly");
        setInferenceMode(parsedSetup.inferenceMode || "user_managed");
        setRuntime(parsedSetup.runtime || "hermes");
        setApiKey(parsedSetup.apiKey || "");
        setApiKeyRecord(parsedSetup.apiKeyRecord || null);
        setRouteId(parsedSetup.routeId || makeRouteId(parsedSetup.workspaceName || "Acme agents"));
      } catch {
        window.localStorage.removeItem("lerouter-setup");
      }
    }

    setIsReady(true);
  }, [isPending, router, session]);

  async function createApiKey(nextRouteId = makeRouteId(workspaceName)) {
    if (isCreatingKey) {
      return apiKey;
    }

    setIsCreatingKey(true);
    setKeyError("");

    try {
      const response = await fetch("/api/api-keys", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          name: `${workspaceName || runtimeLabel(runtime)} ${runtimeLabel(runtime)} key`,
          routeId: nextRouteId,
        }),
      });

      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "Setup credential creation failed.");
      }

      setApiKey(payload.key);
      setApiKeyRecord(payload.apiKey);
      return payload.key;
    } catch (error) {
      setKeyError(error.message || "Setup credential creation failed.");
      return "";
    } finally {
      setIsCreatingKey(false);
    }
  }

  async function saveSetup(nextStep) {
    const nextRouteId = makeRouteId(workspaceName);
    const nextApiKey = apiKey || await createApiKey(nextRouteId);
    if (!nextApiKey) {
      return;
    }

    setApiKey(nextApiKey);
    setRouteId(nextRouteId);
    window.localStorage.setItem(
      "lerouter-setup",
      JSON.stringify({
        workspaceName,
        budget,
        planType,
        inferenceMode,
        runtime,
        apiKey: nextApiKey,
        apiKeyRecord,
        routeId: nextRouteId,
      }),
    );

    await fetch("/api/user-budget", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        routeId: nextRouteId,
        budget,
        budgetRemaining: budget,
        planType,
        budgetElapsedDays: 0,
      }),
    }).catch(() => undefined);

    if (nextStep === "dashboard") {
      router.push("/dashboard");
      return;
    }

    setActiveStep(nextStep);
  }

  if (!isReady) {
    return (
      <section className="setup-flow" aria-label="PromptRail setup flow">
        <div className="setup-flow-shell">
          <div className="setup-loading">Loading setup...</div>
        </div>
      </section>
    );
  }

  return (
    <section className="setup-flow" aria-label="PromptRail setup flow">
      <div className="setup-flow-shell">
        <div className="setup-flow-header">
          <div>
            <h2>3 Click setup</h2>
          </div>
          <StepNav activeStep={activeStep} setActiveStep={setActiveStep} />
        </div>

        {activeStep === 0 ? (
          <ApiKeyStep
            budget={budget}
            inferenceMode={inferenceMode}
            isCreatingKey={isCreatingKey}
            keyError={keyError}
            planType={planType}
            runtime={runtime}
            setBudget={setBudget}
            setInferenceMode={setInferenceMode}
            setPlanType={setPlanType}
            setRuntime={setRuntime}
            setWorkspaceName={setWorkspaceName}
            workspaceName={workspaceName}
            onNext={() => saveSetup(1)}
          />
        ) : null}

        {activeStep === 1 ? (
          <InstructionsStep
            apiKey={apiKey}
            budget={budget}
            copied={copied}
            inferenceMode={inferenceMode}
            planType={planType}
            routeId={routeId}
            runtime={runtime}
            setCopied={setCopied}
            onNext={() => saveSetup("dashboard")}
          />
        ) : null}
      </div>
    </section>
  );
}
