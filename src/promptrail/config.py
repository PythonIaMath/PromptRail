"""Runtime SDK configuration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

UserResolver = Callable[[], str | None]
PrivacyMode = Literal["metadata_only", "content"]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    api_key: str | None = None
    application: str | None = None
    environment: str | None = None
    user_id: str | UserResolver | None = None
    privacy_mode: PrivacyMode = "metadata_only"
    debug: bool = False
    gateway_url: str = "https://api.promptrail.ai/v1"
    runtime_events_endpoint: str = "https://api.promptrail.ai/v1/runtime/events"
    export_enabled: bool = True
    enable_opentelemetry: bool = True
    max_queue_size: int = 2048
    export_batch_size: int = 50
    export_flush_interval: float = 0.25
    export_shutdown_timeout: float = 5.0
    export_timeout: float = 5.0
    export_gzip: bool = True
    max_export_retries: int = 3
    sdk_version: str = "0.1.0"
    max_attribute_depth: int = 4
    max_attribute_items: int = 50
    max_string_length: int = 2048
    max_total_attributes: int = 200

    def __post_init__(self) -> None:
        if self.api_key is not None and not self.api_key.strip():
            raise ValueError("api_key must be non-empty when provided")
        for field_name in ("application", "environment"):
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise ValueError(f"{field_name} must be non-empty when provided")
        if isinstance(self.user_id, str) and not self.user_id.strip():
            raise ValueError("user_id must be non-empty when provided")
        if (
            self.user_id is not None
            and not isinstance(self.user_id, str)
            and not callable(self.user_id)
        ):
            raise TypeError("user_id must be a string, callable, or None")
        if self.privacy_mode not in {"metadata_only", "content"}:
            raise ValueError("privacy_mode must be 'metadata_only' or 'content'")
        for field_name in ("gateway_url", "runtime_events_endpoint"):
            parsed = urlsplit(getattr(self, field_name))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
            if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError(f"{field_name} must use HTTPS outside localhost")
        for field_name in (
            "max_attribute_depth",
            "max_attribute_items",
            "max_string_length",
            "max_total_attributes",
        ):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be >= 1")
        for field_name in (
            "max_queue_size",
            "export_batch_size",
            "max_export_retries",
        ):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be >= 1")
        for field_name in (
            "export_flush_interval",
            "export_shutdown_timeout",
            "export_timeout",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be > 0")

    @property
    def endpoint(self) -> str:
        """Compatibility alias used by the isolated HTTP exporter."""

        return self.runtime_events_endpoint

    def resolve_user_id(self) -> str | None:
        if callable(self.user_id):
            value = self.user_id()
            return value if isinstance(value, str) and value else None
        return self.user_id
