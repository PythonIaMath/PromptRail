"""Cache-scoped deterministic context compaction."""

from __future__ import annotations

import copy
import hashlib
import math
import re
import threading
from dataclasses import dataclass
from uuid import uuid4

from .cache import canonical_json, estimate_tokens
from .errors import CompactionError
from .models import (
    CacheAnalysis,
    CallBudget,
    CompactionPlan,
    CompactionRecord,
    CompactionResult,
    ModelDecision,
)


class CompactionTargetPlanner:
    """Translate Gemma's required context and importance overrides into a plan."""

    def __init__(self, *, minimum_kept_tokens: int = 32) -> None:
        if minimum_kept_tokens <= 0:
            raise ValueError("minimum_kept_tokens must be positive")
        self._minimum_kept_tokens = minimum_kept_tokens

    def minimum_reachable_tokens(self, cache: CacheAnalysis) -> int:
        immutable_indices = set(cache.protected_message_indices)
        reducible_tokens = sum(
            max(0, cache.message_tokens[index] - self._minimum_kept_tokens)
            for index in cache.compactable_message_indices
            if index not in immutable_indices
        )
        return cache.total_tokens - reducible_tokens

    def plan(
        self,
        *,
        cache: CacheAnalysis,
        decision: ModelDecision,
        budget: CallBudget,
    ) -> CompactionPlan:
        route = decision.route
        target = min(cache.total_tokens, budget.required_context_tokens)
        physically_reachable_floor = self.minimum_reachable_tokens(cache)
        target_tokens = max(physically_reachable_floor, target)
        required_reduction = max(0, cache.total_tokens - target_tokens)
        protected_indices = set(cache.protected_message_indices)
        cached_indices = set(cache.cacheable_message_indices)
        noncached_capacity = sum(
            max(0, cache.message_tokens[index] - self._minimum_kept_tokens)
            for index in cache.compactable_message_indices
            if index not in protected_indices and index not in cached_indices
        )
        eligible_indices = tuple(
            index
            for index in cache.compactable_message_indices
            if index not in protected_indices
            and (required_reduction > noncached_capacity or index not in cached_indices)
        )
        cached_tokens = (
            min(target_tokens, decision.cache_value.cached_tokens)
            if decision.cache_value.exact_reuse and required_reduction <= noncached_capacity
            else 0
        )
        cached_rate = (
            route.cache_read_price_per_million
            if cached_tokens and route.cache_read_price_per_million is not None
            else route.input_price_per_million
        )
        estimated_cost = (
            cached_tokens * cached_rate / 1_000_000
            + max(0, target_tokens - cached_tokens) * route.input_price_per_million / 1_000_000
        )
        overrides = {item.block_id: item.importance for item in budget.importance_overrides}
        importance_by_index = {
            index: overrides.get(block.block_id, block.importance)
            for block in cache.context_blocks
            for index in block.message_indices
        }
        return CompactionPlan(
            input_budget_usd=budget.input_cost_usd,
            estimated_input_cost_usd=estimated_cost,
            target_tokens=target_tokens,
            required_reduction_tokens=required_reduction,
            compactable_message_indices=eligible_indices,
            importance_by_message_index=importance_by_index,
        )


@dataclass(frozen=True)
class RetrievedContext:
    session_id: str
    content: str


class InMemoryRetrievalStore:
    """Process-local recovery store for deterministic compaction originals."""

    def __init__(self) -> None:
        self._values: dict[str, RetrievedContext] = {}
        self._lock = threading.RLock()

    def put(self, *, session_id: str, content: str) -> str:
        retrieval_id = f"ctx_{uuid4().hex}"
        with self._lock:
            self._values[retrieval_id] = RetrievedContext(session_id=session_id, content=content)
        return retrieval_id

    def get(self, *, session_id: str, retrieval_id: str) -> str:
        with self._lock:
            item = self._values.get(retrieval_id)
        if item is None or item.session_id != session_id:
            raise CompactionError("compaction retrieval entry is missing or outside session scope")
        return item.content

    def delete(self, *, session_id: str, retrieval_id: str) -> None:
        with self._lock:
            item = self._values.get(retrieval_id)
            if item is not None and item.session_id == session_id:
                self._values.pop(retrieval_id, None)

    def delete_session(self, session_id: str) -> int:
        with self._lock:
            matching = [
                retrieval_id
                for retrieval_id, item in self._values.items()
                if item.session_id == session_id
            ]
            for retrieval_id in matching:
                self._values.pop(retrieval_id, None)
            return len(matching)


class CacheScopedCompactor:
    """Importance-weighted, inline-only context compaction."""

    def __init__(
        self,
        store: InMemoryRetrievalStore | None = None,
        *,
        minimum_kept_tokens: int = 32,
    ) -> None:
        if minimum_kept_tokens <= 0:
            raise ValueError("minimum_kept_tokens must be positive")
        # Kept as a compatibility attribute for callers created before schema v2.
        # Compacted prompts never contain retrieval IDs and no content is stored.
        self._store = store if store is not None else InMemoryRetrievalStore()
        self._minimum_kept_tokens = minimum_kept_tokens

    @property
    def store(self) -> InMemoryRetrievalStore:
        return self._store

    @property
    def minimum_kept_tokens(self) -> int:
        return self._minimum_kept_tokens

    def compact(
        self,
        *,
        session_id: str,
        messages: tuple[dict[str, object], ...],
        cache: CacheAnalysis,
        plan: CompactionPlan,
    ) -> CompactionResult:
        if len(messages) != len(cache.message_tokens):
            raise CompactionError("cache analysis does not match the supplied conversation")
        original = copy.deepcopy(messages)
        transformed = [copy.deepcopy(message) for message in messages]
        remaining = plan.required_reduction_tokens
        records: list[CompactionRecord] = []
        block_by_index = {
            index: block for block in cache.context_blocks for index in block.message_indices
        }
        immutable_indices = set(cache.protected_message_indices)
        capacities = {
            index: (
                0
                if index in immutable_indices
                else max(0, cache.message_tokens[index] - self._minimum_kept_tokens)
            )
            for index in plan.compactable_message_indices
        }
        pressures: dict[int, float] = {}
        for index in plan.compactable_message_indices:
            block = block_by_index.get(index)
            importance = plan.importance_by_message_index.get(index, 0.5)
            redundancy = max(0.1, block.redundancy if block is not None else 0.1)
            staleness = 1 + ((block.age if block is not None else 0) / 8)
            cache_factor = (
                1 / (1 + 8 * block.cache_invalidation_cost)
                if block is not None and block.cached
                else 1.0
            )
            pressures[index] = (1 - importance) ** 2 * redundancy * staleness * cache_factor
        allocations = self._water_fill(
            reduction=remaining,
            capacities=capacities,
            pressures=pressures,
        )
        eligible = sorted(allocations, key=lambda index: (-allocations[index], index))
        for index in eligible:
            content = transformed[index].get("content")
            if not isinstance(content, str):
                continue
            before = estimate_tokens(transformed[index])
            requested_reduction = min(remaining, allocations[index])
            if requested_reduction <= 0:
                continue
            target_content_tokens = max(
                self._minimum_kept_tokens,
                estimate_tokens(content) - requested_reduction,
            )
            original_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            block = block_by_index.get(index)
            compacted = self._compact_text(
                content,
                target_tokens=target_content_tokens,
                block_type=block.block_type if block is not None else "generic",
            )
            transformed[index]["content"] = compacted
            after = estimate_tokens(transformed[index])
            if after >= before:
                transformed[index] = copy.deepcopy(original[index])
                continue
            actual_reduction = before - after
            remaining = max(0, remaining - actual_reduction)
            records.append(
                CompactionRecord(
                    message_index=index,
                    original_hash=original_hash,
                    tokens_before=before,
                    tokens_after=after,
                    block_type=block.block_type if block is not None else "generic",
                    importance=plan.importance_by_message_index.get(index, 0.5),
                )
            )

        self._validate_boundaries(original, tuple(transformed), cache, plan)
        tokens_after = cache.tool_tokens + sum(estimate_tokens(message) for message in transformed)
        return CompactionResult(
            messages=tuple(transformed),
            tokens_before=cache.total_tokens,
            tokens_after=tokens_after,
            target_tokens=plan.target_tokens,
            target_met=tokens_after <= plan.target_tokens,
            records=tuple(records),
        )

    @staticmethod
    def _compact_text(
        content: str,
        *,
        target_tokens: int,
        block_type: str,
    ) -> str:
        marker = f"\n[PromptRail inline {block_type} summary]\n"
        content_budget = max(48, target_tokens * 4 - len(marker))
        if content_budget >= len(content):
            return content
        lines = content.splitlines()
        if block_type in {"test", "patch"}:
            signal = re.compile(
                r"(?i)(?:error|fail|traceback|passed|diff --git|^@@|^\+\+\+|^---|^\+|^- )"
            )
            important = [line for line in lines if signal.search(line)]
        elif block_type == "tool":
            signal = re.compile(r"(?i)(?:^/|\.py\b|\.json\b|\.toml\b|exit |error|warning)")
            important = [line for line in lines if signal.search(line)]
        else:
            important = []
        head_budget = max(24, math.floor(content_budget * 0.45))
        tail_budget = max(24, math.floor(content_budget * 0.25))
        signal_budget = max(0, content_budget - head_budget - tail_budget)
        signal_text = "\n".join(important)[:signal_budget].rstrip()
        parts = [content[:head_budget].rstrip(), marker.strip()]
        if signal_text:
            parts.append(signal_text)
        parts.append(content[-tail_budget:].lstrip())
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _water_fill(
        *,
        reduction: int,
        capacities: dict[int, int],
        pressures: dict[int, float],
    ) -> dict[int, int]:
        """Allocate reduction proportionally, redistributing capped shares."""

        allocations = {index: 0 for index in capacities}
        active = {index for index, capacity in capacities.items() if capacity > 0}
        remaining = reduction
        while remaining > 0 and active:
            total_pressure = sum(max(pressures[index], 1e-9) for index in active)
            progressed = 0
            for index in tuple(sorted(active)):
                capacity = capacities[index] - allocations[index]
                share = max(
                    1,
                    math.floor(remaining * max(pressures[index], 1e-9) / total_pressure),
                )
                amount = min(capacity, share, remaining)
                allocations[index] += amount
                remaining -= amount
                progressed += amount
                if allocations[index] >= capacities[index]:
                    active.discard(index)
                if remaining <= 0:
                    break
            if progressed == 0:
                break
        return {index: amount for index, amount in allocations.items() if amount > 0}

    @staticmethod
    def _validate_boundaries(
        before: tuple[dict[str, object], ...],
        after: tuple[dict[str, object], ...],
        cache: CacheAnalysis,
        plan: CompactionPlan,
    ) -> None:
        authorized = set(plan.compactable_message_indices)
        for index, (old, new) in enumerate(zip(before, after, strict=True)):
            changed = canonical_json(old) != canonical_json(new)
            if changed and index not in authorized:
                raise CompactionError(f"compaction changed unauthorized message index {index}")
            if index in cache.protected_message_indices and changed:
                raise CompactionError(f"compaction changed protected message index {index}")
