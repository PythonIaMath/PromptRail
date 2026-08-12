# PromptRail Runtime SDK

The Runtime SDK supplies live execution identity to the PromptRail gateway and runtime-events service. It does not perform routing, provider selection, context compaction, cache optimization, reasoning selection, output budgeting, cost allocation, latency allocation, historical analysis, or prediction in the client process.

## Install

The core package has no mandatory OpenTelemetry or OpenAI dependency.

```bash
pip install promptrail
pip install "promptrail[opentelemetry]"
pip install "promptrail[openai]"
pip install "promptrail[runtime]"  # OpenTelemetry + OpenAI
```

## Minimal integration

```python
import os

from openai import OpenAI
from promptrail import PromptRail, wrap_openai

PromptRail.init(
    api_key=os.environ["PROMPTRAIL_API_KEY"],
    application="coding-agent",
    environment="production",
    user_id=lambda: get_current_user_id(),
)

client = wrap_openai(
    OpenAI(
        base_url="https://api.promptrail.ai/v1",
        api_key=os.environ["PROMPTRAIL_API_KEY"],
    )
)
```

`wrap_openai` is intentionally explicit. PromptRail does not monkey-patch the OpenAI package. The returned proxy preserves the normal `chat.completions.create`, `completions.create`, `responses.create`, and `embeddings.create` interfaces while supplying fresh `extra_headers` for every call.

Generic HTTP clients can use `inject_headers`, `httpx_request_hook`, or `async_httpx_request_hook`. Private PromptRail headers are added only when the request origin matches `gateway_url`.

## Initialization

```python
PromptRail.init(
    api_key="pr_live_...",
    application="my-agent",
    environment="production",
    user_id=lambda: current_enterprise_user_id(),
    capture_content=False,
    gateway_url="https://api.promptrail.ai/v1",
    runtime_events_endpoint="https://api.promptrail.ai/v1/runtime/events",
    queue_size=2048,
    batch_size=50,
    flush_interval=0.25,
    shutdown_timeout=5.0,
    request_timeout=5.0,
    max_retries=3,
    compression=True,
    enable_opentelemetry=True,
    debug=False,
)
```

Important options:

- `application` and `environment` associate current activity with server-side historical profiles.
- `user_id` accepts a stable string or a callback. Callback failures are fail-open.
- `capture_content=False` selects `metadata_only`, the default telemetry policy.
- `runtime_events_endpoint` supports enterprise-hosted ingestion endpoints. Non-local endpoints must use HTTPS.
- exporter queues and batches are bounded.
- `enable_opentelemetry=False` disables only the tracing adapter. Explicit runs and request headers still work.

Call `PromptRail.shutdown()` during orderly process shutdown. An `atexit` hook also attempts the configured flush.

## Runtime context

```python
RuntimeContext(
    user_id: str | None,
    run_id: str | None,
    trace_id: str | None,
    span_id: str | None,
    parent_span_id: str | None,
)
```

Inactive code can have `run_id=None`. Every emitted runtime event and wrapped gateway request gets a run ID.

Helpers:

```python
from promptrail import (
    current_run_id,
    current_runtime_context,
    current_trace_id,
    current_user_id,
)
```

## Run detection

Run identity follows this order:

1. A root OpenTelemetry span observed after initialization is mapped to a PromptRail run.
2. An explicit sync or async run supplies a stable boundary.
3. A wrapped OpenAI call creates a short implicit run if neither context exists.

```python
from promptrail import run

with run(user_id="tenant_3:user_81"):
    result = agent.invoke(...)

async with run(user_id="tenant_3:user_81"):
    result = await agent.ainvoke(...)
```

Nested and parallel async tasks inherit context through `contextvars`. Python thread pools do not propagate context automatically, so use:

```python
from promptrail import submit_with_context

future = submit_with_context(executor, function, argument)
```

User precedence is explicit run user, contextual user, configured resolver, then `None`.

## OpenTelemetry integration

When the OpenTelemetry SDK is installed, initialization adds one `PromptRailSpanProcessor` to the active `TracerProvider`. Existing span processors and exporters are left intact. If only the API proxy exists, PromptRail creates an SDK provider so subsequently created spans are recordable.

The processor:

- maps root traces to runs
- stores span-parent, span-run, and span-user correlation
- normalizes start/end/error events
- classifies spans using explicit and semantic attributes before conservative name hints
- performs no network I/O
- catches all observer exceptions

Supported classifications are `agent`, `tool`, `retrieval`, `llm`, `workflow`, `branch`, and `other`.

Recommended explicit attributes include:

```python
span.set_attribute("promptrail.span.type", "tool")
span.set_attribute("tool.name", "search_repository")
```

PromptRail uses the application's configured global OpenTelemetry propagator for `traceparent`, `tracestate`, and any additional standard fields. It does not replace that propagator. `PromptRailContextPropagator` is available for explicit integration points.

## Gateway correlation

A wrapped request is correlated synchronously, without waiting for event ingestion:

```http
traceparent: 00-<trace-id>-<span-id>-01
x-promptrail-run-id: run_...
x-promptrail-user-id: tenant_3:user_81
x-promptrail-trace-id: ...
x-promptrail-span-id: ...
x-promptrail-parent-span-id: ...
x-promptrail-application: coding-agent
x-promptrail-environment: production
x-promptrail-schema-version: 1.0
x-promptrail-sdk-version: 0.1.0
```

PromptRail-owned values replace stale caller values on each gateway request. Other headers are preserved. Direct provider requests are returned unchanged. The gateway must strip private `x-promptrail-*` headers before provider forwarding.

## Manual events

Manual events are an escape hatch, not a normal instrumentation requirement.

```python
from promptrail import event

event(
    "workflow.stage",
    name="verification",
    status="started",
    attributes={"attempt": 2},
)
```

Canonical span event types and the JSON contract are documented in [runtime-event-schema.md](runtime-event-schema.md).

## Privacy

`metadata_only` drops known prompt, completion, message, document, source-code, request body, and tool input/output values. It retains operational metadata such as tool/model names, IDs, counts, token counts, hashes, durations, and input/output sizes. Values are depth-, length-, item-, and total-count-bounded. Arbitrary Python objects are never traversed or serialized.

`capture_content=True` opts telemetry into bounded content capture. This setting is separate from live gateway processing. LLM content still reaches the PromptRail gateway for inference optimization.

## Export and failure behavior

Events follow this path:

```text
application -> put_nowait -> bounded queue -> daemon worker -> batch -> HTTPS keep-alive
```

The worker uses compact JSON, optional gzip, exponential backoff, bounded retry attempts, and a shutdown deadline. Queue saturation drops the newest event and increments a local counter.

The following never fail the application:

- user resolver exceptions
- missing OpenTelemetry or OpenAI extras
- unsupported or malformed spans
- queue saturation
- event serialization errors
- connection failures, timeouts, and non-success ingestion responses
- shutdown flush failures
- instrumentation context-manager failures

Invalid static initialization configuration fails fast because it is a developer error.

## Debug logging

```python
PromptRail.init(..., debug=True)
```

Debug logging reports initialization, provider detection, span processor attachment, run lifecycle, queue insertion, header injection, batch export, and sanitized integration failures. Prompt, tool, and LLM content is not logged by the SDK.

## Performance

The hot path has no synchronous network calls. Current measured local results are in [runtime-benchmarks.md](runtime-benchmarks.md). Re-run with:

```bash
uv run pytest -q -s tests/performance/test_runtime_overhead.py
```

## Current limitations

- Transparent OpenAI injection requires `wrap_openai` or an HTTP hook. Endpoint-only injection would require brittle global monkey-patching, which the SDK intentionally avoids.
- Abandoned stream objects should be explicitly closed so their instrumentation contexts are released promptly.
- Automatic root-run detection observes spans started after `PromptRail.init()`.
- Thread-pool propagation requires `submit_with_context` or an equivalent `contextvars.copy_context()` wrapper.
- The MVP includes only the OpenTelemetry adapter. It does not import historical traces.

## Recommended adapter next steps

### LangSmith

Build an optional `promptrail[langsmith]` adapter that maps LangSmith run-tree IDs and parent IDs into the canonical `RuntimeContext`, listens to callback lifecycle events, and delegates event sanitization/export to the existing core. Prefer an existing OpenTelemetry bridge when both systems are present so PromptRail does not create duplicate spans.

### Langfuse

Build an optional `promptrail[langfuse]` adapter that reads the active trace/observation context, maps generation/tool/retrieval observations into canonical types, and injects the same run/trace/span headers at gateway calls. Do not replace Langfuse exporters or upload historical traces from this SDK.

Both adapters should implement the small interfaces in `promptrail.tracing.base`, reuse the bounded exporter and privacy policy, preserve native IDs, and include the same concurrency and fail-open contract tests as OpenTelemetry.
