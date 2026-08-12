from __future__ import annotations

import pytest

from promptrail.context import run
from promptrail.tracing.classifier import classify_span
from promptrail.tracing.opentelemetry import (
    PromptRailSpanProcessor,
    current_trace_snapshot,
    inject_trace_headers,
)


class _Context:
    def __init__(self, trace_id: int, span_id: int) -> None:
        self.trace_id = trace_id
        self.span_id = span_id


class _Span:
    def __init__(
        self, name: str, trace_id: int, span_id: int, parent=None, attributes=None
    ) -> None:
        self.name = name
        self.context = _Context(trace_id, span_id)
        self.parent = parent
        self.attributes = attributes or {}
        self.status = None


def test_classifier_prefers_semantic_attributes() -> None:
    assert classify_span({"gen_ai.system": "openai"}, "ambiguous") == "llm"
    assert classify_span({"tool.name": "calculator"}, "openai call") == "tool"
    assert classify_span({"retrieval.query": "hello"}, "chat") == "retrieval"
    assert classify_span({}, "completely unknown") == "other"


def test_span_processor_maps_root_trace_and_parent_ids() -> None:
    events = []
    starts = []
    ends = []
    processor = PromptRailSpanProcessor(
        on_event=events.append,
        on_run_start=lambda run_id, trace_id: starts.append((run_id, trace_id)),
        on_run_end=lambda run_id, trace_id: ends.append((run_id, trace_id)),
        run_id_factory=lambda: "run-fixed",
    )
    root = _Span("workflow", 0xABC, 0xDEF, attributes={"workflow.name": "demo"})
    child = _Span("chat", 0xABC, 0x123, parent=root.context, attributes={"gen_ai.system": "openai"})

    processor.on_start(root)
    processor.on_start(child)
    processor.on_end(child)
    processor.on_end(root)

    assert starts == [("run-fixed", "00000000000000000000000000000abc")]
    assert ends == [("run-fixed", "00000000000000000000000000000abc")]
    assert events[0]["trace_id"] == "00000000000000000000000000000abc"
    assert events[0]["span_id"] == "0000000000000def"
    assert events[1]["parent_span_id"] == "0000000000000def"
    assert events[1]["kind"] == "llm"


def test_descendant_inherits_run_from_active_parent_after_root_ends() -> None:
    events = []
    ends = []
    run_ids = iter(("run-root", "run-regenerated"))
    processor = PromptRailSpanProcessor(
        on_event=events.append,
        on_run_end=lambda run_id, trace_id: ends.append((run_id, trace_id)),
        run_id_factory=lambda: next(run_ids),
    )
    root = _Span("workflow", 0xABC, 0xDEF)
    child = _Span("agent", 0xABC, 0x123, parent=root.context)
    grandchild = _Span("tool", 0xABC, 0x456, parent=child.context)

    processor.on_start(root)
    processor.on_start(child)
    processor.on_end(root)
    assert ends == []
    processor.on_start(grandchild)
    processor.on_end(grandchild)
    processor.on_end(child)

    grandchild_start = next(event for event in events if event["span_id"] == "0000000000000456")
    assert grandchild_start["run_id"] == "run-root"
    assert grandchild_start["parent_span_id"] == "0000000000000123"
    assert ends == [("run-root", "00000000000000000000000000000abc")]


def test_explicit_run_id_owns_otel_root_lifecycle() -> None:
    starts = []
    ends = []
    events = []
    processor = PromptRailSpanProcessor(
        on_event=events.append,
        on_run_start=lambda run_id, trace_id: starts.append((run_id, trace_id)),
        on_run_end=lambda run_id, trace_id: ends.append((run_id, trace_id)),
        run_id_factory=lambda: "run-generated",
    )
    root = _Span("workflow", 0xFED, 0xCBA)

    with run(run_id="run-explicit"):
        processor.on_start(root)
        processor.on_end(root)

    assert starts == []
    assert ends == []
    assert {event["run_id"] for event in events} == {"run-explicit"}


def test_span_processor_fail_open_callbacks() -> None:
    processor = PromptRailSpanProcessor(
        on_event=lambda event: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    processor.on_start(_Span("tool", 1, 2, attributes={"tool.name": "x"}))
    processor.on_end(_Span("tool", 1, 2, attributes={"tool.name": "x"}))


def test_inject_trace_headers_without_otel_is_safe() -> None:
    headers = {"existing": "1"}
    assert inject_trace_headers(headers) is headers
    assert headers["existing"] == "1"


def test_current_trace_snapshot_without_active_span_is_safe() -> None:
    snapshot = current_trace_snapshot()
    assert snapshot.trace_id is None or len(snapshot.trace_id) == 32
    assert snapshot.span_id is None or len(snapshot.span_id) == 16


def test_install_with_real_sdk_if_available(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    from promptrail.tracing.opentelemetry import install_promptrail_span_processor

    provider = TracerProvider()
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    processor = install_promptrail_span_processor()
    assert processor is not None
    assert install_promptrail_span_processor() is None
