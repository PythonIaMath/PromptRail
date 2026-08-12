"""PromptRail Runtime SDK lifecycle and fail-open event observation."""

from __future__ import annotations

import threading
from collections import deque
from contextlib import suppress
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from .config import RuntimeConfig
from .context import (
    RuntimeContext,
    RuntimeLifecycleCallbacks,
    clear_runtime_context,
    ensure_implicit_run,
    set_lifecycle_callbacks,
    set_runtime_config,
)
from .context import (
    current_runtime_context as _context_runtime_context,
)
from .exporter import ExportWorker
from .privacy import PrivacyPolicy
from .tracing.canonical import EventType, PromptRailEvent
from .tracing.opentelemetry import (
    PromptRailSpanProcessor,
    current_trace_snapshot,
    ensure_current_trace_run,
    install_promptrail_span_processor,
)
from .utils.ids import secure_id
from .utils.logging import debug

try:
    SDK_VERSION = version("promptrail")
except PackageNotFoundError:  # pragma: no cover - editable source without metadata
    SDK_VERSION = "0.1.0"


class RuntimeClient:
    """Own the lightweight runtime observer, exporter, and trace adapter."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.privacy = PrivacyPolicy(
            mode=config.privacy_mode,
            max_depth=config.max_attribute_depth,
            max_items=config.max_attribute_items,
            max_string_length=config.max_string_length,
            max_total_attributes=config.max_total_attributes,
        )
        self._worker: ExportWorker | None = None
        self._span_processor: PromptRailSpanProcessor | None = None
        self._started_runs: set[str] = set()
        self._ended_runs: set[str] = set()
        self._ended_run_order: deque[str] = deque(maxlen=4096)
        self._run_contexts: dict[str, RuntimeContext] = {}
        self._runs_lock = threading.Lock()
        self._closed = False

    def start(self) -> None:
        clear_runtime_context()
        set_runtime_config(self.config)
        set_lifecycle_callbacks(
            RuntimeLifecycleCallbacks(
                on_run_start=self.observe_run_start,
                on_run_end=self.observe_run_end,
            )
        )
        if self.config.api_key and self.config.export_enabled:
            try:
                self._worker = ExportWorker(self.config)
                self._worker.start()
            except Exception as exc:
                self._log_failure("event exporter initialization", exc)
                self._worker = None
        if self.config.enable_opentelemetry:
            try:
                with suppress(Exception):
                    from opentelemetry import trace

                    provider = trace.get_tracer_provider()
                    if hasattr(provider, "add_span_processor"):
                        debug(
                            "existing OpenTelemetry provider detected",
                            enabled=self.config.debug,
                        )
                self._span_processor = install_promptrail_span_processor(
                    on_event=_dispatch_otel_event,
                    on_run_start=_dispatch_otel_run_start,
                    on_run_end=_dispatch_otel_run_end,
                )
            except Exception as exc:
                self._log_failure("OpenTelemetry initialization", exc)
        debug("PromptRail initialized", enabled=self.config.debug)
        if self._span_processor is not None:
            debug("span processor attached", enabled=self.config.debug)

    def shutdown(self, timeout: float | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        with self._runs_lock:
            open_runs = [
                context
                for run_id, context in self._run_contexts.items()
                if run_id not in self._ended_runs
            ]
        for context in open_runs:
            self.observe_run_end(context)
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                worker.shutdown(timeout)
            except Exception as exc:
                self._log_failure("event exporter shutdown", exc)
        set_lifecycle_callbacks()
        set_runtime_config(None)
        clear_runtime_context()

    def current_context(self, *, ensure_run: bool = False) -> RuntimeContext:
        """Merge contextvars and the current native OpenTelemetry context."""

        base = _context_runtime_context()
        snapshot = current_trace_snapshot()
        run_id = base.run_id or snapshot.run_id
        if ensure_run and not run_id:
            if snapshot.trace_id:
                run_id, created = ensure_current_trace_run(secure_id("run"), user_id=base.user_id)
                traced = replace(
                    base,
                    run_id=run_id,
                    trace_id=snapshot.trace_id,
                    span_id=snapshot.span_id,
                    parent_span_id=snapshot.parent_span_id,
                )
                if created:
                    self.observe_run_start(traced)
            else:
                base = ensure_implicit_run()
                run_id = base.run_id
                self.observe_run_start(base)
        return replace(
            base,
            run_id=run_id,
            trace_id=snapshot.trace_id or base.trace_id,
            span_id=snapshot.span_id or base.span_id,
            parent_span_id=snapshot.parent_span_id or base.parent_span_id,
        )

    def emit(
        self,
        type: EventType | str,
        *,
        context: RuntimeContext | None = None,
        name: str | None = None,
        status: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> PromptRailEvent | None:
        """Create and enqueue one canonical event without raising to callers."""

        try:
            runtime = context or self.current_context(ensure_run=True)
            if not runtime.run_id:
                return None
            normalized_attributes = dict(attributes or {})
            if self.config.application:
                normalized_attributes.setdefault("promptrail.application", self.config.application)
            if self.config.environment:
                normalized_attributes.setdefault("promptrail.environment", self.config.environment)
            normalized_attributes.setdefault("promptrail.sdk.version", SDK_VERSION)
            event = PromptRailEvent.from_context(
                type,
                context=runtime,
                name=name,
                status=status,
                attributes=normalized_attributes,
                privacy_policy=self.privacy,
            )
            if self._worker is not None:
                queued = self._worker.enqueue(event)
                if queued:
                    debug("event queued", enabled=self.config.debug)
                else:
                    debug("event dropped because queue is full", enabled=self.config.debug)
            return event
        except Exception as exc:
            self._log_failure("event observation", exc)
            return None

    def observe_run_start(self, context: RuntimeContext) -> None:
        if not context.run_id:
            return
        observed = self.current_context()
        if observed.run_id == context.run_id:
            context = _merge_context(context, observed)
        with self._runs_lock:
            if context.run_id in self._ended_runs:
                self._ended_runs.discard(context.run_id)
                with suppress(ValueError):
                    self._ended_run_order.remove(context.run_id)
            if context.run_id in self._started_runs:
                existing = self._run_contexts.get(context.run_id)
                if existing is not None:
                    self._run_contexts[context.run_id] = _merge_context(existing, context)
                return
            self._started_runs.add(context.run_id)
            self._run_contexts[context.run_id] = context
        self.emit(EventType.RUN_START, context=context, status="started")
        debug(f"run started: {context.run_id}", enabled=self.config.debug)

    def observe_run_end(self, context: RuntimeContext, error: BaseException | None = None) -> None:
        if not context.run_id:
            return
        observed = self.current_context()
        if observed.run_id == context.run_id:
            context = _merge_context(context, observed)
        with self._runs_lock:
            if context.run_id in self._ended_runs:
                return
            stored = self._run_contexts.get(context.run_id)
            if stored is not None:
                context = _merge_context(stored, context)
            if len(self._ended_run_order) == self._ended_run_order.maxlen:
                oldest = self._ended_run_order.popleft()
                self._ended_runs.discard(oldest)
            self._ended_runs.add(context.run_id)
            self._ended_run_order.append(context.run_id)
            self._started_runs.discard(context.run_id)
            self._run_contexts.pop(context.run_id, None)
        status = "error" if error else "success"
        attributes = {"error.type": type(error).__name__} if error else None
        self.emit(EventType.RUN_END, context=context, status=status, attributes=attributes)
        debug(f"run completed: {context.run_id}", enabled=self.config.debug)

    def observe_otel_run_start(self, run_id: str, trace_id: str) -> None:
        context = RuntimeContext(
            user_id=self._safe_user_id(),
            run_id=run_id if run_id.startswith("run_") else f"run_{run_id}",
            trace_id=trace_id,
        )
        self.observe_run_start(context)

    def observe_otel_run_end(self, run_id: str, trace_id: str) -> None:
        normalized = run_id if run_id.startswith("run_") else f"run_{run_id}"
        self.observe_run_end(RuntimeContext(run_id=normalized, trace_id=trace_id))

    def observe_otel_event(self, raw: dict[str, Any]) -> None:
        try:
            kind = str(raw.get("kind") or "other")
            phase = str(raw.get("phase") or "start")
            event_type = f"{kind}.{phase}"
            run_id = str(raw.get("run_id") or "")
            if run_id and not run_id.startswith("run_"):
                run_id = f"run_{run_id}"
            context = RuntimeContext(
                user_id=_optional_text(raw.get("user_id")) or self._safe_user_id(),
                run_id=run_id or secure_id("run"),
                trace_id=_optional_text(raw.get("trace_id")),
                span_id=_optional_text(raw.get("span_id")),
                parent_span_id=_optional_text(raw.get("parent_span_id")),
            )
            status = _normalize_otel_status(
                raw.get("status"), phase, has_error=raw.get("has_error") is True
            )
            raw_attributes = raw.get("attributes")
            attributes = dict(raw_attributes) if isinstance(raw_attributes, dict) else {}
            attributes["otel.span.kind"] = kind
            duration_ms = raw.get("duration_ms")
            if isinstance(duration_ms, int | float) and duration_ms >= 0:
                attributes["duration_ms"] = duration_ms
            self.emit(
                event_type,
                context=context,
                name=_optional_text(raw.get("name")),
                status=status,
                attributes=attributes,
            )
            if status == "error":
                self.emit(
                    EventType.ERROR,
                    context=context,
                    name=_optional_text(raw.get("name")),
                    status="error",
                    attributes={"source": "opentelemetry"},
                )
        except Exception as exc:
            self._log_failure("OpenTelemetry span processing", exc)

    def _safe_user_id(self) -> str | None:
        try:
            return self.current_context().user_id
        except Exception:
            return None

    def _log_failure(self, operation: str, exc: BaseException) -> None:
        debug(
            f"{operation} failed: {type(exc).__name__}",
            enabled=self.config.debug,
        )


_client_lock = threading.RLock()
_client: RuntimeClient | None = None


def initialize(config: RuntimeConfig) -> RuntimeClient:
    global _client
    with _client_lock:
        if _client is not None:
            _client.shutdown()
        _client = RuntimeClient(config)
        _client.start()
        return _client


def get_runtime_client() -> RuntimeClient | None:
    return _client


def _dispatch_otel_event(raw: dict[str, Any]) -> None:
    client = get_runtime_client()
    if client is not None and client.config.enable_opentelemetry:
        client.observe_otel_event(raw)


def _dispatch_otel_run_start(run_id: str, trace_id: str) -> None:
    client = get_runtime_client()
    if client is not None and client.config.enable_opentelemetry:
        client.observe_otel_run_start(run_id, trace_id)


def _dispatch_otel_run_end(run_id: str, trace_id: str) -> None:
    client = get_runtime_client()
    if client is not None and client.config.enable_opentelemetry:
        client.observe_otel_run_end(run_id, trace_id)


def shutdown(timeout: float | None = None) -> None:
    global _client
    with _client_lock:
        client, _client = _client, None
    if client is not None:
        client.shutdown(timeout)


def current_runtime_context(*, ensure_run: bool = False) -> RuntimeContext:
    client = get_runtime_client()
    if client is not None:
        return client.current_context(ensure_run=ensure_run)
    context = _context_runtime_context()
    return ensure_implicit_run() if ensure_run and not context.run_id else context


def current_run_id() -> str | None:
    return current_runtime_context().run_id


def current_user_id() -> str | None:
    return current_runtime_context().user_id


def current_trace_id() -> str | None:
    return current_runtime_context().trace_id


def emit_event(
    type: EventType | str,
    *,
    name: str | None = None,
    status: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> PromptRailEvent | None:
    client = get_runtime_client()
    if client is None:
        return None
    return client.emit(type, name=name, status=status, attributes=attributes)


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _merge_context(base: RuntimeContext, overlay: RuntimeContext) -> RuntimeContext:
    """Enrich lifecycle identity without discarding previously known fields."""

    return RuntimeContext(
        user_id=overlay.user_id or base.user_id,
        run_id=overlay.run_id or base.run_id,
        trace_id=overlay.trace_id or base.trace_id,
        span_id=overlay.span_id or base.span_id,
        parent_span_id=overlay.parent_span_id or base.parent_span_id,
    )


def _normalize_otel_status(value: Any, phase: str, *, has_error: bool = False) -> str:
    text = str(value or "").casefold()
    if has_error or "error" in text:
        return "error"
    return "started" if phase == "start" else "success"
