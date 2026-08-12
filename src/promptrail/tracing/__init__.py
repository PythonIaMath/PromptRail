"""Runtime tracing primitives."""

from .base import inject_headers
from .canonical import SCHEMA_VERSION, EventType, PromptRailEvent, event, normalize_event_type

__all__ = [
    "SCHEMA_VERSION",
    "EventType",
    "PromptRailEvent",
    "event",
    "inject_headers",
    "normalize_event_type",
]
