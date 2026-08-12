"""SDK-backed contracts for the trace connection demo interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from promptrail.historical import (
    MAX_HISTORICAL_IMPORT_BYTES,
    HistoricalImportResult,
    TraceSourceConfiguration,
    import_historical_traces,
)


@dataclass(slots=True)
class TraceConnectionService:
    """Validate UI requests through the same contracts used by the Runtime SDK."""

    max_import_bytes: ClassVar[int] = MAX_HISTORICAL_IMPORT_BYTES
    configurations: dict[str, TraceSourceConfiguration] = field(default_factory=dict)
    last_import: HistoricalImportResult | None = None

    def capabilities(self) -> dict[str, Any]:
        return {
            "sdk": {
                "package": "promptrail",
                "trace_processor": "PromptRailSpanProcessor",
                "runtime_events_endpoint": "/v1/runtime/events",
                "schema_version": "1.0",
                "max_import_bytes": MAX_HISTORICAL_IMPORT_BYTES,
            },
            "sources": [
                "langsmith",
                "langfuse",
                "braintrust",
                "helicone",
                "opentelemetry",
                "custom",
            ],
        }

    def connect(self, payload: dict[str, Any]) -> dict[str, Any]:
        configuration = TraceSourceConfiguration.validate(
            source=payload.get("source", ""),
            project=payload.get("project", ""),
            credential=payload.get("credential"),
            metadata_only=bool(payload.get("metadata_only", True)),
        )
        self.configurations[configuration.source.value] = configuration
        return {
            "status": "configured",
            "connection": configuration.to_public_dict(),
            "message": (
                "Source configuration validated against the PromptRail SDK contract. "
                "Remote historical sync begins when the control-plane connector is available."
            ),
        }

    def import_json(self, body: bytes, *, metadata_only: bool = True) -> dict[str, Any]:
        result = import_historical_traces(body, metadata_only=metadata_only)
        self.last_import = result
        return {
            "status": "accepted",
            "import": result.summary(),
            "message": "Trace data normalized to PromptRail runtime event schema 1.0.",
        }


__all__ = ["TraceConnectionService"]
