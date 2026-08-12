"""Canonical runtime event model."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from promptrail.context import RuntimeContext, current_runtime_context
from promptrail.privacy import PrivacyPolicy
from promptrail.utils.ids import secure_id
from promptrail.utils.time import epoch_ms

SCHEMA_VERSION = "1.0"


class EventType(StrEnum):
    RUN_START = "run.start"
    RUN_END = "run.end"
    AGENT_START = "agent.start"
    AGENT_END = "agent.end"
    TOOL_START = "tool.start"
    TOOL_END = "tool.end"
    RETRIEVAL_START = "retrieval.start"
    RETRIEVAL_END = "retrieval.end"
    LLM_START = "llm.start"
    LLM_END = "llm.end"
    BRANCH_START = "branch.start"
    BRANCH_END = "branch.end"
    ERROR = "error"
    OTHER_START = "other.start"
    OTHER_END = "other.end"
    WORKFLOW_START = "workflow.start"
    WORKFLOW_END = "workflow.end"


def normalize_event_type(value: EventType | str) -> str:
    if isinstance(value, EventType):
        return value.value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("event type must be a non-empty string or EventType")
    return value.strip()[:128]


@dataclass(frozen=True, slots=True)
class PromptRailEvent:
    type: EventType | str
    run_id: str
    user_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    name: str | None = None
    status: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: secure_id("evt"))
    timestamp_ms: int = field(default_factory=epoch_ms)
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_context(
        cls,
        type: EventType | str,
        *,
        context: RuntimeContext | None = None,
        name: str | None = None,
        status: str | None = None,
        attributes: dict[str, Any] | None = None,
        privacy_policy: PrivacyPolicy | None = None,
    ) -> PromptRailEvent:
        runtime_context = context or current_runtime_context()
        if not runtime_context.run_id:
            raise ValueError("run_id is required for runtime events")
        policy = privacy_policy or PrivacyPolicy()
        return cls(
            type=type,
            run_id=runtime_context.run_id,
            user_id=runtime_context.user_id,
            trace_id=runtime_context.trace_id,
            span_id=runtime_context.span_id,
            parent_span_id=runtime_context.parent_span_id,
            name=name,
            status=status,
            attributes=policy.sanitize(attributes),
        )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "user_id": self.user_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "type": normalize_event_type(self.type),
            "name": self.name[:512] if self.name else None,
            "timestamp_ms": self.timestamp_ms,
            "status": self.status[:64] if self.status else None,
            "attributes": dict(self.attributes),
        }


def event(
    type: EventType | str,
    *,
    name: str | None = None,
    status: str | None = None,
    attributes: dict[str, Any] | None = None,
    context: RuntimeContext | None = None,
    privacy_policy: PrivacyPolicy | None = None,
) -> PromptRailEvent:
    return PromptRailEvent.from_context(
        type,
        context=context,
        name=name,
        status=status,
        attributes=attributes,
        privacy_policy=privacy_policy,
    )
