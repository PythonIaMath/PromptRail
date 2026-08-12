"""Runtime event privacy controls."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

Scalar = str | int | float | bool | None
_CONTENT_KEY_PARTS = (
    "prompt",
    "completion",
    "message",
    "messages",
    "body",
    "document",
    "documents",
    "content",
    "input",
    "output",
    "source",
    "code",
    "tool_input",
    "tool_output",
    "response",
)
_SAFE_METADATA_SUFFIXES = (
    "_chars",
    "_count",
    "_duration_ms",
    "_hash",
    "_id",
    "_length",
    "_mime_type",
    "_model",
    "_name",
    "_size",
    "_size_bytes",
    "_status",
    "_tokens",
    "_type",
)


@dataclass(frozen=True, slots=True)
class PrivacyPolicy:
    mode: Literal["metadata_only", "content"] = "metadata_only"
    max_depth: int = 4
    max_items: int = 50
    max_string_length: int = 2048
    max_total_attributes: int = 200

    def __post_init__(self) -> None:
        if self.mode not in {"metadata_only", "content"}:
            raise ValueError("mode must be 'metadata_only' or 'content'")
        for name in ("max_depth", "max_items", "max_string_length", "max_total_attributes"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")

    def sanitize(self, attributes: Mapping[str, Any] | None) -> dict[str, Any]:
        if not attributes:
            return {}
        budget = _Budget(self.max_total_attributes)
        sanitized = self._sanitize_mapping(attributes, depth=0, budget=budget)
        return sanitized if isinstance(sanitized, dict) else {}

    def _allows_key(self, key: str) -> bool:
        if self.mode == "content":
            return True
        normalized = key.lower().replace("-", "_").replace(".", "_")
        if not any(part in normalized for part in _CONTENT_KEY_PARTS):
            return True
        return normalized.endswith(_SAFE_METADATA_SUFFIXES)

    def _sanitize_mapping(
        self, value: Mapping[str, Any], depth: int, budget: _Budget
    ) -> dict[str, Any]:
        if depth >= self.max_depth or not budget.take():
            return {}
        output: dict[str, Any] = {}
        for raw_key, raw_item in value.items():
            if len(output) >= self.max_items:
                break
            if not isinstance(raw_key, str):
                continue
            if not self._allows_key(raw_key):
                continue
            if not budget.take():
                break
            item = self._sanitize_value(raw_item, depth + 1, budget)
            if item is not _DROP:
                output[raw_key] = item
        return output

    def _sanitize_sequence(self, value: Sequence[Any], depth: int, budget: _Budget) -> list[Any]:
        if isinstance(value, str | bytes | bytearray) or depth >= self.max_depth:
            return []
        output: list[Any] = []
        for item in list(value)[: self.max_items]:
            if not budget.take():
                break
            sanitized = self._sanitize_value(item, depth + 1, budget)
            if sanitized is not _DROP:
                output.append(sanitized)
        return output

    def _sanitize_value(self, value: Any, depth: int, budget: _Budget) -> Any:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else _DROP
        if isinstance(value, str):
            return value[: self.max_string_length]
        if isinstance(value, Mapping):
            return self._sanitize_mapping(value, depth, budget)
        if isinstance(value, list | tuple):
            return self._sanitize_sequence(value, depth, budget)
        return _DROP


@dataclass(slots=True)
class _Budget:
    remaining: int

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


class _Drop:
    pass


_DROP = _Drop()
