"""Cache-aware LeRouter scoring and deterministic provider deadline allocation."""

from __future__ import annotations

import math
from typing import Protocol

from .errors import RoutingError
from .models import (
    CacheAnalysis,
    CacheValue,
    CallBudget,
    CallIntent,
    ModelCandidate,
    ModelDecision,
    OperatingPolicy,
    ProviderRoute,
    ProviderRoutingMode,
    ProviderRoutingPlan,
)


class LeRouterRanker(Protocol):
    """Return LeRouter's request-specific score for every candidate model."""

    def rank(
        self,
        *,
        intent: CallIntent,
        candidates: tuple[ModelCandidate, ...],
    ) -> dict[str, float]: ...


class SuppliedLeRouterRanker:
    """Use explicit scores supplied by a tested LeRouter caller."""

    def rank(
        self,
        *,
        intent: CallIntent,
        candidates: tuple[ModelCandidate, ...],
    ) -> dict[str, float]:
        del intent
        missing = [candidate.model_id for candidate in candidates if candidate.router_score is None]
        if missing:
            raise RoutingError(
                "LeRouter scores are required for every candidate: " + ", ".join(missing)
            )
        return {candidate.model_id: float(candidate.router_score) for candidate in candidates}


class CacheAwareLeRouter:
    """Apply cache economics and hard budgets around LeRouter candidate scores."""

    def __init__(self, ranker: LeRouterRanker | None = None) -> None:
        self._ranker = ranker if ranker is not None else SuppliedLeRouterRanker()

    def select(
        self,
        *,
        intent: CallIntent,
        candidates: tuple[ModelCandidate, ...],
        cache: CacheAnalysis,
        budget: CallBudget,
        policy: OperatingPolicy,
    ) -> ModelDecision:
        if not candidates:
            raise RoutingError("LeRouter received no model candidates")
        decisions: list[ModelDecision] = []
        rejection_reasons: list[str] = []
        router_scores = self._ranker.rank(intent=intent, candidates=candidates)
        expected_ids = {candidate.model_id for candidate in candidates}
        if set(router_scores) != expected_ids:
            raise RoutingError("LeRouter returned an incomplete or foreign candidate score set")
        minimum_quality = self._minimum_quality(intent.task, policy)

        for candidate in candidates:
            if candidate.quality < minimum_quality:
                rejection_reasons.append(f"{candidate.model_id}: quality below policy minimum")
                continue
            if not intent.required_capabilities.issubset(candidate.capabilities):
                rejection_reasons.append(f"{candidate.model_id}: required capabilities missing")
                continue
            if (
                cache.total_tokens + intent.predicted_output_tokens
                > candidate.context_window_tokens
            ):
                rejection_reasons.append(f"{candidate.model_id}: context window exceeded")
                continue
            value = cache.values.get(candidate.model_id)
            if value is None:
                rejection_reasons.append(f"{candidate.model_id}: cache economics unavailable")
                continue
            route = self._route_for_value(candidate, value)
            predicted_cost = (
                value.input_cost_usd
                + intent.predicted_output_tokens * route.output_price_per_million / 1_000_000
            )
            predicted_latency = (
                route.p95_total_latency_ms
                + policy.controller_reserve_ms
                + policy.compaction_reserve_ms
                + policy.latency_safety_margin_ms
            )
            if predicted_cost > budget.cost_usd + 1e-12:
                rejection_reasons.append(
                    f"{candidate.model_id}: predicted cost exceeds call budget"
                )
                continue
            if predicted_latency > budget.latency_ms:
                rejection_reasons.append(
                    f"{candidate.model_id}: predicted latency exceeds call budget"
                )
                continue

            base_score = router_scores[candidate.model_id]
            if not math.isfinite(base_score):
                raise RoutingError(f"LeRouter returned a non-finite score for {candidate.model_id}")
            cost_ratio = predicted_cost / max(budget.cost_usd, 1e-12)
            latency_ratio = predicted_latency / max(budget.latency_ms, 1)
            cache_ratio = value.retained_value_usd / max(budget.cost_usd, 1e-12)
            switch_ratio = value.switch_cost_usd / max(budget.cost_usd, 1e-12)
            score = (
                policy.quality_priority * base_score
                - policy.cost_priority * cost_ratio
                - policy.latency_priority * latency_ratio
                + policy.cache_priority * cache_ratio
                - policy.cache_priority * switch_ratio
            )
            reasons = [
                f"base_router_score={base_score:.6f}",
                f"predicted_cost_usd={predicted_cost:.8f}",
                f"predicted_latency_ms={predicted_latency}",
            ]
            if value.exact_reuse:
                reasons.append(f"preserves {value.cached_tokens} cached tokens on {value.provider}")
            elif value.switch_cost_usd > 0:
                reasons.append(f"cache_switch_cost_usd={value.switch_cost_usd:.8f}")
            decisions.append(
                ModelDecision(
                    candidate=candidate,
                    route=route,
                    score=score,
                    base_router_score=base_score,
                    predicted_cost_usd=predicted_cost,
                    predicted_latency_ms=predicted_latency,
                    cache_value=value,
                    reasons=tuple(reasons),
                )
            )

        if not decisions:
            detail = "; ".join(rejection_reasons[:8])
            raise RoutingError(f"no model satisfies the call budget and capabilities: {detail}")
        return max(
            decisions,
            key=lambda item: (
                item.score,
                item.base_router_score,
                -item.predicted_cost_usd,
                item.candidate.model_id,
            ),
        )

    @staticmethod
    def _route_for_value(candidate: ModelCandidate, value: CacheValue) -> ProviderRoute:
        route = next((item for item in candidate.routes if item.provider == value.provider), None)
        if route is None:
            raise RoutingError(
                f"cache manager selected unavailable provider {value.provider!r} "
                f"for {candidate.model_id}"
            )
        return route

    @staticmethod
    def _minimum_quality(task: str, policy: OperatingPolicy) -> float:
        normalized = task.casefold()
        matched = [
            rule.minimum_quality
            for rule in policy.task_rules
            if rule.minimum_quality is not None
            and rule.match_terms
            and any(term in normalized for term in rule.match_terms)
        ]
        return max([policy.minimum_quality, *matched])


class ProviderDeadlineAllocator:
    """Turn a call-level latency budget into an opportunistic provider window."""

    def plan(
        self,
        *,
        decision: ModelDecision,
        cache: CacheAnalysis,
        budget: CallBudget,
        policy: OperatingPolicy,
        preparation_elapsed_ms: int = 0,
    ) -> ProviderRoutingPlan:
        routes = decision.candidate.routes
        if not routes:
            raise RoutingError("selected model has no provider routes")

        sticky = (
            cache.last_model_id == decision.candidate.model_id and cache.last_provider is not None
        )
        if sticky:
            route = next((item for item in routes if item.provider == cache.last_provider), None)
            if route is not None:
                return ProviderRoutingPlan(
                    mode=ProviderRoutingMode.STICKY,
                    model_id=decision.candidate.model_id,
                    routes=(route,),
                    start_within_ms=0,
                    total_timeout_ms=self._execution_budget(
                        budget,
                        policy,
                        preparation_elapsed_ms,
                    ),
                    latency_budget_ms=budget.latency_ms,
                    reason=(
                        "same model as previous session call; provider pinned for cache locality"
                    ),
                )

        cheapest = min(
            routes,
            key=lambda route: (
                self._route_cost(route, decision, cache),
                route.p95_total_latency_ms,
                route.route_id,
            ),
        )
        guaranteed_pool = tuple(route for route in routes if route.guaranteed) or routes
        guaranteed = min(
            guaranteed_pool,
            key=lambda route: (route.p95_total_latency_ms, route.p95_ttft_ms, route.route_id),
        )
        execution_budget = self._execution_budget(
            budget,
            policy,
            preparation_elapsed_ms,
        )
        slack = max(0, execution_budget - guaranteed.p95_total_latency_ms)
        start_within_ms = min(
            policy.maximum_provider_exploration_ms,
            math.floor(slack * policy.provider_exploration_fraction),
        )
        if cheapest.route_id == guaranteed.route_id or start_within_ms <= 0:
            return ProviderRoutingPlan(
                mode=ProviderRoutingMode.DIRECT,
                model_id=decision.candidate.model_id,
                routes=(guaranteed,),
                start_within_ms=0,
                total_timeout_ms=execution_budget,
                latency_budget_ms=budget.latency_ms,
                reason=(
                    "cheapest provider is already the guaranteed route"
                    if cheapest.route_id == guaranteed.route_id
                    else "call latency budget has no safe provider-exploration slack"
                ),
            )

        ordered = [cheapest]
        ordered.extend(
            route
            for route in sorted(
                routes,
                key=lambda item: (item.p95_total_latency_ms, item.route_id),
            )
            if route.route_id not in {cheapest.route_id, guaranteed.route_id}
        )
        ordered.append(guaranteed)
        return ProviderRoutingPlan(
            mode=ProviderRoutingMode.DEADLINE,
            model_id=decision.candidate.model_id,
            routes=tuple(ordered),
            start_within_ms=start_within_ms,
            total_timeout_ms=execution_budget,
            latency_budget_ms=budget.latency_ms,
            reason=(
                f"allocated {start_within_ms}ms of {budget.latency_ms}ms to the cheapest "
                f"provider before escalating to {guaranteed.provider}"
            ),
        )

    @staticmethod
    def _execution_budget(
        budget: CallBudget,
        policy: OperatingPolicy,
        preparation_elapsed_ms: int,
    ) -> int:
        if preparation_elapsed_ms < 0:
            raise ValueError("preparation_elapsed_ms cannot be negative")
        observed_or_reserved_preparation = max(
            preparation_elapsed_ms,
            policy.controller_reserve_ms + policy.compaction_reserve_ms,
        )
        available = (
            budget.latency_ms - observed_or_reserved_preparation - policy.latency_safety_margin_ms
        )
        if available <= 0:
            raise RoutingError("call latency budget was consumed before provider execution")
        return available

    @staticmethod
    def _route_cost(
        route: ProviderRoute,
        decision: ModelDecision,
        cache: CacheAnalysis,
    ) -> float:
        if route.provider == decision.cache_value.provider:
            input_cost = decision.cache_value.input_cost_usd
        else:
            input_cost = cache.total_tokens * route.input_price_per_million / 1_000_000
        selected_output_cost = max(
            0.0,
            decision.predicted_cost_usd - decision.cache_value.input_cost_usd,
        )
        if decision.route.output_price_per_million > 0:
            predicted_output_tokens = (
                selected_output_cost * 1_000_000 / decision.route.output_price_per_million
            )
        else:
            predicted_output_tokens = 0.0
        return input_cost + predicted_output_tokens * route.output_price_per_million / 1_000_000
