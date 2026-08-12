"""Conservative span classification helpers for PromptRail tracing.

The classifier is intentionally small and dependency-free. It prefers stable
semantic attributes over span-name heuristics, and falls back to ``other`` when
there is not enough evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

SpanKind = Literal["agent", "tool", "retrieval", "llm", "workflow", "branch", "other"]

_CLASS_NAMES: set[str] = {"agent", "tool", "retrieval", "llm", "workflow", "branch", "other"}
_LLM_SYSTEMS = {
    "anthropic",
    "azure.ai.inference",
    "bedrock",
    "cohere",
    "gemini",
    "gen_ai",
    "mistral",
    "ollama",
    "openai",
    "vertex_ai",
}
_RETRIEVAL_SYSTEMS = {"chroma", "elasticsearch", "milvus", "pinecone", "qdrant", "redis", "vector", "weaviate"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value).strip().lower()
    except Exception:
        return ""


def _attributes(span_or_attributes: Any) -> Mapping[str, Any]:
    if isinstance(span_or_attributes, Mapping):
        return span_or_attributes
    attributes = getattr(span_or_attributes, "attributes", None)
    if isinstance(attributes, Mapping):
        return attributes
    return {}


def _name(span_or_name: Any) -> str:
    if isinstance(span_or_name, str):
        return span_or_name
    return _text(getattr(span_or_name, "name", ""))


def classify_span(span_or_attributes: Any, name: str | None = None) -> SpanKind:
    """Classify a span as agent/tool/retrieval/llm/workflow/branch/other.

    The order is semantic-attribute-first, then cautious name heuristics. Unknown
    or malformed values fail open to ``other``.
    """

    try:
        attributes = _attributes(span_or_attributes)
        explicit = _text(
            attributes.get("promptrail.span.kind")
            or attributes.get("promptrail.kind")
            or attributes.get("llm.span.kind")
            or attributes.get("span.kind")
        )
        if explicit in _CLASS_NAMES:
            return explicit  # type: ignore[return-value]

        operation = _text(
            attributes.get("gen_ai.operation.name")
            or attributes.get("ai.operation.name")
            or attributes.get("llm.operation")
        )
        if operation in {"chat", "completion", "embeddings", "generate", "invoke"}:
            return "llm"
        if operation in {"execute_tool", "tool", "function_call"}:
            return "tool"
        if operation in {"retrieve", "search", "query"}:
            return "retrieval"

        system = _text(attributes.get("gen_ai.system") or attributes.get("llm.system") or attributes.get("db.system"))
        if system in _LLM_SYSTEMS or system.startswith("openai"):
            return "llm"
        if system in _RETRIEVAL_SYSTEMS:
            return "retrieval"

        if any(key in attributes for key in ("gen_ai.request.model", "gen_ai.response.model", "llm.model_name", "ai.model.id")):
            return "llm"
        if any(key in attributes for key in ("tool.name", "tool.call.id", "function.name", "gen_ai.tool.name")):
            return "tool"
        if any(key in attributes for key in ("retrieval.query", "retrieval.documents", "db.vector.query", "vector.top_k")):
            return "retrieval"
        if any(key in attributes for key in ("agent.name", "agent.id", "ai.agent.name")):
            return "agent"
        if any(key in attributes for key in ("workflow.name", "workflow.id", "job.name", "task.name")):
            return "workflow"
        if any(key in attributes for key in ("branch.name", "branch.id", "decision.branch")):
            return "branch"

        span_name = _text(name) or _name(span_or_attributes)
        tokens = span_name.replace(".", " ").replace("_", " ").replace("-", " ")
        if any(word in tokens.split() for word in ("agent", "planner", "executor")):
            return "agent"
        if any(word in tokens.split() for word in ("tool", "function")):
            return "tool"
        if any(word in tokens.split() for word in ("retrieve", "retrieval", "rerank", "search")):
            return "retrieval"
        if any(word in tokens.split() for word in ("llm", "chat", "completion", "embedding", "openai", "anthropic")):
            return "llm"
        if any(word in tokens.split() for word in ("workflow", "pipeline", "run")):
            return "workflow"
        if any(word in tokens.split() for word in ("branch", "route", "decision")):
            return "branch"
    except Exception:
        return "other"
    return "other"


__all__ = ["SpanKind", "classify_span"]
