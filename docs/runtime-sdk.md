# PromptRail Python SDK

The PromptRail Runtime SDK connects your application to the PromptRail control plane. It adds run, user, trace, and span identity to LLM requests and exports execution metadata in the background.

The SDK observes your application. Model routing, cost and latency allocation, provider selection, context compaction, and historical analysis happen in PromptRail services, not inside your process.

## Status

The Python SDK is under active construction. The runtime context, OpenAI wrapper, OpenTelemetry processor, event exporter, privacy policy, and historical JSON normalization APIs are implemented and tested.

Remote historical connectors for LangSmith, Langfuse, Braintrust, and Helicone are not deployed yet. The current connection interface validates their configuration but does not start a remote sync.

## Requirements

- Python 3.11 or newer
- A PromptRail API key for gateway requests and event export
- The `openai` or `opentelemetry` extra only when you use that integration

## Install

Choose the smallest extra that fits your application:

```bash
pip install promptrail
pip install "promptrail[openai]"
pip install "promptrail[opentelemetry]"
pip install "promptrail[runtime]"  # OpenAI + OpenTelemetry
pip install "promptrail[all]"      # Runtime + LangChain + HTTP integrations
```

For local development in this repository:

```bash
uv sync --extra dev
```

## Five-minute setup

### 1. Initialize once

Initialize PromptRail when your application process starts.

```python
import os

from promptrail import PromptRail

PromptRail.init(
    api_key=os.environ["PROMPTRAIL_API_KEY"],
    application="support-agent",
    environment="production",
    user_id=lambda: get_current_user_id(),
)
```

`application` identifies the workload whose normal execution PromptRail learns. `environment` keeps development and production behavior separate. `user_id` may be a stable string, a callback, or `None`.

### 2. Send LLM calls through the PromptRail gateway

```python
import os

from openai import OpenAI
from promptrail import run, wrap_openai

client = wrap_openai(
    OpenAI(
        base_url="https://api.promptrail.ai/v1",
        api_key=os.environ["PROMPTRAIL_API_KEY"],
    )
)

with run(user_id="customer_123"):
    response = client.responses.create(
        model="gpt-4o-mini",
        input="Summarize the open support ticket.",
    )

print(response.output_text)
```

`wrap_openai` preserves the official OpenAI interface. It adds fresh correlation headers to calls whose origin matches the configured PromptRail gateway. Direct provider clients are returned unchanged, so PromptRail metadata is not sent to unrelated hosts.

### 3. Flush during shutdown

```python
PromptRail.shutdown()
```

PromptRail also registers an `atexit` hook, but an explicit shutdown gives the exporter its full flush deadline.

A complete runnable version lives in [`examples/openai_basic.py`](../examples/openai_basic.py).

## How requests and events move

```text
application code
  |
  | run context + active OpenTelemetry span
  v
PromptRail SDK
  |-- synchronously adds identity headers to gateway requests
  `-- asynchronously queues canonical runtime events
          |
          v
    POST /v1/runtime/events
```

Request correlation does not wait for event ingestion. The gateway receives the current identity on the LLM request itself.

## Initialization reference

```python
PromptRail.init(
    api_key=None,
    application=None,
    environment=None,
    user_id=None,
    capture_content=False,
    privacy_mode=None,
    gateway_url="https://api.promptrail.ai/v1",
    runtime_events_endpoint="https://api.promptrail.ai/v1/runtime/events",
    export_enabled=True,
    enable_opentelemetry=True,
    queue_size=2048,
    batch_size=50,
    flush_interval=0.25,
    shutdown_timeout=5.0,
    request_timeout=5.0,
    max_retries=3,
    compression=True,
    debug=False,
)
```

| Option | Default | Meaning |
| --- | --- | --- |
| `api_key` | `None` | Authenticates gateway requests and runtime event export. No worker starts without a key. |
| `application` | `None` | Stable application or agent name. Empty strings are rejected. |
| `environment` | `None` | Deployment environment such as `production` or `staging`. |
| `user_id` | `None` | Stable user string or zero-argument resolver callback. Resolver failures are fail-open. |
| `capture_content` | `False` | Convenience switch for content telemetry. Metadata-only is safer and is the default. |
| `privacy_mode` | derived | Explicitly selects `metadata_only` or `content`. |
| `gateway_url` | PromptRail API | Origin allowed to receive private PromptRail headers. |
| `runtime_events_endpoint` | `/v1/runtime/events` | Runtime event ingestion URL. Non-local HTTP URLs are rejected. |
| `export_enabled` | `True` | Enables the background event exporter when an API key exists. |
| `enable_opentelemetry` | `True` | Installs the PromptRail span processor when OpenTelemetry is available. |
| `queue_size` | `2048` | Maximum queued events. Saturation drops the newest event. |
| `batch_size` | `50` | Maximum events per export batch. |
| `flush_interval` | `0.25` | Maximum idle seconds before a partial batch is sent. |
| `shutdown_timeout` | `5.0` | Default exporter shutdown deadline in seconds. |
| `request_timeout` | `5.0` | HTTP event export timeout in seconds. |
| `max_retries` | `3` | Bounded retry attempts for transient export failures. |
| `compression` | `True` | Gzip-compresses event payloads when useful. |
| `debug` | `False` | Emits sanitized SDK diagnostics. |

`capture_content=True` conflicts with `privacy_mode="metadata_only"` and raises `ValueError`.

Local HTTP endpoints are allowed only for `localhost`, `127.0.0.1`, and `::1`. All other endpoints must use HTTPS.

## Run context

A `RuntimeContext` contains:

```text
RuntimeContext(
    user_id: str | None,
    run_id: str | None,
    trace_id: str | None,
    span_id: str | None,
    parent_span_id: str | None,
)
```

Create an explicit boundary around one user-visible workflow:

```python
from promptrail import current_runtime_context, run

with run(user_id="customer_123", run_id="run_ticket_891"):
    result = agent.invoke(...)
    print(current_runtime_context())
```

The same API works with asynchronous code:

```python
async with run(user_id="customer_123"):
    result = await agent.ainvoke(...)
```

Run identity resolves in this order:

1. A root OpenTelemetry trace mapped by the PromptRail span processor.
2. An explicit `run(...)` scope.
3. A short implicit run created around a wrapped gateway call.

Nested asyncio tasks inherit context through `contextvars`. Thread pools do not inherit it automatically. Use `submit_with_context`:

```python
from concurrent.futures import ThreadPoolExecutor

from promptrail import run, submit_with_context

with ThreadPoolExecutor() as executor:
    with run(user_id="customer_123"):
        future = submit_with_context(executor, process_ticket, ticket)
        result = future.result()
```

User identity precedence is explicit run user, contextual user, configured resolver, then `None`.

### Context helpers

```python
from promptrail import (
    copy_context,
    current_run_id,
    current_runtime_context,
    current_trace_id,
    current_user_id,
)
```

`copy_context()` returns the current `contextvars.Context`. Call its `run(...)` method when you need to enter that context manually:

```python
context = copy_context()
future = executor.submit(context.run, process_ticket, ticket)
```

## OpenAI integration

### Supported resources

The explicit wrapper instruments synchronous and asynchronous versions of:

- `chat.completions.create`
- `completions.create`
- `responses.create`
- `responses.stream`
- `embeddings.create`
- supported `with_raw_response` and `with_streaming_response` modifiers

```python
from openai import AsyncOpenAI
from promptrail import wrap_openai

client = wrap_openai(
    AsyncOpenAI(
        base_url="https://api.promptrail.ai/v1",
        api_key=api_key,
    )
)

response = await client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello"}],
)
```

PromptRail does not monkey-patch the OpenAI package. Non-target resources and attributes delegate to the original client.

Streaming keeps the implicit run and LLM span open until the stream is exhausted, explicitly closed, or raises an error.

### Origin safety

The wrapper checks `client.base_url` on every call. If code changes a wrapped client from the PromptRail gateway to a direct provider, the request bypasses instrumentation and receives no private PromptRail headers.

## Generic HTTP integration

### Manual headers

```python
from promptrail import inject_headers

headers = inject_headers(
    {"authorization": "Bearer existing-token"},
    url="https://api.promptrail.ai/v1/chat/completions",
)
```

The function returns a new dictionary. Existing non-PromptRail headers are preserved. Stale `x-promptrail-*` values are replaced for gateway requests and removed from non-gateway requests.

### httpx hooks

```python
import httpx

from promptrail import httpx_request_hook

client = httpx.Client(event_hooks={"request": [httpx_request_hook]})
```

For `httpx.AsyncClient`:

```python
from promptrail import async_httpx_request_hook

client = httpx.AsyncClient(event_hooks={"request": [async_httpx_request_hook]})
```

## OpenTelemetry integration

Install the extra:

```bash
pip install "promptrail[opentelemetry]"
```

PromptRail adds one `PromptRailSpanProcessor` to the active `TracerProvider`. It does not remove or replace existing processors and exporters. If OpenTelemetry is unavailable or incompatible, PromptRail initialization continues without tracing.

```python
from opentelemetry import trace
from promptrail import PromptRail

PromptRail.init(
    api_key=api_key,
    application="research-agent",
)

tracer = trace.get_tracer("my.application")

with tracer.start_as_current_span(
    "search-repository",
    attributes={
        "promptrail.span.type": "tool",
        "tool.name": "search_repository",
    },
):
    search_repository()
```

The classifier supports `agent`, `tool`, `retrieval`, `llm`, `workflow`, `branch`, and `other`. Stable semantic attributes win over span-name guesses.

Useful attributes include:

| Work type | Suggested attributes |
| --- | --- |
| LLM | `gen_ai.system`, `gen_ai.operation.name`, `gen_ai.request.model` |
| Tool | `tool.name`, `tool.call.id` |
| Retrieval | `retrieval.query`, `db.system`, `vector.top_k` |
| Agent | `agent.name`, `agent.id` |
| Workflow | `workflow.name`, `workflow.id` |
| Branch | `branch.name`, `branch.id` |

See [`examples/opentelemetry_existing.py`](../examples/opentelemetry_existing.py) and [`examples/multi_step_agent.py`](../examples/multi_step_agent.py).

## Manual events

Manual events help instrument boundaries that do not produce OpenTelemetry spans.

```python
from promptrail import EventType, event, run

with run(user_id="customer_123"):
    event(
        EventType.TOOL_END,
        name="lookup_invoice",
        status="success",
        attributes={
            "tool.name": "lookup_invoice",
            "output_size_bytes": 240,
            "duration_ms": 83,
        },
    )
```

`event(...)` returns the canonical `PromptRailEvent`, or `None` when observation fails. SDK observation is fail-open and does not raise into application work.

Canonical event types and their wire format are documented in [Runtime event schema](runtime-event-schema.md).

## Historical trace imports

Use historical imports to normalize existing execution data before sending it to a PromptRail ingestion service.

```python
from pathlib import Path

from promptrail import import_historical_traces

result = import_historical_traces(
    Path("historical-traces.jsonl").read_bytes(),
    metadata_only=True,
)

print(result.summary())
for runtime_event in result.events:
    print(runtime_event.to_json_dict())
```

Accepted inputs:

- PromptRail batches: `{"events": [...]}`
- OpenTelemetry JSON: `{"resourceSpans": [...]}`
- Generic span exports: `{"spans": [...]}`
- One generic event object
- JSONL with one event or span object per line
- Python mappings or iterables of mappings

Imports are limited to 50 MB for `bytes` and `str` inputs. Empty data, malformed JSONL, unsupported structures, and collections without objects raise `ValueError`.

The importer:

1. Detects the source structure.
2. Converts each record into schema 1.0 `PromptRailEvent` objects.
3. Classifies spans using the same classifier as live OpenTelemetry observation.
4. Applies the same metadata privacy policy as the live SDK.
5. Reports event, trace, run, and LLM-call counts.

### Trace source configuration

```python
from promptrail import TraceSourceConfiguration

configuration = TraceSourceConfiguration.validate(
    source="langfuse",
    project="production-agent",
    credential=read_only_key,
    metadata_only=True,
)

print(configuration.to_public_dict())
```

Supported source identifiers are:

- `langsmith`
- `langfuse`
- `braintrust`
- `helicone`
- `opentelemetry`
- `custom`

Credentials are validated for presence but are not retained by `TraceSourceConfiguration`. OpenTelemetry configuration does not require a credential.

Remote provider synchronization is not implemented in the current SDK. Do not describe a validated configuration as connected or synced until the corresponding control-plane connector is deployed.

### Run the connection interface locally

```bash
uv run python demo/connect_server.py --port 8789
```

Open <http://127.0.0.1:8789/connect>. The tracked preview server exercises SDK capabilities, configuration validation, and historical JSON imports.

## Privacy

The default mode is `metadata_only`.

It removes values whose keys indicate prompts, completions, messages, documents, source code, request bodies, responses, tool input, or tool output. It keeps safe operational fields such as identifiers, model and tool names, token counts, durations, hashes, sizes, MIME types, and statuses.

Attribute collection is bounded:

| Limit | Default |
| --- | ---: |
| Nested depth | 4 |
| Items per mapping or sequence | 50 |
| String length | 2,048 characters |
| Total attributes | 200 |

Arbitrary Python objects are dropped rather than traversed or serialized. Non-finite floats are dropped.

To opt into bounded content telemetry:

```python
PromptRail.init(
    api_key=api_key,
    privacy_mode="content",
)
```

or:

```python
PromptRail.init(
    api_key=api_key,
    capture_content=True,
)
```

This controls runtime telemetry only. LLM request content still reaches the PromptRail gateway when you use it for inference.

## Export behavior

Events are created and queued locally. A daemon worker performs network I/O.

```text
application -> bounded queue -> batch encoder -> optional gzip -> HTTPS keep-alive
```

The exporter:

- never blocks application code on event delivery
- retries transient network errors and HTTP `408`, `425`, `429`, `500`, `502`, `503`, and `504`
- does not retry permanent 4xx rejections
- drops the newest event when the queue is full
- uses a bounded shutdown deadline
- sends `Authorization: Bearer <api_key>` when configured
- identifies itself as `promptrail-python/<sdk-version>`

Event delivery is best-effort and uses no durable local queue. A retry can resend an event if
the server accepted a request but the client did not receive the response. Every event carries a
stable `event_id` so ingestion services can deduplicate retries.

## Failure model

Instrumentation should not break the application it observes.

| Failure | SDK behavior |
| --- | --- |
| User ID resolver raises | Uses no user ID and optionally logs in debug mode. |
| OpenTelemetry is missing | Continues without automatic span observation. |
| Event queue is full | Drops the newest event. |
| Event cannot serialize | Drops that event batch. |
| Export endpoint is unavailable | Retries within limits, then drops the batch. |
| Shutdown deadline expires | Stops waiting and allows process exit. |
| Direct provider URL is used | Does not inject PromptRail identity. |
| Invalid initialization value | Raises immediately because configuration is programmer-controlled. |
| Invalid historical import | Raises `ValueError` with a structural error message. |

## Troubleshooting

### Wrapped calls contain no PromptRail headers

Check all three conditions:

1. `PromptRail.init(...)` ran before the request.
2. The OpenAI client's current `base_url` has the same scheme, host, and port as `gateway_url`.
3. The call uses a supported OpenAI resource method.

Use this local check:

```python
from promptrail import inject_headers

print(inject_headers(url="https://api.promptrail.ai/v1/chat/completions"))
```

### Events are not exported

- Confirm `api_key` is non-empty.
- Confirm `export_enabled=True`.
- Confirm `runtime_events_endpoint` is correct.
- Call `PromptRail.shutdown()` before the process exits.
- Set `debug=True` for sanitized diagnostics.

### Spans are not observed

- Install `promptrail[opentelemetry]`.
- Initialize PromptRail before creating the spans you expect to observe.
- Check that the active provider exposes `add_span_processor`.
- Add explicit semantic attributes such as `promptrail.span.type` when classification is ambiguous.

### Context disappears in a thread

Python `contextvars` do not automatically cross thread-pool submissions. Use `submit_with_context` or wrap the callable with `copy_context`.

### Historical import reports zero LLM calls

The importer counts `llm.end` events. For generic spans, provide semantic attributes such as `gen_ai.system`, `gen_ai.operation.name`, or `gen_ai.request.model` so the classifier can identify LLM work.

## Public API

The main public surface is exported from `promptrail`:

| API | Purpose |
| --- | --- |
| `PromptRail.init(...)` | Initialize process-level runtime observation. |
| `PromptRail.shutdown(...)` | Flush and stop the exporter. |
| `run(...)` | Create a synchronous or asynchronous run boundary. |
| `wrap_openai(client)` | Instrument a PromptRail-gateway OpenAI client. |
| `inject_headers(...)` | Add current identity to an allowed gateway request. |
| `httpx_request_hook` | Synchronous httpx request hook. |
| `async_httpx_request_hook` | Asynchronous httpx request hook. |
| `event(...)` | Emit a manual canonical event. |
| `current_runtime_context()` | Read the resolved runtime identity. |
| `current_run_id()` | Read the current run ID. |
| `current_user_id()` | Read the current user ID. |
| `current_trace_id()` | Read the current trace ID. |
| `copy_context(...)` | Capture context for a later callable. |
| `submit_with_context(...)` | Submit work to an executor with current context. |
| `import_historical_traces(...)` | Normalize historical JSON into runtime events. |
| `TraceSourceConfiguration.validate(...)` | Validate a historical source configuration without retaining credentials. |

## Examples

| Example | What it demonstrates |
| --- | --- |
| [`openai_basic.py`](../examples/openai_basic.py) | One gateway request in an explicit run. |
| [`explicit_run.py`](../examples/explicit_run.py) | Manual events inside a stable workflow boundary. |
| [`async_agent.py`](../examples/async_agent.py) | Async run context and event emission. |
| [`parallel_agents.py`](../examples/parallel_agents.py) | Context propagation across parallel asyncio tasks. |
| [`opentelemetry_existing.py`](../examples/opentelemetry_existing.py) | Attaching PromptRail to an existing provider. |
| [`multi_step_agent.py`](../examples/multi_step_agent.py) | OpenAI, tools, branches, and workflow spans together. |

## Related documentation

- [Runtime event schema](runtime-event-schema.md)
- [Runtime SDK architecture](runtime-sdk-architecture.md)
- [Project README](../README.md)
