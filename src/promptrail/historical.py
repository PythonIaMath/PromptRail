"""Historical trace ingestion contracts shared by the SDK and control plane."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .privacy import PrivacyPolicy
from .tracing.canonical import EventType, PromptRailEvent, normalize_event_type
from .tracing.classifier import classify_span
from .utils.ids import secure_id
from .utils.time import epoch_ms

MAX_HISTORICAL_IMPORT_BYTES = 50 * 1024 * 1024


class TraceSource(StrEnum):
    LANGSMITH = "langsmith"
    LANGFUSE = "langfuse"
    BRAINTRUST = "braintrust"
    HELICONE = "helicone"
    OPENTELEMETRY = "opentelemetry"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class TraceSourceConfiguration:
    source: TraceSource
    project: str
    metadata_only: bool = True

    @classmethod
    def validate(
        cls,
        *,
        source: str,
        project: str,
        credential: str | None,
        metadata_only: bool = True,
    ) -> TraceSourceConfiguration:
        try:
            parsed_source = TraceSource(source.strip().lower())
        except (AttributeError, ValueError) as exc:
            raise ValueError("unsupported trace source") from exc
        if not isinstance(project, str) or not project.strip():
            raise ValueError("project is required")
        if parsed_source is not TraceSource.OPENTELEMETRY and (
            not isinstance(credential, str) or not credential.strip()
        ):
            raise ValueError("a non-empty credential is required")
        return cls(source=parsed_source, project=project.strip()[:256], metadata_only=metadata_only)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.value,
            "project": self.project,
            "privacy_mode": "metadata_only" if self.metadata_only else "content",
            "sdk": {
                "trace_processor": "PromptRailSpanProcessor",
                "runtime_events_endpoint": "/v1/runtime/events",
                "schema_version": "1.0",
            },
        }


@dataclass(frozen=True, slots=True)
class HistoricalImportResult:
    events: tuple[PromptRailEvent, ...]
    source_format: str
    trace_count: int
    run_count: int
    llm_call_count: int

    def summary(self) -> dict[str, Any]:
        return {
            "accepted_events": len(self.events),
            "source_format": self.source_format,
            "trace_count": self.trace_count,
            "run_count": self.run_count,
            "llm_call_count": self.llm_call_count,
            "schema_version": "1.0",
        }


def import_historical_traces(
    data: bytes | str | Mapping[str, Any] | Iterable[Mapping[str, Any]],
    *,
    metadata_only: bool = True,
) -> HistoricalImportResult:
    """Normalize PromptRail, JSONL, generic span, or OTLP JSON into runtime events."""

    raw = _decode_input(data)
    records, source_format = _extract_records(raw)
    privacy = PrivacyPolicy(mode="metadata_only" if metadata_only else "content")
    events = tuple(_to_event(record, privacy=privacy) for record in records)
    if not events:
        raise ValueError("trace import contains no events or spans")
    return HistoricalImportResult(
        events=events,
        source_format=source_format,
        trace_count=len({event.trace_id for event in events if event.trace_id}),
        run_count=len({event.run_id for event in events}),
        llm_call_count=sum(
            normalize_event_type(event.type) == EventType.LLM_END for event in events
        ),
    )


def _decode_input(
    data: bytes | str | Mapping[str, Any] | Iterable[Mapping[str, Any]],
) -> Any:
    if isinstance(data, bytes):
        if len(data) > MAX_HISTORICAL_IMPORT_BYTES:
            raise ValueError("trace import exceeds 50 MB")
        text = data.decode("utf-8")
    elif isinstance(data, str):
        if len(data.encode("utf-8")) > MAX_HISTORICAL_IMPORT_BYTES:
            raise ValueError("trace import exceeds 50 MB")
        text = data
    else:
        return data
    stripped = text.strip()
    if not stripped:
        raise ValueError("trace import is empty")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    records = []
    for line_number, line in enumerate(stripped.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}") from exc
        if not isinstance(value, Mapping):
            raise ValueError(f"JSONL line {line_number} must be an object")
        records.append(value)
    return records


def _extract_records(raw: Any) -> tuple[list[Mapping[str, Any]], str]:
    if isinstance(raw, Mapping):
        if isinstance(raw.get("events"), list):
            return _mapping_records(raw["events"]), "promptrail"
        if isinstance(raw.get("resourceSpans"), list):
            return _otlp_records(raw["resourceSpans"]), "opentelemetry"
        if isinstance(raw.get("spans"), list):
            return _mapping_records(raw["spans"]), "generic-spans"
        if raw.get("trace_id") or raw.get("traceId") or raw.get("run_id"):
            return [raw], "generic-event"
        raise ValueError("unsupported trace JSON structure")
    if isinstance(raw, Iterable) and not isinstance(raw, str | bytes):
        return _mapping_records(raw), "jsonl"
    raise ValueError("trace import must be a JSON object, array, or JSONL objects")


def _mapping_records(values: Iterable[Any]) -> list[Mapping[str, Any]]:
    records = [value for value in values if isinstance(value, Mapping)]
    if not records:
        raise ValueError("trace collection contains no objects")
    return records


def _otlp_records(resource_spans: Iterable[Any]) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for resource in resource_spans:
        if not isinstance(resource, Mapping):
            continue
        scopes = resource.get("scopeSpans") or resource.get("instrumentationLibrarySpans") or []
        for scope in scopes if isinstance(scopes, list) else []:
            if not isinstance(scope, Mapping):
                continue
            spans = scope.get("spans") or []
            records.extend(item for item in spans if isinstance(item, Mapping))
    if not records:
        raise ValueError("OpenTelemetry payload contains no spans")
    return records


def _to_event(record: Mapping[str, Any], *, privacy: PrivacyPolicy) -> PromptRailEvent:
    attributes = _attributes(record.get("attributes"))
    name = _string(record.get("name"))
    raw_type = record.get("type")
    if isinstance(raw_type, str) and raw_type.strip():
        event_type = normalize_event_type(raw_type)
    else:
        kind = classify_span(attributes, name)
        event_type = f"{kind}.end" if kind != "other" else EventType.OTHER_END.value
    trace_id = _identifier(record.get("trace_id") or record.get("traceId"), 32)
    span_id = _identifier(record.get("span_id") or record.get("spanId"), 16)
    parent_span_id = _identifier(
        record.get("parent_span_id") or record.get("parentSpanId"), 16
    )
    run_id = _string(record.get("run_id") or record.get("runId")) or (
        f"run_{trace_id}" if trace_id else secure_id("run")
    )
    timestamp = record.get("timestamp_ms") or record.get("timestampMs")
    if timestamp is None and record.get("endTimeUnixNano") is not None:
        try:
            timestamp = int(str(record["endTimeUnixNano"])) // 1_000_000
        except (TypeError, ValueError):
            timestamp = None
    return PromptRailEvent(
        type=event_type,
        run_id=run_id[:256],
        user_id=_string(record.get("user_id") or record.get("userId")),
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=name,
        status=_status(record.get("status")),
        attributes=privacy.sanitize(attributes),
        event_id=_string(record.get("event_id") or record.get("eventId")) or secure_id("evt"),
        timestamp_ms=_positive_int(timestamp) or epoch_ms(),
    )


def _attributes(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        output: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, Mapping) or not isinstance(item.get("key"), str):
                continue
            wrapped = item.get("value")
            if isinstance(wrapped, Mapping) and len(wrapped) == 1:
                output[item["key"]] = next(iter(wrapped.values()))
            else:
                output[item["key"]] = wrapped
        return output
    return {}


def _identifier(value: Any, width: int) -> str | None:
    text = _string(value)
    if not text:
        return None
    normalized = text.lower().removeprefix("0x")
    return normalized.zfill(width)[-width:]


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _status(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip()[:64] or None
    if isinstance(value, Mapping):
        candidate = value.get("message") or value.get("code")
        return str(candidate)[:64] if candidate is not None else None
    return None


__all__ = [
    "MAX_HISTORICAL_IMPORT_BYTES",
    "HistoricalImportResult",
    "TraceSource",
    "TraceSourceConfiguration",
    "import_historical_traces",
]
