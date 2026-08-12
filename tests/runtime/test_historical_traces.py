from __future__ import annotations

import json

import pytest

from promptrail import TraceSource, TraceSourceConfiguration, import_historical_traces
from promptrail.tracing import EventType


def test_trace_source_configuration_is_public_safe_and_never_retains_credentials() -> None:
    configuration = TraceSourceConfiguration.validate(
        source="langsmith",
        project=" production-agent ",
        credential="secret-read-only-key",
        metadata_only=True,
    )

    assert configuration.source is TraceSource.LANGSMITH
    assert configuration.project == "production-agent"
    assert "secret" not in repr(configuration)
    assert configuration.to_public_dict() == {
        "source": "langsmith",
        "project": "production-agent",
        "privacy_mode": "metadata_only",
        "sdk": {
            "trace_processor": "PromptRailSpanProcessor",
            "runtime_events_endpoint": "/v1/runtime/events",
            "schema_version": "1.0",
        },
    }


@pytest.mark.parametrize("source", ["langsmith", "langfuse", "braintrust", "helicone", "custom"])
def test_credential_sources_require_credentials(source: str) -> None:
    with pytest.raises(ValueError, match="credential"):
        TraceSourceConfiguration.validate(source=source, project="prod", credential="")


def test_import_jsonl_normalizes_to_canonical_sdk_events_and_enforces_privacy() -> None:
    payload = "\n".join(
        [
            json.dumps(
                {
                    "trace_id": "abc",
                    "span_id": "def",
                    "name": "openai chat",
                    "attributes": {
                        "gen_ai.system": "openai",
                        "gen_ai.request.model": "gpt-5",
                        "prompt": "must not leave the process",
                        "input_tokens": 120,
                    },
                }
            ),
            json.dumps(
                {
                    "trace_id": "abc",
                    "span_id": "123",
                    "name": "calculator",
                    "attributes": {"tool.name": "calculator"},
                }
            ),
        ]
    )

    result = import_historical_traces(payload)

    assert result.source_format == "jsonl"
    assert result.trace_count == 1
    assert result.run_count == 1
    assert result.llm_call_count == 1
    assert result.events[0].type == EventType.LLM_END.value
    assert result.events[0].trace_id == "00000000000000000000000000000abc"
    assert result.events[0].attributes["input_tokens"] == 120
    assert "prompt" not in result.events[0].attributes


def test_import_otlp_json_extracts_spans_and_timestamps() -> None:
    payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "1",
                                "spanId": "2",
                                "name": "chat completion",
                                "endTimeUnixNano": "1720000000000000000",
                                "attributes": [
                                    {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                                    {"key": "output_tokens", "value": {"intValue": "30"}},
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }

    result = import_historical_traces(payload)

    assert result.source_format == "opentelemetry"
    assert result.events[0].timestamp_ms == 1_720_000_000_000
    assert result.events[0].attributes["gen_ai.system"] == "openai"
