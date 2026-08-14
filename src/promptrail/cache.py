"""Prompt-prefix analysis and session-scoped cache value accounting."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import (
    CacheAnalysis,
    CacheValue,
    ContextBlock,
    ModelCandidate,
    ModelUsage,
    ProviderRoute,
)

_ERROR_RE = re.compile(
    r"(?im)(?:^|\b)(?:ERROR|FAILED|FAILURE|FATAL|Traceback|"
    r"[A-Za-z_][\w.]*(?:Error|Exception))(?::|\b)"
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def estimate_tokens(value: Any) -> int:
    """Deterministic UTF-8 estimate; callers can replace it with provider counts later."""

    length = len(canonical_json(value))
    return max(1, (length + 3) // 4)


def _prefix_hash(
    messages: tuple[dict[str, Any], ...],
    count: int,
    tools: tuple[dict[str, Any], ...],
) -> str | None:
    if count <= 0 and not tools:
        return None
    return hashlib.sha256(
        canonical_json({"messages": messages[:count], "tools": tools})
    ).hexdigest()


@dataclass(frozen=True)
class _CacheRecord:
    model_id: str
    provider: str
    prefix_hash: str
    message_count: int
    prefix_tokens: int
    expires_at: datetime


@dataclass
class _SessionState:
    last_model_id: str | None = None
    last_provider: str | None = None
    record: _CacheRecord | None = None


class PromptCacheCoordinator:
    """Analyze one session without storing prompt content."""

    def __init__(self, *, minimum_compactable_tokens: int = 128) -> None:
        if minimum_compactable_tokens <= 0:
            raise ValueError("minimum_compactable_tokens must be positive")
        self._minimum_compactable_tokens = minimum_compactable_tokens
        self._sessions: dict[str, _SessionState] = {}
        self._lock = threading.RLock()

    def analyze(
        self,
        *,
        session_id: str,
        messages: tuple[dict[str, Any], ...],
        tools: tuple[dict[str, Any], ...],
        candidates: tuple[ModelCandidate, ...],
        predicted_output_tokens: int,
        task: str = "",
        context_blocks: tuple[ContextBlock, ...] | None = None,
    ) -> CacheAnalysis:
        if not messages:
            raise ValueError("cache analysis requires at least one message")
        message_tokens = tuple(estimate_tokens(message) for message in messages)
        tool_tokens = sum(estimate_tokens(tool) for tool in tools)
        total_tokens = sum(message_tokens) + tool_tokens
        proposed_count = self._proposed_prefix_count(messages)
        proposed_hash = _prefix_hash(messages, proposed_count, tools)
        now = datetime.now(UTC)
        with self._lock:
            state = self._sessions.get(session_id, _SessionState())
            record = state.record
            if record is not None and record.expires_at <= now:
                record = None
                state.record = None

            active_exact = bool(
                record
                and record.message_count <= len(messages)
                and _prefix_hash(messages, record.message_count, tools) == record.prefix_hash
            )
            confirmed_prefix_count = (
                record.message_count
                if record is not None and active_exact and record.prefix_tokens > 0
                else 0
            )
            cacheable_indices = tuple(range(confirmed_prefix_count))
            protected_indices = self._protected_indices(messages)
            compactable_indices = tuple(
                index
                for index, message in enumerate(messages)
                if index not in protected_indices
                and message_tokens[index] >= self._minimum_compactable_tokens
                and self._safe_compactable_message(message)
            )
            cacheable_tokens = (
                record.prefix_tokens
                if record is not None and confirmed_prefix_count
                else 0
            )
            compactable_tokens = sum(message_tokens[index] for index in compactable_indices)
            accounted_message_indices = set(cacheable_indices) | set(compactable_indices)
            accounted_tokens = sum(
                message_tokens[index] for index in accounted_message_indices
            ) + (tool_tokens if confirmed_prefix_count else 0)
            protected_dynamic_tokens = max(0, total_tokens - accounted_tokens)
            values = {
                candidate.model_id: self._candidate_value(
                    candidate=candidate,
                    total_tokens=total_tokens,
                    predicted_output_tokens=predicted_output_tokens,
                    record=record if active_exact else None,
                )
                for candidate in candidates
            }
            raw_blocks = context_blocks or self.build_context_blocks(messages=messages, task=task)
            blocks = tuple(
                block.model_copy(
                    update={
                        "cached": any(
                            index in cacheable_indices for index in block.message_indices
                        ),
                        "cache_invalidation_cost": (
                            block.tokens / max(1, cacheable_tokens)
                            if any(index in cacheable_indices for index in block.message_indices)
                            else 0.0
                        ),
                    }
                )
                for block in raw_blocks
            )
            return CacheAnalysis(
                total_tokens=total_tokens,
                tool_tokens=tool_tokens,
                message_tokens=message_tokens,
                # This is the prefix offered to the provider by the current call. It is
                # deliberately separate from cacheable_message_indices, which only describe
                # a prefix the provider has already reported as cached.
                prefix_hash=proposed_hash,
                cacheable_message_indices=cacheable_indices,
                protected_message_indices=protected_indices,
                compactable_message_indices=compactable_indices,
                cacheable_tokens=cacheable_tokens,
                protected_dynamic_tokens=protected_dynamic_tokens,
                compactable_tokens=compactable_tokens,
                last_model_id=state.last_model_id,
                last_provider=state.last_provider,
                values=values,
                context_blocks=blocks,
            )

    @classmethod
    def build_context_blocks(
        cls,
        *,
        messages: tuple[dict[str, Any], ...],
        task: str,
    ) -> tuple[ContextBlock, ...]:
        """Build deterministic units before Gemma supplies bounded overrides."""

        task_terms = set(re.findall(r"[a-z0-9_]{3,}", task.casefold()))
        latest_user = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if str(messages[index].get("role") or "").casefold() == "user"
                and not messages[index].get("tool_call_id")
            ),
            None,
        )
        latest_tool = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if str(messages[index].get("role") or "").casefold() == "tool"
                or messages[index].get("tool_call_id")
            ),
            None,
        )
        seen: dict[str, int] = {}
        blocks: list[ContextBlock] = []
        for index, message in enumerate(messages):
            role = str(message.get("role") or "").casefold()
            text = cls._content_text(message.get("content"))
            if role in {"system", "developer"}:
                block_type = "protocol"
            elif role == "user":
                block_type = "user"
            elif role == "tool" or message.get("tool_call_id"):
                if re.search(r"(?im)(?:pytest|unittest|\btests?\b|passed|failed)", text):
                    block_type = "test"
                elif re.search(r"(?m)^(?:diff --git|@@ |\+\+\+ |--- )", text):
                    block_type = "patch"
                else:
                    block_type = "tool"
            elif role == "assistant":
                block_type = "assistant"
            else:
                block_type = "generic"
            content_terms = set(re.findall(r"[a-z0-9_]{3,}", text.casefold()))
            overlap = len(task_terms & content_terms) / max(1, len(task_terms))
            fingerprint = hashlib.sha256(canonical_json(message)).hexdigest()
            duplicate = seen.get(fingerprint, 0)
            seen[fingerprint] = duplicate + 1
            age = len(messages) - index - 1
            immutable = role in {"system", "developer"} or index == latest_user
            if immutable:
                importance = 1.0
            elif index == latest_tool:
                importance = 0.95
            elif _ERROR_RE.search(text) or block_type in {"test", "patch"}:
                importance = 0.9
            else:
                recency = 1 / (1 + age / 4)
                importance = min(0.95, 0.25 + 0.45 * overlap + 0.3 * recency)
            blocks.append(
                ContextBlock(
                    block_id=f"ctx_{fingerprint[:16]}_{duplicate}",
                    message_indices=(index,),
                    block_type=block_type,
                    tokens=estimate_tokens(message),
                    age=age,
                    task_overlap=overlap,
                    redundancy=min(1.0, duplicate * 0.5),
                    cache_invalidation_cost=0.0,
                    importance=importance,
                    structurally_immutable=immutable,
                )
            )
        return tuple(blocks)

    def observe(
        self,
        *,
        session_id: str,
        analysis: CacheAnalysis,
        route: ProviderRoute,
        usage: ModelUsage,
    ) -> None:
        """Record selection and only promote a cache when the provider can support it."""

        with self._lock:
            state = self._sessions.setdefault(session_id, _SessionState())
            state.last_model_id = usage.model_id
            state.last_provider = usage.provider
            cache_observed = usage.cache_read_tokens > 0 or usage.cache_write_tokens > 0
            if analysis.prefix_hash and route.cache_supported and cache_observed:
                state.record = _CacheRecord(
                    model_id=usage.model_id,
                    provider=usage.provider,
                    prefix_hash=analysis.prefix_hash,
                    message_count=self._candidate_prefix_count(analysis),
                    prefix_tokens=max(usage.cache_read_tokens, usage.cache_write_tokens),
                    expires_at=datetime.now(UTC) + timedelta(seconds=route.cache_ttl_seconds),
                )

    @staticmethod
    def _candidate_prefix_count(analysis: CacheAnalysis) -> int:
        """Recover the proposed prefix boundary without retaining prompt content."""

        latest_user = max(
            (
                index
                for block in analysis.context_blocks
                if block.block_type == "user" and block.structurally_immutable
                for index in block.message_indices
            ),
            default=None,
        )
        if latest_user is not None:
            return latest_user
        protocol_indices = {
            index
            for block in analysis.context_blocks
            if block.block_type == "protocol"
            for index in block.message_indices
        }
        count = 0
        while count in protocol_indices:
            count += 1
        return count

    @staticmethod
    def _proposed_prefix_count(messages: tuple[dict[str, Any], ...]) -> int:
        latest_user = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if str(messages[index].get("role") or "").casefold() == "user"
                and not messages[index].get("tool_call_id")
            ),
            None,
        )
        if latest_user is not None:
            return latest_user
        count = 0
        for message in messages:
            if str(message.get("role") or "").casefold() not in {"system", "developer"}:
                break
            count += 1
        return count

    @classmethod
    def _protected_indices(
        cls,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[int, ...]:
        protected: set[int] = set()
        latest_user = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if str(messages[index].get("role") or "").casefold() == "user"
                and not messages[index].get("tool_call_id")
            ),
            None,
        )
        if latest_user is not None:
            protected.add(latest_user)
        tool_results = {
            str(message.get("tool_call_id")) for message in messages if message.get("tool_call_id")
        }
        for index, message in enumerate(messages):
            role = str(message.get("role") or "").casefold()
            if role in {"system", "developer"}:
                protected.add(index)
            for tool_call in message.get("tool_calls") or ():
                call_id = str(tool_call.get("id") or "") if isinstance(tool_call, dict) else ""
                if call_id and call_id not in tool_results:
                    protected.add(index)
        return tuple(sorted(protected))

    @classmethod
    def _safe_compactable_message(cls, message: dict[str, Any]) -> bool:
        role = str(message.get("role") or "").casefold()
        if role not in {"assistant", "tool"}:
            return False
        content = message.get("content")
        return isinstance(content, str) and bool(content.strip())

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        try:
            return canonical_json(content).decode("utf-8")
        except (TypeError, ValueError, UnicodeDecodeError):
            return str(content)

    @staticmethod
    def _route_input_cost(
        route: ProviderRoute,
        *,
        total_tokens: int,
        cached_tokens: int,
    ) -> float:
        cached_rate = (
            route.cache_read_price_per_million
            if route.cache_read_price_per_million is not None
            else route.input_price_per_million
        )
        return (
            cached_tokens * cached_rate
            + max(0, total_tokens - cached_tokens) * route.input_price_per_million
        ) / 1_000_000

    def _candidate_value(
        self,
        *,
        candidate: ModelCandidate,
        total_tokens: int,
        predicted_output_tokens: int,
        record: _CacheRecord | None,
    ) -> CacheValue:
        route_costs: list[tuple[float, ProviderRoute, bool, int]] = []
        for route in candidate.routes:
            exact = bool(
                record
                and candidate.model_id == record.model_id
                and route.provider == record.provider
                and route.cache_supported
            )
            cached_tokens = min(total_tokens, record.prefix_tokens) if exact and record else 0
            input_cost = self._route_input_cost(
                route,
                total_tokens=total_tokens,
                cached_tokens=cached_tokens,
            )
            output_cost = predicted_output_tokens * route.output_price_per_million / 1_000_000
            route_costs.append((input_cost + output_cost, route, exact, cached_tokens))
        _, selected_route, exact, cached_tokens = min(
            route_costs,
            key=lambda item: (item[0], item[1].p95_total_latency_ms, item[1].route_id),
        )
        input_cost = self._route_input_cost(
            selected_route,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
        )
        uncached_cost = total_tokens * selected_route.input_price_per_million / 1_000_000
        current_savings = max(0.0, uncached_cost - input_cost)
        # Open-ended agents do not have a defensible fixed remaining-call count.
        # Value the cache on the current/next-call horizon without inventing one.
        retained_value = current_savings
        switch_cost = 0.0
        if record and not exact:
            record_route = next(
                (
                    route
                    for route in candidate.routes
                    if route.provider == record.provider and route.cache_supported
                ),
                None,
            )
            if record_route is not None:
                cached_rate = (
                    record_route.cache_read_price_per_million
                    if record_route.cache_read_price_per_million is not None
                    else record_route.input_price_per_million
                )
                switch_cost = (
                    min(total_tokens, record.prefix_tokens)
                    * max(0.0, record_route.input_price_per_million - cached_rate)
                    / 1_000_000
                )
        return CacheValue(
            model_id=candidate.model_id,
            provider=selected_route.provider,
            exact_reuse=exact,
            cached_tokens=cached_tokens,
            input_cost_usd=input_cost,
            retained_value_usd=retained_value,
            switch_cost_usd=switch_cost,
        )
