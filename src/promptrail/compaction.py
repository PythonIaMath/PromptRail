"""Cache-scoped deterministic context compaction."""

from __future__ import annotations

import copy
import hashlib
import math
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
    """Derive an input-token target from selected-model price and call budget."""

    def plan(
        self,
        *,
        cache: CacheAnalysis,
        decision: ModelDecision,
        budget: CallBudget,
    ) -> CompactionPlan:
        route = decision.route
        cached_tokens = (
            min(cache.total_tokens, decision.cache_value.cached_tokens)
            if decision.cache_value.exact_reuse
            else 0
        )
        cached_rate = (
            route.cache_read_price_per_million
            if cached_tokens and route.cache_read_price_per_million is not None
            else route.input_price_per_million
        )
        fixed_cached_cost = cached_tokens * cached_rate / 1_000_000
        remaining_input_budget = max(0.0, budget.input_cost_usd - fixed_cached_cost)
        affordable_uncached_tokens = math.floor(
            remaining_input_budget * 1_000_000 / max(route.input_price_per_million, 1e-12)
        )
        raw_target = min(cache.total_tokens, cached_tokens + affordable_uncached_tokens)
        non_compactable_tokens = cache.total_tokens - cache.compactable_tokens
        target_tokens = max(non_compactable_tokens, raw_target)
        required_reduction = max(0, cache.total_tokens - target_tokens)
        estimated_cost = (
            fixed_cached_cost
            + max(0, target_tokens - cached_tokens) * route.input_price_per_million / 1_000_000
        )
        return CompactionPlan(
            input_budget_usd=budget.input_cost_usd,
            estimated_input_cost_usd=estimated_cost,
            target_tokens=target_tokens,
            required_reduction_tokens=required_reduction,
            compactable_message_indices=cache.compactable_message_indices,
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
    """Reduce only message indices explicitly authorized by cache analysis."""

    def __init__(
        self,
        store: InMemoryRetrievalStore | None = None,
        *,
        minimum_kept_tokens: int = 32,
    ) -> None:
        if minimum_kept_tokens <= 0:
            raise ValueError("minimum_kept_tokens must be positive")
        self._store = store if store is not None else InMemoryRetrievalStore()
        self._minimum_kept_tokens = minimum_kept_tokens

    @property
    def store(self) -> InMemoryRetrievalStore:
        return self._store

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
        eligible = sorted(
            plan.compactable_message_indices,
            key=lambda index: (-cache.message_tokens[index], index),
        )
        for index in eligible:
            if remaining <= 0:
                break
            content = transformed[index].get("content")
            if not isinstance(content, str):
                continue
            before = estimate_tokens(transformed[index])
            maximum_reduction = max(0, before - self._minimum_kept_tokens)
            requested_reduction = min(remaining, maximum_reduction)
            if requested_reduction <= 0:
                continue
            target_content_tokens = max(
                self._minimum_kept_tokens,
                estimate_tokens(content) - requested_reduction,
            )
            retrieval_id = self._store.put(session_id=session_id, content=content)
            original_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            compacted = self._compact_text(
                content,
                target_tokens=target_content_tokens,
                retrieval_id=retrieval_id,
                original_hash=original_hash,
            )
            transformed[index]["content"] = compacted
            after = estimate_tokens(transformed[index])
            if after >= before:
                transformed[index] = copy.deepcopy(original[index])
                self._store.delete(session_id=session_id, retrieval_id=retrieval_id)
                continue
            actual_reduction = before - after
            remaining = max(0, remaining - actual_reduction)
            records.append(
                CompactionRecord(
                    message_index=index,
                    retrieval_id=retrieval_id,
                    original_hash=original_hash,
                    tokens_before=before,
                    tokens_after=after,
                )
            )

        self._validate_boundaries(original, tuple(transformed), cache, plan)
        tokens_after = sum(estimate_tokens(message) for message in transformed)
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
        retrieval_id: str,
        original_hash: str,
    ) -> str:
        marker = (
            f"\n\n[PromptRail compacted content; retrieval_id={retrieval_id}; "
            f"sha256={original_hash}]\n\n"
        )
        content_budget = max(48, target_tokens * 4 - len(marker))
        head_length = max(24, math.floor(content_budget * 0.7))
        tail_length = max(24, content_budget - head_length)
        if head_length + tail_length >= len(content):
            return content
        return content[:head_length].rstrip() + marker + content[-tail_length:].lstrip()

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
            if index in cache.cacheable_message_indices and changed:
                raise CompactionError(f"compaction invalidated cacheable message index {index}")
            if index in cache.protected_message_indices and changed:
                raise CompactionError(f"compaction changed protected message index {index}")
