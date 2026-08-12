from __future__ import annotations

import statistics
import time

from promptrail import PromptRail, RuntimeContext, current_runtime_context, run
from promptrail.exporter.queue import EventQueue
from promptrail.tracing import EventType, PromptRailEvent
from promptrail.tracing.opentelemetry import PromptRailSpanProcessor

GATEWAY = "https://gateway.promptrail.test/v1"
ITERATIONS = 1500
P99_LIMIT_MS = 5.0


def _measure(fn, iterations: int = ITERATIONS) -> tuple[float, float]:
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    ordered = sorted(samples)
    median = statistics.median(ordered)
    p99 = ordered[int(len(ordered) * 0.99) - 1]
    return median, p99


class _Context:
    trace_id = int("1" * 32, 16)
    span_id = int("2" * 16, 16)


class _Span:
    name = "openai.chat.completions.create"
    attributes = {"gen_ai.system": "openai"}
    status = None
    start_time = 1_000_000
    end_time = 2_000_000
    parent = None

    def get_span_context(self) -> _Context:
        return _Context()


def test_runtime_hot_path_overhead_benchmark_style(capsys) -> None:
    PromptRail.shutdown(timeout=0)
    PromptRail.init(gateway_url=GATEWAY, user_id="bench-user", export_enabled=False, enable_opentelemetry=False)
    queue = EventQueue(maxsize=ITERATIONS + 10)
    span_events: list[dict] = []
    processor = PromptRailSpanProcessor(on_event=span_events.append, run_id_factory=lambda: "run_bench")

    with run(run_id="run_bench", trace_id="1" * 32, span_id="2" * 16):
        event_context = current_runtime_context()

        benchmarks = {
            "context_lookup": lambda: current_runtime_context(),
            "event_creation": lambda: PromptRailEvent.from_context(
                EventType.LLM_START,
                context=event_context,
                attributes={"model": "gpt-4o-mini", "messages": ["redacted"]},
            ),
            "queue_insertion": lambda: queue.put_nowait(PromptRailEvent(EventType.OTHER_START, run_id="run_bench")),
            "header_injection": lambda: PromptRail.inject_headers({"authorization": "keep"}, url=GATEWAY),
            "span_processing": lambda: (processor.on_start(_Span()), processor.on_end(_Span())),
        }

        results = {name: _measure(fn) for name, fn in benchmarks.items()}

    for name, (median, p99) in results.items():
        print(f"{name}: median={median:.4f}ms p99={p99:.4f}ms")
        assert p99 < P99_LIMIT_MS
    assert span_events
    PromptRail.shutdown(timeout=0)
