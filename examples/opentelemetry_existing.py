"""Attach PromptRail runtime correlation to an existing OpenTelemetry trace.

This example assumes your application already configures OpenTelemetry. It adds
runtime correlation events only and never changes client-side optimization.
"""

from __future__ import annotations

import os

from promptrail import PromptRail, current_runtime_context, event, run


def main() -> None:
    try:
        from opentelemetry import trace
    except ImportError as exc:  # pragma: no cover - optional example dependency
        raise SystemExit("Install OpenTelemetry first: pip install opentelemetry-api") from exc

    rail = PromptRail(project=os.environ.get("PROMPTRAIL_PROJECT", "examples"))
    tracer = trace.get_tracer("promptrail.examples")

    with tracer.start_as_current_span("answer-question") as span:
        with run("otel-existing", runtime=rail, metadata={"trace": "existing"}):
            ctx = current_runtime_context()
            span.set_attribute("promptrail.run_id", str(getattr(ctx, "run_id", "")))
            event("question.received", {"source": "example"})
            answer = "PromptRail correlates runtime activity with your trace."
            event("question.answered", {"answer_chars": len(answer)})
            print(answer)


if __name__ == "__main__":
    main()
