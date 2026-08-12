"""Additive PromptRail and W3C context propagator.

The SDK uses this propagator directly for gateway calls. It does not replace the
application's global OpenTelemetry propagator.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from .headers import inject_headers


class PromptRailContextPropagator:
    """Inject standard trace context plus current PromptRail runtime headers."""

    def inject(
        self,
        carrier: MutableMapping[str, str],
        context: Any | None = None,
        setter: Any | None = None,
    ) -> None:
        try:
            values = inject_headers(carrier)
            carrier.clear()
            carrier.update(values)
        except Exception:
            return

    def extract(
        self,
        carrier: Any,
        context: Any | None = None,
        getter: Any | None = None,
    ) -> Any:
        try:
            from opentelemetry.propagate import extract

            if getter is None:
                return extract(carrier, context=context)
            return extract(carrier, context=context, getter=getter)
        except Exception:
            return context

    @property
    def fields(self) -> set[str]:
        fields = {
            "traceparent",
            "tracestate",
            "x-promptrail-run-id",
            "x-promptrail-user-id",
            "x-promptrail-trace-id",
            "x-promptrail-span-id",
            "x-promptrail-parent-span-id",
        }
        try:
            from opentelemetry.propagate import get_global_textmap

            fields.update(get_global_textmap().fields)
        except Exception:
            pass
        return fields


__all__ = ["PromptRailContextPropagator"]
