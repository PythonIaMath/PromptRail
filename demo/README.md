# PromptRail live Codex demo

This demo runs two real Codex coding agents against isolated copies of the same workspace:

- **Baseline** uses the model selected with `/model` and no PromptRail optimization.
- **PromptRail** runs each model call through the repository's budget controller, cache-aware
  model router, provider planner, safe context compactor, and usage settlement path.

The managed lane loads documents marked `supports_tools: true` from
`lerouter.model_profiles`, then disables every `:free` slug and every zero-priced route such as
`openrouter/free`. The current catalog contains 274 tool-capable entries, of which 17 are free or
zero-priced routes, leaving 257 paid candidates. Proprietary and open-source paid models remain
available. For this demo only, every
candidate route uses OpenRouter even when its stored production route is disabled or points to a
direct provider. MongoDB is read-only and is not modified.

The baseline lane calls OpenAI directly for GPT-5.6 Sol, Terra, and Luna, and calls OpenRouter
for Kimi K3. PromptRail and its paid control plane call OpenRouter. The demo removes
`max_output_tokens` and `max_tokens`
from both agent lanes and disables the managed agent-call guard, so only each
provider/model's native limits apply.

ModernBERT output prediction, deterministic context analysis, and twelve one-model
Gemma rankers start concurrently. As soon as ModernBERT and context analysis finish,
the budget allocator starts while any slower ranking requests continue. ModernBERT
and the rankers use persistent HTTP/2 pools; the deployed ModernBERT service keeps one
warm Modal container. Each
settled usage event records actual `ttft_ms`, full `total_latency_ms`, negotiated
`http_version`, tokens, and provider cost.

Within one agent run, the twelve scores are reused only for an exact normalized task,
required-capability set, catalog version, controller model, and model-availability set.
A task, capability, catalog, or health change produces a cache miss and twelve new real
one-number Gemma requests.

The allocator always includes the catalog's cheapest context/capability-compatible
model. If Gemma returns cost or latency below that model's exact admission floor,
the demo records an `allocation` repair and cascades to that feasible floor instead
of returning `no model satisfies the call budget`.
OpenRouter therefore receives the natural model maximum, or an explicit value supplied by Codex,
unchanged. The OpenRouter account must have enough credit for its worst-case affordability check.

If a selected model returns a provider/model error, the proxy consumes that error before Codex
sees it, temporarily excludes the failed model, and resends the already compacted request through
the next precomputed feasible model. The active call ID, budget, prediction, ranking, and compaction
are reused; provider failover does not invoke ModernBERT or Gemma again. Authentication and
account-credit failures (HTTP 401/402) remain terminal because every model would receive the same
failure. Model/provider policy failures, including HTTP 403, cascade to another model.

The router keeps the most recent user request as the task throughout Codex tool continuations;
shell output never replaces the routing goal. The supplied demo policy requires measured catalog
quality of at least `0.1` for coding, repository, bug-fix, implementation, and architecture work.
Models below that threshold remain routable for tasks that do not require it.

To keep the control plane fast, deterministic evidence from MongoDB narrows the 257 paid candidates
before each LLM control call: 12 candidates for budget allocation and 12 for semantic
ranking. All 257 remain in PromptRail's candidate universe and can satisfy the final budget,
context, capability, cache, and cost checks. Shortlisting uses only stored forces, measured coding
quality, price, latency, and context evidence.

The generated Codex model catalog uses direct, standard function tools instead of Sol's
model-specific Responses Lite code mode. Every selected model therefore receives the same explicit
Codex shell and continuation contract. PromptRail does not quarantine models or reinterpret short
answers as failures.

The managed lane has no configured agent-call ceiling.

Every call uses OpenRouter's returned `usage.cost`, including the fixed baseline, selected agent
models, controller, and ranker. The PromptRail total therefore includes control-plane spend. There
are no projected prices or synthetic token counts.

## Configure

Put the existing key in `demo/.env` (ignored by git), export it in the shell, or pass the file that
already contains it:

```bash
cp demo/.env.example demo/.env
# edit demo/.env and set OPENAI_API_KEY, OPENROUTER_API_KEY, and MONGODB_URI
```

The demo intentionally skips the enterprise analytics agent. `policy.json` is loaded through
`SuppliedPolicyAgent`; `demo_input.json` is only the immutable source-digest binding required by
the gateway. Model IDs, price, latency, context, forces, benchmarks, and measured quality evidence
come from MongoDB. The controller and ranker use the paid OpenRouter slug
`google/gemma-3-12b-it`; startup rejects a `:free` controller override.
The direct ModernBERT endpoint additionally requires `LEROUTER_INTERNAL_SERVICE_TOKEN`.
When it is not already exported, the demo resolves `lerouter-internal-service-token`
through the active Modal CLI profile once at startup, keeps it in process memory,
and never writes or logs it. The demo fails visibly if Modal authentication is
unavailable or the endpoint returns an invalid prediction.

## Run

```bash
uv run --project demo python demo/run.py
```

To use an existing environment file without copying its secret:

```bash
uv run --project demo python demo/run.py --env-file /absolute/path/to/.env
```

Enter a prompt. It is submitted simultaneously to two persistent Codex conversations. Each lane
gets its own copy under `demo/runs/`, so tool calls cannot race over the same files. When both lanes
finish a turn, cumulative PromptRail savings appear for five seconds, then the split conversation
returns with a new prompt. Both lanes resume their exact Codex thread and workspace, preserving
conversation and tool context across turns. Type `q` at the prompt to quit.

The complete conversation remains in scrollback across turns. Press `Tab` to focus the baseline
or PromptRail pane (the focused heading is highlighted), then use `Up`/`Down`, `Page Up`/`Page
Down`, `Home`, or `End` to navigate it. Mouse-wheel scrolling also works in terminals that expose
wheel events to curses. These controls remain active while a turn is running and while entering
the next prompt.

Type `/model` to list comparison baselines, or select one directly:

```text
/model sol
/model terra
/model luna
/model kimi
```

The selected model applies to the next baseline turn. Both Codex conversations retain their
multi-turn context, while displayed cost, token, cache, and call totals start a new comparison
window so spend from different baseline models is never mixed into one savings percentage.

The conversation panes use compact event labels: `USER`, `BUDGET`, `ROUTE`, `CONTROL`,
`RUN`, `DONE`, `UPDATE`, `FAILOVER`, `USAGE`, `ANSWER`, `COMPLETE`, and `ERROR`. Intermediate
agent commentary is shown as `UPDATE`; only the message immediately followed by
`turn.completed` receives the answer divider. Long shell wrappers and generated snapshot
paths are collapsed. The savings screen reports each lane's actual cost, total metered tokens,
cached tokens, metered calls, and latest agent model alongside the percentage result.
Initial and resumed subprocesses are OS-bound to their lane workspace, so a later turn cannot read
or modify the real repository root. The code-only demo also withholds the image-viewing tool from
routed models; shell and file-editing tools remain available.

OpenRouter model availability changes over time. A stale MongoDB model can therefore be considered
by PromptRail but rejected by OpenRouter if its endpoint no longer exists.
OpenRouter can reject a model before inference when the account cannot afford the request's natural
maximum output. The demo intentionally does not lower that maximum.

If a paid model returns HTTP 429, the demo drains the unbilled error, places that model on a
two-minute health cooldown, and uses the next feasible model from the existing ranked plan under
the same reservation. This avoids both retrying one unavailable endpoint
and repeating the multi-second control plane while keeping failures and costs real.

## Real latency benchmark

Run the 30-warm/3-cold control-plane benchmark with:

```bash
uv run --project demo python demo/benchmark_control_plane.py \
  --env-file /absolute/path/to/.env
```

It writes raw JSONL samples plus p50/p95/max stage and matched-provider TTFT
summaries under `demo/benchmarks/`, including output-prediction absolute error,
percentage error, and underprediction rate. The same run also performs a real
four-turn managed conversation with a hidden continuity marker. It fails if later
turns lose the marker and records context growth, cache reads, compaction, model
changes, cost, and TTFT for every turn. Use `--multiturn-turns 2`, `3`, or `4` to
change its length.
The runner refuses to execute when any real-service credential is missing; it never
substitutes synthetic timings.

For the same live two-lane Codex interface in a local browser:

```bash
uv run --project demo python demo/web.py \
  --env-file /absolute/path/to/.env
```

Open `http://127.0.0.1:8788`. The server binds to localhost only and keeps credentials
out of the browser. It launches the same baseline and PromptRail Codex commands, proxy
backend, isolated workspaces, persistent multi-turn threads, live provider accounting,
`/model` selector, and five-second savings screen as the terminal UI. One comparison
session can run at a time.
