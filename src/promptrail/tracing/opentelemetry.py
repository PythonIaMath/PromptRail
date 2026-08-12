"""Optional OpenTelemetry bridge for PromptRail runtime tracing.

All OpenTelemetry imports are lazy. The module never performs network I/O and
fails open at integration boundaries so application code is not affected by
missing or incompatible tracing dependencies.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any
from weakref import WeakSet

from .classifier import classify_span

EventCallback = Callable[[dict[str, Any]], None]
RunCallback = Callable[[str, str], None]

_registered_providers: WeakSet[Any] = WeakSet()
_registered_provider_ids: set[int] = set()
_registry_lock = threading.RLock()
_trace_to_run: dict[str, str] = {}
_span_to_parent: dict[str, str | None] = {}
_span_to_trace: dict[str, str] = {}
_span_to_run: dict[str, str] = {}
_span_to_user: dict[str, str | None] = {}
_span_to_run_started: dict[str, bool] = {}


def _default_run_id() -> str:
    try:
        from promptrail.context import current_run_id

        active = current_run_id()
        if active:
            return active
    except Exception:
        pass
    return f"run_{uuid.uuid4().hex}"


def _format_trace_id(value: Any) -> str | None:
    try:
        integer = int(value)
    except Exception:
        return None
    if integer <= 0:
        return None
    return f"{integer:032x}"[-32:]


def _format_span_id(value: Any) -> str | None:
    try:
        integer = int(value)
    except Exception:
        return None
    if integer <= 0:
        return None
    return f"{integer:016x}"[-16:]


def _span_context(span: Any) -> Any:
    try:
        getter = getattr(span, "get_span_context", None)
        if callable(getter):
            return getter()
    except Exception:
        return None
    return getattr(span, "context", None)


def _parent_span_id(span: Any, parent_context: Any = None) -> str | None:
    parent = parent_context if parent_context is not None else getattr(span, "parent", None)
    if parent is None:
        return None
    if hasattr(parent, "span_id"):
        return _format_span_id(getattr(parent, "span_id", None))
    try:
        from opentelemetry import trace  # type: ignore

        current = trace.get_current_span(parent)
        context = _span_context(current)
        return _format_span_id(getattr(context, "span_id", None))
    except Exception:
        return None


def _event(
    span: Any,
    phase: str,
    trace_id: str,
    span_id: str,
    parent_span_id: str | None,
    run_id: str,
    user_id: str | None,
) -> dict[str, Any]:
    attributes = getattr(span, "attributes", None)
    if not isinstance(attributes, Mapping):
        attributes = {}
    status = getattr(span, "status", None)
    span_events = getattr(span, "events", ()) or ()
    has_error = any(getattr(item, "name", None) == "exception" for item in span_events)
    start_time = getattr(span, "start_time", None)
    end_time = getattr(span, "end_time", None)
    event = {
        "type": "otel.span." + phase,
        "phase": phase,
        "run_id": run_id,
        "user_id": user_id,
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": getattr(span, "name", None),
        "kind": classify_span(attributes, getattr(span, "name", None)),
        "status": str(status) if status is not None else None,
        "has_error": has_error,
        "attributes": dict(attributes),
    }
    if isinstance(start_time, int) and isinstance(end_time, int) and end_time >= start_time:
        event["duration_ms"] = (end_time - start_time) / 1_000_000
    return event


@dataclass(frozen=True, slots=True)
class TraceSnapshot:
    """Current OpenTelemetry context normalized for PromptRail metadata."""

    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    run_id: str | None = None


def current_trace_snapshot() -> TraceSnapshot:
    """Return the current trace/span/run IDs, or empty fields when OTel is absent."""

    try:
        from opentelemetry import trace  # type: ignore

        span = trace.get_current_span()
        context = _span_context(span)
        trace_id = _format_trace_id(getattr(context, "trace_id", None))
        span_id = _format_span_id(getattr(context, "span_id", None))
        with _registry_lock:
            parent_span_id = _span_to_parent.get(span_id) if span_id else None
            run_id = _trace_to_run.get(trace_id) if trace_id else None
        return TraceSnapshot(
            trace_id=trace_id, span_id=span_id, parent_span_id=parent_span_id, run_id=run_id
        )
    except Exception:
        return TraceSnapshot()


def inject_trace_headers(
    carrier: MutableMapping[str, str] | None = None,
) -> MutableMapping[str, str]:
    """Inject standard OTel propagation headers using the configured propagator."""

    headers: MutableMapping[str, str] = carrier if carrier is not None else {}
    try:
        from opentelemetry.propagate import inject  # type: ignore

        inject(headers)
    except Exception:
        pass
    return headers


class PromptRailSpanProcessor:
    """OpenTelemetry SDK span processor that emits PromptRail lifecycle events."""

    def __init__(
        self,
        *,
        on_event: EventCallback | None = None,
        on_run_start: RunCallback | None = None,
        on_run_end: RunCallback | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._on_event = on_event
        self._on_run_start = on_run_start
        self._on_run_end = on_run_end
        self._run_id_factory = run_id_factory or _default_run_id

    def on_start(self, span: Any, parent_context: Any | None = None) -> None:
        try:
            context = _span_context(span)
            trace_id = _format_trace_id(getattr(context, "trace_id", None))
            span_id = _format_span_id(getattr(context, "span_id", None))
            if not trace_id or not span_id:
                return
            parent_span_id = _parent_span_id(span, parent_context)
            root = parent_span_id is None
            active_run_id: str | None = None
            active_user_id: str | None = None
            try:
                from promptrail.context import current_run_id, current_user_id

                active_run_id = current_run_id()
                active_user_id = current_user_id()
            except Exception:
                pass
            with _registry_lock:
                run_id = _trace_to_run.get(trace_id)
                run_started = False
                if run_id is None and root:
                    run_id = self._run_id_factory()
                    _trace_to_run[trace_id] = run_id
                    run_started = True
                elif run_id is None:
                    run_id = self._run_id_factory()
                    _trace_to_run[trace_id] = run_id
                span_run_id = active_run_id or run_id
                _span_to_parent[span_id] = parent_span_id
                _span_to_trace[span_id] = trace_id
                _span_to_run[span_id] = span_run_id
                _span_to_user[span_id] = active_user_id
                _span_to_run_started[span_id] = run_started
            if run_started and self._on_run_start:
                self._on_run_start(run_id, trace_id)
            if self._on_event:
                self._on_event(
                    _event(
                        span,
                        "start",
                        trace_id,
                        span_id,
                        parent_span_id,
                        span_run_id,
                        active_user_id,
                    )
                )
        except Exception:
            return

    def on_end(self, span: Any) -> None:
        try:
            context = _span_context(span)
            trace_id = _format_trace_id(getattr(context, "trace_id", None))
            span_id = _format_span_id(getattr(context, "span_id", None))
            if not trace_id or not span_id:
                return
            with _registry_lock:
                run_id = _span_to_run.pop(span_id, None)
                run_id = run_id or _trace_to_run.get(trace_id) or self._run_id_factory()
                user_id = _span_to_user.pop(span_id, None)
                parent_span_id = _span_to_parent.get(span_id)
                run_started = _span_to_run_started.pop(span_id, False)
                _span_to_parent.pop(span_id, None)
                _span_to_trace.pop(span_id, None)
                if run_started:
                    _trace_to_run.pop(trace_id, None)
            if self._on_event:
                self._on_event(
                    _event(
                        span,
                        "end",
                        trace_id,
                        span_id,
                        parent_span_id,
                        run_id,
                        user_id,
                    )
                )
            if run_started and self._on_run_end:
                self._on_run_end(run_id, trace_id)
        except Exception:
            return

    def _on_ending(self, span: Any) -> None:
        """OpenTelemetry 1.44 pre-end hook. PromptRail has no synchronous work here."""

        return None

    def shutdown(self) -> None:  # SDK compatibility
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # SDK compatibility
        return True


def _is_proxy_provider(provider: Any) -> bool:
    return provider.__class__.__name__ in {"ProxyTracerProvider", "_ProxyTracerProvider"}


def install_promptrail_span_processor(
    *,
    on_event: EventCallback | None = None,
    on_run_start: RunCallback | None = None,
    on_run_end: RunCallback | None = None,
) -> PromptRailSpanProcessor | None:
    """Attach PromptRailSpanProcessor to the active provider without exporter changes."""

    try:
        from opentelemetry import trace  # type: ignore

        provider = trace.get_tracer_provider()
        if not hasattr(provider, "add_span_processor") and _is_proxy_provider(provider):
            try:
                from opentelemetry.sdk.trace import TracerProvider  # type: ignore

                provider = TracerProvider()
                trace.set_tracer_provider(provider)
            except Exception:
                return None
        if not hasattr(provider, "add_span_processor"):
            return None
        with _registry_lock:
            if provider in _registered_providers or id(provider) in _registered_provider_ids:
                return None
            processor = PromptRailSpanProcessor(
                on_event=on_event, on_run_start=on_run_start, on_run_end=on_run_end
            )
            provider.add_span_processor(processor)
            try:
                _registered_providers.add(provider)
            except TypeError:
                _registered_provider_ids.add(id(provider))
            return processor
    except Exception:
        return None


def get_tracer(name: str = "promptrail", version: str | None = None) -> Any | None:
    try:
        from opentelemetry import trace  # type: ignore

        return trace.get_tracer(name, version)
    except Exception:
        return None


def start_as_current_span(name: str, **kwargs: Any) -> Any:
    """Return an OTel current-span context manager, or a no-op context manager."""

    tracer = get_tracer()
    if tracer is not None:
        try:
            return tracer.start_as_current_span(name, **kwargs)
        except Exception:
            pass

    class _NoopSpan:
        def __enter__(self) -> None:
            return None

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

    return _NoopSpan()


__all__ = [
    "PromptRailSpanProcessor",
    "TraceSnapshot",
    "current_trace_snapshot",
    "get_tracer",
    "inject_trace_headers",
    "install_promptrail_span_processor",
    "start_as_current_span",
]
