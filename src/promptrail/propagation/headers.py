"""Synchronous PromptRail gateway identity injection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from urllib.parse import quote, urlsplit

from ..sdk import SDK_VERSION, current_runtime_context, get_runtime_client
from ..tracing.canonical import SCHEMA_VERSION
from ..tracing.opentelemetry import inject_trace_headers
from ..utils.ids import secure_id

_PROMPTRAIL_HEADERS = {
    "x-promptrail-run-id",
    "x-promptrail-user-id",
    "x-promptrail-trace-id",
    "x-promptrail-span-id",
    "x-promptrail-parent-span-id",
    "x-promptrail-application",
    "x-promptrail-environment",
    "x-promptrail-schema-version",
    "x-promptrail-sdk-version",
}


def is_promptrail_gateway(url: object | None, *, configured_url: str | None = None) -> bool:
    """Return whether a URL terminates at the configured PromptRail gateway."""

    client = get_runtime_client()
    expected = configured_url or (
        client.config.gateway_url if client else "https://api.promptrail.ai/v1"
    )
    candidate = str(url or expected)
    try:
        actual_parts = urlsplit(candidate)
        expected_parts = urlsplit(expected)
        actual_port = actual_parts.port or (443 if actual_parts.scheme == "https" else 80)
        expected_port = expected_parts.port or (443 if expected_parts.scheme == "https" else 80)
        return (
            actual_parts.scheme.casefold() == expected_parts.scheme.casefold()
            and (actual_parts.hostname or "").casefold()
            == (expected_parts.hostname or "").casefold()
            and actual_port == expected_port
        )
    except Exception:
        return False


def inject_headers(
    headers: Mapping[str, str] | None = None,
    *,
    url: object | None = None,
    ensure_run: bool = True,
) -> dict[str, str]:
    """Return request headers carrying the current runtime identity.

    The operation is local and fail-open. Private PromptRail headers are emitted
    only for the configured gateway origin, so credentials and correlation values
    are never added to direct model-provider requests.
    """

    try:
        output = {str(key): str(value) for key, value in (headers or {}).items()}
    except Exception:
        return {}
    if not is_promptrail_gateway(url):
        _remove_private_headers(output)
        return output
    try:
        inject_trace_headers(output)
        context = current_runtime_context()
        if ensure_run and context.run_id is None:
            if context.trace_id is not None:
                context = current_runtime_context(ensure_run=True)
            else:
                context = replace(context, run_id=secure_id("run"))
        trace_id = _canonical_hex(context.trace_id, 32)
        span_id = _canonical_hex(context.span_id, 16)
        parent_span_id = _canonical_hex(context.parent_span_id, 16)
        if trace_id and span_id:
            traceparent = _header_value(output, "traceparent")
            if not _traceparent_matches(traceparent, trace_id, span_id):
                _remove_header(output, "traceparent")
                _remove_header(output, "tracestate")
                output["traceparent"] = f"00-{trace_id}-{span_id}-01"
        client = get_runtime_client()
        values = {
            "x-promptrail-run-id": context.run_id,
            "x-promptrail-user-id": context.user_id,
            "x-promptrail-trace-id": trace_id,
            "x-promptrail-span-id": span_id,
            "x-promptrail-parent-span-id": parent_span_id,
            "x-promptrail-application": client.config.application if client else None,
            "x-promptrail-environment": client.config.environment if client else None,
            "x-promptrail-schema-version": SCHEMA_VERSION,
            "x-promptrail-sdk-version": SDK_VERSION,
        }
        _remove_private_headers(output)
        for key, value in values.items():
            if value is not None:
                output[key] = _safe_header_value(value)
        return output
    except Exception:
        return output


def _safe_header_value(value: object) -> str:
    text = str(value).replace("\r", "").replace("\n", "")[:1024]
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return quote(text, safe=":._-@")
    return text


def _remove_private_headers(headers: dict[str, str]) -> None:
    for owned in _PROMPTRAIL_HEADERS:
        _remove_header(headers, owned)


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    return next((value for key, value in headers.items() if key.casefold() == name), None)


def _remove_header(headers: dict[str, str], name: str) -> None:
    for key in tuple(headers):
        if key.casefold() == name:
            headers.pop(key, None)


def _traceparent_matches(value: str | None, trace_id: str, span_id: str) -> bool:
    if value is None:
        return False
    parts = value.split("-")
    return len(parts) == 4 and parts[1].casefold() == trace_id and parts[2].casefold() == span_id


def _valid_hex(value: str | None, length: int) -> bool:
    if value is None or len(value) != length:
        return False
    try:
        return int(value, 16) != 0
    except ValueError:
        return False


def _canonical_hex(value: str | None, length: int) -> str | None:
    return value.casefold() if _valid_hex(value, length) and value is not None else None


__all__ = ["inject_headers", "is_promptrail_gateway"]
