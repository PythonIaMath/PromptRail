# PromptRail Runtime Event Schema 1.0

Runtime events use a deliberately small, versioned JSON schema. They describe current execution structure and metadata, not historical analytics or raw application payloads.

## Envelope

The runtime endpoint accepts:

```json
{
  "events": [
    {
      "schema_version": "1.0",
      "event_id": "evt_01J...",
      "run_id": "run_01J...",
      "user_id": "tenant_3:user_81",
      "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
      "span_id": "00f067aa0ba902b7",
      "parent_span_id": "b7ad6b7169203331",
      "type": "tool.end",
      "name": "search_repository",
      "timestamp_ms": 1786550400000,
      "status": "success",
      "attributes": {
        "tool.name": "search_repository",
        "duration_ms": 921,
        "input_size_bytes": 310,
        "output_size_bytes": 18431,
        "promptrail.application": "coding-agent",
        "promptrail.environment": "production"
      }
    }
  ]
}
```

## Fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `schema_version` | string | yes | Event contract version. MVP value is `1.0`. |
| `event_id` | string | yes | Unique event ID generated locally. |
| `run_id` | string | yes | Complete end-user request or agent execution. |
| `user_id` | string or null | yes | Stable enterprise-provided identifier, not required to be PII. |
| `trace_id` | string or null | yes | Lowercase 32-character OpenTelemetry trace ID when available. |
| `span_id` | string or null | yes | Lowercase 16-character current span ID when available. |
| `parent_span_id` | string or null | yes | Lowercase 16-character direct parent span ID when available. |
| `type` | string | yes | Canonical lifecycle type or manual escape-hatch type. |
| `name` | string or null | yes | Bounded operation name. |
| `timestamp_ms` | integer | yes | Unix epoch milliseconds. |
| `status` | string or null | yes | Normalized status such as `success`, `error`, `unset`, or `cancelled`. |
| `attributes` | object | yes | Sanitized, bounded scalar/list/map metadata. |

## Initial canonical event types

```text
run.start
run.end
agent.start
agent.end
tool.start
tool.end
retrieval.start
retrieval.end
llm.start
llm.end
branch.start
branch.end
error
```

OpenTelemetry spans that cannot be classified safely use `other.start` and `other.end`. A generic root workflow span uses `workflow.start` and `workflow.end`. The manual event API may send namespaced strings such as `workflow.stage`.

## Span classification

Classification precedence is:

1. Explicit PromptRail attributes such as `promptrail.span.type`.
2. OpenTelemetry semantic attributes including `gen_ai.*`, `tool.*`, `retrieval.*`, and workflow/agent attributes.
3. Standard span kind and bounded, conservative name hints.
4. `other` when uncertain.

String matching never overrides contradictory explicit or semantic metadata.

## Metadata-only privacy mode

`metadata_only` is the default. The sanitizer:

- drops known prompt, completion, message, body, document, tool input/output, source-code, and content keys
- retains scalar operational metadata, identifiers, sizes, counts, durations, status, model names, provider names, and workflow stages
- truncates strings and collections to configured bounds
- converts no arbitrary objects through `repr`, pickling, dataclass traversal, or model serialization

`content` mode permits explicitly supplied string/list/map content subject to the same structural limits. It does not alter what the PromptRail gateway may process for the live inference request.

## Gateway correlation headers

The SDK sends the current identity synchronously with a PromptRail gateway request:

```http
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
x-promptrail-run-id: run_01J...
x-promptrail-user-id: tenant_3:user_81
x-promptrail-trace-id: 4bf92f3577b34da6a3ce929d0e0e4736
x-promptrail-span-id: 00f067aa0ba902b7
x-promptrail-parent-span-id: b7ad6b7169203331
x-promptrail-application: coding-agent
x-promptrail-environment: production
x-promptrail-schema-version: 1.0
x-promptrail-sdk-version: 0.1.0
```

Optional values are omitted. The gateway must strip private `x-promptrail-*` metadata before forwarding to model providers.

## Compatibility

New optional fields may be added within schema 1.x. A breaking rename, meaning change, or required-field change requires a new major schema version. The server should retain SDK-version-aware ingestion and ignore unknown attributes.
