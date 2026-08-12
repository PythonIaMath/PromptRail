from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping


class SerializationError(Exception):
    """Sanitized serialization failure."""


def _event_to_mapping(event: Any) -> Mapping[str, Any]:
    if hasattr(event, "to_dict") and callable(event.to_dict):
        value = event.to_dict()
    elif hasattr(event, "model_dump") and callable(event.model_dump):
        value = event.model_dump(mode="json")
    elif is_dataclass(event):
        value = asdict(event)
    elif isinstance(event, Mapping):
        value = event
    else:
        raise SerializationError(f"event type {type(event).__name__} is not serializable")
    if not isinstance(value, Mapping):
        raise SerializationError("event serializer did not return a mapping")
    return value


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unsupported value type {type(value).__name__}")


class BatchJSONEncoder:
    """Encodes event batches to compact JSON bytes."""

    content_type = "application/json"

    def encode(self, events: Iterable[Any]) -> bytes:
        try:
            payload = {"events": [_event_to_mapping(event) for event in events]}
            return json.dumps(payload, default=_default, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except SerializationError:
            raise
        except Exception as exc:
            raise SerializationError(f"failed to serialize runtime event batch: {type(exc).__name__}") from None


__all__ = ["BatchJSONEncoder", "SerializationError"]
