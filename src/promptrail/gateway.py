"""Composition root for one PromptRail agent run."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .cache import PromptCacheCoordinator, estimate_tokens
from .compaction import CacheScopedCompactor, CompactionTargetPlanner
from .controller import GlobalController
from .errors import BudgetError
from .models import (
    CallIntent,
    ModelCandidate,
    ModelUsage,
    OperatingPolicy,
    PreparedCall,
    ProviderRoute,
    RunSnapshot,
    RunStatus,
)
from .policy import EnterprisePolicyAgent, SuppliedPolicyAgent
from .routing import CacheAwareLeRouter, ProviderDeadlineAllocator


@dataclass(frozen=True)
class _RunConfiguration:
    policy: OperatingPolicy
    candidates: tuple[ModelCandidate, ...]


class PromptRailGateway:
    """Coordinate policy, budgets, cache, model choice, providers, and context."""

    def __init__(
        self,
        *,
        policy_agent: EnterprisePolicyAgent | SuppliedPolicyAgent,
        controller: GlobalController | None = None,
        cache: PromptCacheCoordinator | None = None,
        model_router: CacheAwareLeRouter | None = None,
        provider_allocator: ProviderDeadlineAllocator | None = None,
        compaction_planner: CompactionTargetPlanner | None = None,
        compactor: CacheScopedCompactor | None = None,
    ) -> None:
        self._policy_agent = policy_agent
        self.controller = controller if controller is not None else GlobalController()
        self.cache = cache if cache is not None else PromptCacheCoordinator()
        self.model_router = model_router if model_router is not None else CacheAwareLeRouter()
        self.provider_allocator = (
            provider_allocator if provider_allocator is not None else ProviderDeadlineAllocator()
        )
        self.compaction_planner = (
            compaction_planner if compaction_planner is not None else CompactionTargetPlanner()
        )
        self.compactor = compactor if compactor is not None else CacheScopedCompactor()
        self._runs: dict[str, _RunConfiguration] = {}
        self._lock = threading.RLock()

    def start_run(
        self,
        *,
        session_id: str,
        enterprise_json_paths: Sequence[str | Path],
        candidates: Sequence[ModelCandidate],
    ) -> RunSnapshot:
        candidate_tuple = tuple(candidates)
        if not candidate_tuple:
            raise ValueError("a PromptRail run requires at least one model candidate")
        policy = self._policy_agent.synthesize(enterprise_json_paths)
        snapshot = self.controller.start_run(session_id=session_id, policy=policy)
        with self._lock:
            self._runs[snapshot.run_id] = _RunConfiguration(
                policy=policy,
                candidates=candidate_tuple,
            )
        return snapshot

    async def prepare_call(self, *, run_id: str, intent: CallIntent) -> PreparedCall:
        preparation_started = time.perf_counter()
        configuration = self._configuration(run_id)
        raw_input_tokens = sum(estimate_tokens(message) for message in intent.messages)
        raw_input_tokens += sum(estimate_tokens(tool) for tool in intent.tools)
        budget = self.controller.allocate_call(
            run_id=run_id,
            task=intent.task,
            input_tokens=raw_input_tokens,
            priority=intent.priority,
            expected_remaining_calls=intent.expected_remaining_calls,
        )
        expected_future_calls = max(
            1,
            (intent.expected_remaining_calls or configuration.policy.expected_llm_calls) - 1,
        )
        try:
            cache = self.cache.analyze(
                session_id=intent.session_id,
                messages=intent.messages,
                candidates=configuration.candidates,
                predicted_output_tokens=intent.predicted_output_tokens,
                expected_future_calls=expected_future_calls,
            )
            decision = self.model_router.select(
                intent=intent,
                candidates=configuration.candidates,
                cache=cache,
                budget=budget,
                policy=configuration.policy,
            )
            compaction_plan = self.compaction_planner.plan(
                cache=cache,
                decision=decision,
                budget=budget,
            )
            provider_future = asyncio.to_thread(
                self.provider_allocator.plan,
                decision=decision,
                cache=cache,
                budget=budget,
                policy=configuration.policy,
                preparation_elapsed_ms=math.floor(
                    (time.perf_counter() - preparation_started) * 1_000
                ),
            )
            compaction_future = asyncio.to_thread(
                self.compactor.compact,
                session_id=intent.session_id,
                messages=intent.messages,
                cache=cache,
                plan=compaction_plan,
            )
            _, compaction = await asyncio.gather(provider_future, compaction_future)
            preparation_latency_ms = math.ceil((time.perf_counter() - preparation_started) * 1_000)
            provider = self.provider_allocator.plan(
                decision=decision,
                cache=cache,
                budget=budget,
                policy=configuration.policy,
                preparation_elapsed_ms=preparation_latency_ms,
            )
            return PreparedCall(
                intent=intent,
                budget=budget,
                cache=cache,
                model=decision,
                provider=provider,
                compaction=compaction,
                preparation_latency_ms=preparation_latency_ms,
            )
        except BaseException:
            self.controller.fail_call(
                run_id=run_id,
                call_id=budget.call_id,
                billing_unknown=False,
            )
            raise

    def prepare_call_sync(self, *, run_id: str, intent: CallIntent) -> PreparedCall:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.prepare_call(run_id=run_id, intent=intent))
        raise RuntimeError("prepare_call_sync cannot run inside an active event loop")

    def observe_model(self, *, prepared: PreparedCall, usage: ModelUsage) -> RunSnapshot:
        if usage.model_id != prepared.model.candidate.model_id:
            raise BudgetError("provider usage model does not match the authorized model")
        authorized_providers = {route.provider for route in prepared.provider.routes}
        if usage.provider not in authorized_providers:
            raise BudgetError("provider usage came from a route outside the authorized plan")
        route = next(
            route for route in prepared.provider.routes if route.provider == usage.provider
        )
        cost = usage.cost_usd
        if cost is None:
            cost = self._usage_cost(usage, route)
        snapshot = self.controller.settle_call(
            run_id=prepared.budget.run_id,
            call_id=prepared.budget.call_id,
            cost_usd=cost,
            latency_ms=usage.latency_ms + prepared.preparation_latency_ms,
        )
        self.cache.observe(
            session_id=prepared.intent.session_id,
            analysis=prepared.cache,
            route=route,
            usage=usage,
        )
        return snapshot

    def fail_model(self, *, prepared: PreparedCall, billing_unknown: bool = True) -> RunSnapshot:
        return self.controller.fail_call(
            run_id=prepared.budget.run_id,
            call_id=prepared.budget.call_id,
            billing_unknown=billing_unknown,
        )

    def observe_tool(self, *, run_id: str) -> RunSnapshot:
        return self.controller.observe_tool(run_id=run_id)

    def finish_run(self, *, run_id: str, success: bool) -> RunSnapshot:
        snapshot = self.controller.finish_run(
            run_id=run_id,
            status=RunStatus.COMPLETED if success else RunStatus.FAILED,
        )
        with self._lock:
            self._runs.pop(run_id, None)
        self.compactor.store.delete_session(snapshot.session_id)
        return snapshot

    def policy(self, run_id: str) -> OperatingPolicy:
        return self._configuration(run_id).policy

    def _configuration(self, run_id: str) -> _RunConfiguration:
        with self._lock:
            configuration = self._runs.get(run_id)
        if configuration is None:
            raise BudgetError(f"unknown PromptRail run configuration: {run_id}")
        return configuration

    @staticmethod
    def _usage_cost(usage: ModelUsage, route: ProviderRoute) -> float:
        read_tokens = min(usage.input_tokens, usage.cache_read_tokens)
        remaining = max(0, usage.input_tokens - read_tokens)
        write_tokens = min(remaining, usage.cache_write_tokens)
        uncached_tokens = max(0, remaining - write_tokens)
        read_rate = (
            route.cache_read_price_per_million
            if route.cache_read_price_per_million is not None
            else route.input_price_per_million
        )
        write_rate = (
            route.cache_write_price_per_million
            if route.cache_write_price_per_million is not None
            else route.input_price_per_million
        )
        cost = (
            read_tokens * read_rate
            + write_tokens * write_rate
            + uncached_tokens * route.input_price_per_million
            + usage.output_tokens * route.output_price_per_million
        ) / 1_000_000
        if not math.isfinite(cost):
            raise BudgetError("computed provider cost is not finite")
        return cost
