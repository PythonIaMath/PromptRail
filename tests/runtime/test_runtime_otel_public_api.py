from __future__ import annotations

from typing import Any

import pytest

from promptrail import PromptRail, current_runtime_context, run, wrap_openai

GATEWAY = "https://gateway.promptrail.test/v1"


@pytest.fixture(autouse=True)
def clean_runtime() -> None:
    PromptRail.shutdown(timeout=0)
    yield
    PromptRail.shutdown(timeout=0)


class CapturingProcessor:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def on_start(self, span: Any, parent_context: Any | None = None) -> None:
        self.events.append(("start", span.name, current_runtime_context()))

    def on_end(self, span: Any) -> None:
        self.events.append(("end", span.name, current_runtime_context()))

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _install_provider() -> tuple[Any, list[dict[str, Any]]]:
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    from promptrail.tracing.opentelemetry import PromptRailSpanProcessor

    events: list[dict[str, Any]] = []
    provider = sdk_trace.TracerProvider()
    provider.add_span_processor(PromptRailSpanProcessor(on_event=events.append))
    return provider, events


def test_traceparent_trace_span_correlation_under_real_otel_spans() -> None:
    pytest.importorskip("opentelemetry.trace")
    provider, _ = _install_provider()
    PromptRail.init(gateway_url=GATEWAY, export_enabled=False, enable_opentelemetry=False)
    tracer = provider.get_tracer("promptrail-test")
    with run(run_id="run_otel"), tracer.start_as_current_span("root") as span:
        ctx = span.get_span_context()
        headers = PromptRail.inject_headers({}, url=GATEWAY)
        runtime = PromptRail.current_runtime_context()
    assert headers["traceparent"].startswith("00-")
    assert headers["x-promptrail-run-id"] == "run_otel"
    assert runtime.trace_id == f"{ctx.trace_id:032x}"
    assert runtime.span_id == f"{ctx.span_id:016x}"


def test_nested_spans_agents_parent_child_headers() -> None:
    pytest.importorskip("opentelemetry.trace")
    provider, _ = _install_provider()
    PromptRail.init(gateway_url=GATEWAY, export_enabled=False, enable_opentelemetry=False)
    tracer = provider.get_tracer("promptrail-test-nested")
    with (
        run(run_id="run_nested"),
        tracer.start_as_current_span(
            "agent", attributes={"promptrail.span.kind": "agent"}
        ) as parent,
    ):
        parent_id = f"{parent.get_span_context().span_id:016x}"
        with tracer.start_as_current_span("tool") as child:
            child_id = f"{child.get_span_context().span_id:016x}"
            runtime = PromptRail.current_runtime_context()
            headers = PromptRail.inject_headers({}, url=GATEWAY)
    assert runtime.span_id == child_id
    assert runtime.parent_span_id == parent_id
    assert headers["x-promptrail-span-id"] == child_id
    assert headers["x-promptrail-parent-span-id"] == parent_id


def test_parallel_llm_calls_same_run_different_spans() -> None:
    pytest.importorskip("opentelemetry.trace")
    provider, events = _install_provider()
    PromptRail.init(gateway_url=GATEWAY, export_enabled=False, enable_opentelemetry=False)

    class Create:
        def __init__(self) -> None:
            self.headers: list[dict[str, str]] = []

        def create(self, **kwargs: Any) -> dict[str, Any]:
            self.headers.append(kwargs["extra_headers"])
            return kwargs

    class Client:
        base_url = GATEWAY

        def __init__(self) -> None:
            self.chat = type("Chat", (), {"completions": Create()})()

    tracer = provider.get_tracer("promptrail-test-llm")
    fake = Client()
    wrapped = wrap_openai(fake)
    first = second = None
    with run(run_id="run_same"), tracer.start_as_current_span("parent"):
        with tracer.start_as_current_span("openai.chat.completions.create"):
            wrapped.chat.completions.create(model="a")
            first = fake.chat.completions.headers[-1]
        with tracer.start_as_current_span("openai.chat.completions.create"):
            wrapped.chat.completions.create(model="b")
            second = fake.chat.completions.headers[-1]
    headers = [first, second]
    assert [h["x-promptrail-run-id"] for h in headers if h is not None] == ["run_same", "run_same"]
    assert first is not None and second is not None
    assert first["x-promptrail-span-id"] != second["x-promptrail-span-id"]
    names = [event.get("name") for event in events if event.get("phase") == "start"]
    assert names.count("openai.chat.completions.create") >= 2
