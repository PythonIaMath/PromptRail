# PromptRail Runtime Event Schema

PromptRail runtime events use schema version `1.0`. Live SDK observation and historical trace imports produce the same canonical event structure.

## Batch request

The runtime exporter sends events as a JSON object:

```json
{
  "events": [
    {
      "schema_version": "1.0",
      "event_id": "evt_01...",
      "run_id": "run_01...",
      "user_id": "customer_123",
      "trace_id": "0123456789abcdef0123456789abcdef",
      "span_id": "0123456789abcdef",
      "parent_span_id": null,
      "type": "llm.end",
      "name": "responses.create",
      "timestamp_ms": 1720000000000,
      "status": "success",
      "attributes": {
        "gen_ai.system": "openai",
        "gen_ai.request.model": "gpt-4o-mini",
        "input_tokens": 420,
        "output_tokens": 88,
        "duration_ms": 730
      }
    }
  ]
}
```

Default ingestion endpoint:

```http
POST /v1/runtime/events
Content-Type: application/json
Authorization: Bearer <PROMPTRAIL_API_KEY>
Content-Encoding: gzip
```

`Content-Encoding` is present only when compression is enabled and useful for the payload size.

## Event fields

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | string | yes | Currently `1.0`. |
| `event_id` | string | yes | Unique event identifier used for backend deduplication. |
| `run_id` | string | yes | PromptRail execution boundary. |
| `user_id` | string or null | no | Stable application user or tenant identity. |
| `trace_id` | string or null | no | 32-character lowercase OpenTelemetry trace ID when available. |
| `span_id` | string or null | no | 16-character lowercase OpenTelemetry span ID when available. |
| `parent_span_id` | string or null | no | Parent span ID when available. |
| `type` | string | yes | Canonical type or a custom non-empty type, maximum 128 characters. |
| `name` | string or null | no | Operation name, maximum 512 characters. |
| `timestamp_ms` | integer | yes | Unix epoch timestamp in milliseconds. |
| `status` | string or null | no | Status text, maximum 64 characters. |
| `attributes` | object | yes | Privacy-filtered operational metadata. |

## Canonical event types

| Type | Meaning |
| --- | --- |
| `run.start` | A PromptRail run began. |
| `run.end` | A PromptRail run ended. |
| `agent.start` | An agent operation began. |
| `agent.end` | An agent operation ended. |
| `tool.start` | A tool call began. |
| `tool.end` | A tool call ended. |
| `retrieval.start` | Retrieval began. |
| `retrieval.end` | Retrieval ended. |
| `llm.start` | An LLM operation began. |
| `llm.end` | An LLM operation ended. |
| `branch.start` | A branch or route began. |
| `branch.end` | A branch or route ended. |
| `workflow.start` | A workflow stage began. |
| `workflow.end` | A workflow stage ended. |
| `other.start` | An unclassified operation began. |
| `other.end` | An unclassified operation ended. |
| `error` | An error occurred outside a paired end event. |

Custom event strings are accepted when they are non-empty. Use canonical types where possible so analysis can group equivalent work across frameworks.

## Attribute conventions

PromptRail classifies spans from stable semantic attributes before checking span names.

### LLM

```json
{
  "gen_ai.system": "openai",
  "gen_ai.operation.name": "chat",
  "gen_ai.request.model": "gpt-4o-mini",
  "input_tokens": 420,
  "output_tokens": 88,
  "duration_ms": 730
}
```

### Tool

```json
{
  "promptrail.span.type": "tool",
  "tool.name": "search_repository",
  "tool.call.id": "call_123",
  "output_size_bytes": 2048,
  "duration_ms": 51
}
```

### Retrieval

```json
{
  "promptrail.span.type": "retrieval",
  "db.system": "pinecone",
  "vector.top_k": 10,
  "document_count": 8,
  "duration_ms": 94
}
```

### Agent, workflow, and branch

```json
{
  "agent.name": "planner",
  "workflow.name": "answer-ticket",
  "branch.name": "billing-route"
}
```

## Privacy rules

`metadata_only` is the default SDK policy. It removes values associated with:

- prompts and completions
- messages and response content
- documents and source code
- request and response bodies
- tool input and tool output

Operational suffixes remain allowed when they describe metadata rather than content. Examples include `_id`, `_name`, `_model`, `_tokens`, `_count`, `_duration_ms`, `_size_bytes`, `_hash`, and `_status`.

Default attribute bounds:

| Bound | Value |
| --- | ---: |
| Maximum nested depth | 4 |
| Maximum items per mapping or sequence | 50 |
| Maximum string length | 2,048 characters |
| Maximum total attributes | 200 |

Unsupported objects and non-finite floating-point values are dropped.

## Historical input mapping

`import_historical_traces(...)` also accepts common historical structures.

### PromptRail batch

```json
{"events": [{"run_id": "run_1", "type": "llm.end"}]}
```

### Generic spans

```json
{
  "spans": [
    {
      "trace_id": "abc",
      "span_id": "def",
      "name": "openai chat",
      "attributes": {"gen_ai.system": "openai"}
    }
  ]
}
```

### OpenTelemetry JSON

The importer reads spans from `resourceSpans[].scopeSpans[].spans[]`. It also accepts the older `instrumentationLibrarySpans` key.

OpenTelemetry identifiers may use `traceId`, `spanId`, and `parentSpanId`. Nanosecond `endTimeUnixNano` values are converted to `timestamp_ms`.

### JSONL

Each non-empty line must contain one JSON object:

```jsonl
{"trace_id":"abc","span_id":"def","name":"openai chat","attributes":{"gen_ai.system":"openai"}}
{"trace_id":"abc","span_id":"123","name":"search","attributes":{"tool.name":"search"}}
```

## Validation behavior

The historical importer raises `ValueError` for:

- empty input
- inputs larger than 50 MB when passed as `bytes` or `str`
- malformed JSON or JSONL
- JSONL lines that are not objects
- unsupported top-level structures
- event or span collections without objects
- OpenTelemetry payloads without spans

## Versioning

Consumers should read `schema_version` and tolerate unknown optional attributes. A future breaking wire-format change will use a new schema version rather than silently changing `1.0` semantics.

## Related documentation

- [Python SDK guide](runtime-sdk.md)
- [Runtime SDK architecture](runtime-sdk-architecture.md)
