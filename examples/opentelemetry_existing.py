"""Attach PromptRail as an additional observer to an OpenTelemetry provider."""

from __future__ import annotations

import os

from promptrail import PromptRail, current_runtime_context, event


def main() -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError as exc:  # pragma: no cover - optional example dependency
        raise SystemExit(
            'Install the optional dependency: pip install "promptrail[opentelemetry]"'
        ) from exc

    provider = trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        trace.set_tracer_provider(TracerProvider())

    PromptRail.init(
        api_key=os.environ.get("PROMPTRAIL_API_KEY"),
        application="existing-otel-example",
        user_id=lambda: "example-user",
        export_enabled=bool(os.environ.get("PROMPTRAIL_API_KEY")),
    )
    tracer = trace.get_tracer("promptrail.examples")

    try:
        with tracer.start_as_current_span(
            "answer-question", attributes={"workflow.name": "question-answering"}
        ):
            event("workflow.stage", name="answering")
            print(current_runtime_context())
    finally:
        PromptRail.shutdown()


if __name__ == "__main__":
    main()
