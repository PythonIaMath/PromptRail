# PromptRail Runtime SDK Architecture

## Purpose

The Runtime SDK is a lightweight observation and correlation layer. It gives the PromptRail gateway and runtime-events service enough identity to combine an incoming LLM request with the current enterprise application, user, run, OpenTelemetry trace, and execution span.

It does not perform model routing, provider routing, context compaction, cache management, reasoning selection, output budgeting, cost allocation, latency allocation, historical analysis, or trajectory prediction. Those responsibilities remain server-side and in the existing PromptRail control plane.

## Public surface

```python
from promptrail import PromptRail, run, wrap_openai

PromptRail.init(
    api_key="pr_live_...",
    application="coding-agent",
    environment="production",
    user_id=lambda: current_user_id(),
)

with run(user_id="user_123"):
    client = wrap_openai(openai_client)
    response = client.chat.completions.create(...)

PromptRail.shutdown()
```

The module also exposes `current_runtime_context()`, `current_run_id()`, `current_user_id()`, `current_trace_id()`, `event()`, `inject_headers()`, and `copy_context()` as small escape hatches.

## Components

```text
Application
  |
  | contextvars + current OpenTelemetry context
  v
Runtime context resolver
  |-- explicit run context
  |-- root-trace run registry
  `-- per-call implicit run fallback
  |
  +--------------------------+
  |                          |
  v                          v
Gateway metadata             Canonical events
(synchronous, local)         (synchronous, local)
  |                          |
  | traceparent              v
  | x-promptrail-*        bounded queue
  v                          |
PromptRail gateway            v
                         exporter worker
                              |
                              v
                    POST /v1/runtime/events
```

### Runtime context

Runtime identity is immutable and stored in `contextvars`, never process-global mutable request state. The canonical context contains:

- `user_id`
- `run_id`
- `trace_id`
- `span_id`
- `parent_span_id`

User identity precedence is explicit run override, contextual user, configured resolver, then `None`. Resolver failures are isolated and only logged in debug mode.

### Run detection

Run resolution follows this order:

1. A root OpenTelemetry span is mapped to a PromptRail run ID by the span processor.
2. `promptrail.run(...)` creates an explicit run context for sync and async code.
3. An OpenAI-wrapped or direct header-injection call creates a short implicit run when no other boundary exists.

Trace mappings contain only correlation and lifecycle data. The backend owns all analytical RunState.

### OpenTelemetry

The OpenTelemetry adapter is optional. When an SDK `TracerProvider` already exists, PromptRail adds one span processor and does not replace the provider or existing exporters. If only the OpenTelemetry API proxy exists and the SDK extra is installed, PromptRail creates the provider once. If OpenTelemetry is absent or incompatible, initialization continues without tracing.

The span processor:

- maps root traces to run IDs
- records span-to-parent relationships used for request metadata
- classifies spans from semantic attributes before name heuristics
- normalizes start/end/error events
- never exports synchronously
- never raises into application code

PromptRail uses the installed global text-map propagator to inject standard trace headers. PromptRail-specific propagation is additive and does not replace enterprise propagators.

### OpenAI request correlation

`wrap_openai(client)` returns an interface-compatible proxy. It intercepts OpenAI resource `create(...)` calls, creates an optional `gen_ai` client span, and supplies per-request `extra_headers`. Metadata is computed immediately inside the active call context and includes:

- standard `traceparent` and any configured propagated headers
- `x-promptrail-run-id`
- `x-promptrail-user-id` when available
- `x-promptrail-trace-id` when available
- `x-promptrail-span-id` when available
- `x-promptrail-parent-span-id` when available
- application, environment, schema version, and SDK version

Injection occurs only for configured PromptRail gateway origins. Existing caller headers win only for non-PromptRail names. PromptRail-owned identity headers are refreshed to avoid stale context leaking across concurrent requests.

The explicit wrapper is preferred over monkey-patching the OpenAI package. Generic HTTP integrations can call `inject_headers(...)` or use the provided HTTP request hook.

### Event exporter

Event creation and queue insertion are local and non-blocking. A daemon worker owns HTTP I/O and a persistent standard-library HTTP(S) connection. It batches events, optionally gzip-compresses useful payloads, retries transient failures with exponential backoff, and drops events when the bounded queue is saturated.

Shutdown stops new exports, flushes the current queue up to the configured timeout, and closes the connection. Export failures never fail the application.

### Privacy

The default `metadata_only` policy accepts bounded scalar metadata and size/count summaries. It removes known content-bearing keys and never serializes arbitrary Python objects. `content` mode is opt-in and still applies depth, item-count, string-size, and total attribute-count limits.

Gateway processing policy is separate. This telemetry policy controls runtime-event storage only.

## Fail-open boundaries

Every optional integration boundary catches `Exception`, records a sanitized debug message when enabled, and returns control to the application. This includes user resolvers, tracing registration, span classification, event serialization, queue insertion, HTTP export, OpenAI proxy instrumentation, and shutdown.

Configuration validation at explicit initialization is the exception: invalid static values fail fast because they are developer mistakes before instrumentation begins.

## Concurrency

- `contextvars` isolate async tasks and nested contexts.
- OpenTelemetry supplies native async span propagation.
- Run and span registries use short critical sections and immutable values.
- Thread-pool propagation is explicit through `promptrail.copy_context()` or `submit_with_context(...)`, matching Python's contextvars model.
- No user/run identity is stored in module globals.

## Dependency strategy

The core uses the Python standard library and the repository's existing Pydantic dependency. Optional extras provide:

- `promptrail[opentelemetry]`
- `promptrail[openai]`
- `promptrail[runtime]`
- future `langsmith`, `langfuse`, and `all` adapters

Adapter imports are lazy so core initialization remains usable when extras are absent.

## Performance strategy

Hot paths use frozen slotted dataclasses, direct `contextvars` lookups, bounded dictionaries, compact ID formatting, and `put_nowait`. They do no synchronous network I/O. Performance tests benchmark context lookup, event creation, queue insertion, header injection, and span normalization, with the target of P50 below 100 microseconds and P99 below 1 millisecond for local event work.
