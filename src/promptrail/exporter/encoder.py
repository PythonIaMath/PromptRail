from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


class SerializationError(Exception):
    """Sanitized serialization failure."""


def _event_to_mapping(event: Any) -> Mapping[str, Any]:
    if hasattr(event, "to_json_dict") and callable(event.to_json_dict):
        value = event.to_json_dict()
    elif hasattr(event, "to_dict") and callable(event.to_dict):
        value = event.to_dict()
    elif isinstance(event, Mapping):
        value = event
    else:
        raise SerializationError(f"event type {type(event).__name__} is not serializable")
    if not isinstance(value, Mapping):
        raise SerializationError("event serializer did not return a mapping")
    return value


class BatchJSONEncoder:
    """Encodes event batches to compact JSON bytes."""

    content_type = "application/json"

    def encode(self, events: Iterable[Any]) -> bytes:
        try:
            payload = {"events": [_event_to_mapping(event) for event in events]}
            return json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except SerializationError:
            raise
        except Exception as exc:
            raise SerializationError(
                f"failed to serialize runtime event batch: {type(exc).__name__}"
            ) from None


__all__ = ["BatchJSONEncoder", "SerializationError"]
