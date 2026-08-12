"""Base tracing helpers without optional OpenTelemetry dependencies."""

from __future__ import annotations

from promptrail.context import RuntimeContext, current_runtime_context
from promptrail.tracing.canonical import SCHEMA_VERSION


def inject_headers(
    headers: dict[str, str] | None = None,
    *,
    context: RuntimeContext | None = None,
    application: str | None = None,
    environment: str | None = None,
    sdk_version: str | None = None,
) -> dict[str, str]:
    """Return headers with fresh PromptRail identity metadata added."""
    output = dict(headers or {})
    ctx = context or current_runtime_context()
    owned = [key for key in output if key.lower().startswith("x-promptrail-")]
    for key in owned:
        output.pop(key, None)
    if ctx.trace_id and ctx.span_id:
        output["traceparent"] = f"00-{ctx.trace_id}-{ctx.span_id}-01"
    _set(output, "x-promptrail-run-id", ctx.run_id)
    _set(output, "x-promptrail-user-id", ctx.user_id)
    _set(output, "x-promptrail-trace-id", ctx.trace_id)
    _set(output, "x-promptrail-span-id", ctx.span_id)
    _set(output, "x-promptrail-parent-span-id", ctx.parent_span_id)
    _set(output, "x-promptrail-application", application)
    _set(output, "x-promptrail-environment", environment)
    output["x-promptrail-schema-version"] = SCHEMA_VERSION
    _set(output, "x-promptrail-sdk-version", sdk_version)
    return output


def _set(headers: dict[str, str], name: str, value: str | None) -> None:
    if value:
        headers[name] = value
