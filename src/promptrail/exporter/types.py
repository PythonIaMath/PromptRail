from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RuntimeConfig(Protocol):
    api_key: str | None
    endpoint: str
    max_queue_size: int
    export_batch_size: int
    export_flush_interval: float
    export_shutdown_timeout: float
    export_gzip: bool
    export_timeout: float
    sdk_version: str


@runtime_checkable
class PromptRailEvent(Protocol):
    def to_dict(self) -> Mapping[str, Any]: ...


class Sender(Protocol):
    def send(self, body: bytes, headers: Mapping[str, str]) -> Any: ...
    def close(self) -> None: ...
