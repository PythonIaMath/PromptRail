"""Small public facade for the PromptRail Runtime SDK."""

from __future__ import annotations

import atexit
from collections.abc import Callable
from typing import Any

from .config import RuntimeConfig
from .context import run
from .integrations.openai import wrap_openai
from .propagation.headers import inject_headers
from .sdk import (
    SDK_VERSION,
    current_runtime_context,
    emit_event,
    initialize,
)
from .sdk import (
    shutdown as _shutdown,
)
from .tracing.canonical import EventType, PromptRailEvent


class PromptRail:
    """Process-level Runtime SDK lifecycle."""

    @classmethod
    def init(
        cls,
        *,
        api_key: str | None = None,
        application: str | None = None,
        environment: str | None = None,
        user_id: str | Callable[[], str | None] | None = None,
        capture_content: bool = False,
        privacy_mode: str | None = None,
        gateway_url: str = "https://api.promptrail.ai/v1",
        runtime_events_endpoint: str = "https://api.promptrail.ai/v1/runtime/events",
        export_enabled: bool = True,
        enable_opentelemetry: bool = True,
        queue_size: int = 2048,
        batch_size: int = 50,
        flush_interval: float = 0.25,
        shutdown_timeout: float = 5.0,
        request_timeout: float = 5.0,
        max_retries: int = 3,
        compression: bool = True,
        debug: bool = False,
    ) -> None:
        """Initialize runtime correlation, optional tracing, and event export."""

        mode = privacy_mode or ("content" if capture_content else "metadata_only")
        if privacy_mode is not None and capture_content and privacy_mode != "content":
            raise ValueError("capture_content=True conflicts with privacy_mode='metadata_only'")
        config = RuntimeConfig(
            api_key=api_key,
            application=application,
            environment=environment,
            user_id=user_id,
            privacy_mode=mode,  # type: ignore[arg-type]
            debug=debug,
            gateway_url=gateway_url,
            runtime_events_endpoint=runtime_events_endpoint,
            export_enabled=export_enabled,
            enable_opentelemetry=enable_opentelemetry,
            max_queue_size=queue_size,
            export_batch_size=batch_size,
            export_flush_interval=flush_interval,
            export_shutdown_timeout=shutdown_timeout,
            export_timeout=request_timeout,
            export_gzip=compression,
            max_export_retries=max_retries,
            sdk_version=SDK_VERSION,
        )
        initialize(config)

    @classmethod
    def shutdown(cls, timeout: float | None = None) -> None:
        """Flush queued telemetry up to the timeout and release resources."""

        _shutdown(timeout)

    @staticmethod
    def run(**kwargs: Any) -> Any:
        return run(**kwargs)

    @staticmethod
    def event(
        type: EventType | str,
        *,
        name: str | None = None,
        status: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> PromptRailEvent | None:
        return emit_event(type, name=name, status=status, attributes=attributes)

    @staticmethod
    def wrap_openai(client: Any) -> Any:
        return wrap_openai(client)

    @staticmethod
    def inject_headers(
        headers: dict[str, str] | None = None,
        *,
        url: object | None = None,
    ) -> dict[str, str]:
        return inject_headers(headers, url=url)

    @staticmethod
    def current_runtime_context() -> Any:
        return current_runtime_context()

    @staticmethod
    def current_run_id() -> str | None:
        return current_runtime_context().run_id

    @staticmethod
    def current_user_id() -> str | None:
        return current_runtime_context().user_id

    @staticmethod
    def current_trace_id() -> str | None:
        return current_runtime_context().trace_id


atexit.register(_shutdown)

__all__ = ["PromptRail"]
