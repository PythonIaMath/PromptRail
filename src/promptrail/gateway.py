"""Composition root for one PromptRail agent run."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .budgeting import CallBudgetAllocator
from .cache import PromptCacheCoordinator, estimate_tokens
from .clients import LeRouterHTTPRanker, LeRouterOutputLengthPredictor
from .compaction import CacheScopedCompactor, CompactionTargetPlanner
from .controller import GlobalController
from .errors import BudgetError, CompactionError, RoutingError
from .models import (
    CallIntent,
    ModelCandidate,
    ModelUsage,
    OperatingPolicy,
    OutputLengthPrediction,
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
        budget_allocator: CallBudgetAllocator | None = None,
        controller: GlobalController | None = None,
        cache: PromptCacheCoordinator | None = None,
        model_router: CacheAwareLeRouter | None = None,
        provider_allocator: ProviderDeadlineAllocator | None = None,
        compaction_planner: CompactionTargetPlanner | None = None,
        compactor: CacheScopedCompactor | None = None,
        output_predictor: LeRouterOutputLengthPredictor | None = None,
    ) -> None:
        self._policy_agent = policy_agent
        if controller is not None and budget_allocator is not None:
            raise ValueError("supply either controller or budget_allocator, not both")
        if controller is None:
            if budget_allocator is None:
                raise ValueError("PromptRailGateway requires a Gemma 12B budget_allocator")
            controller = GlobalController(allocator=budget_allocator)
        self.controller = controller
        self.cache = cache if cache is not None else PromptCacheCoordinator()
        self.model_router = (
            model_router
            if model_router is not None
            else CacheAwareLeRouter(LeRouterHTTPRanker.from_env())
        )
        self.provider_allocator = (
            provider_allocator if provider_allocator is not None else ProviderDeadlineAllocator()
        )
        self.compactor = compactor if compactor is not None else CacheScopedCompactor()
        self.compaction_planner = (
            compaction_planner
            if compaction_planner is not None
            else CompactionTargetPlanner(minimum_kept_tokens=self.compactor.minimum_kept_tokens)
        )
        self.output_predictor = output_predictor
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
        if len({candidate.model_id for candidate in candidate_tuple}) != len(candidate_tuple):
            raise ValueError("PromptRail model candidate IDs must be unique")
        policy = self._policy_agent.synthesize(enterprise_json_paths)
        snapshot = self.controller.start_run(session_id=session_id, policy=policy)
        with self._lock:
            self._runs[snapshot.run_id] = _RunConfiguration(
                policy=policy,
                candidates=candidate_tuple,
            )
        return snapshot

    async def prepare_call(
        self,
        *,
        run_id: str,
        intent: CallIntent,
    ) -> PreparedCall:
        preparation_started = time.perf_counter()
        configuration = self._configuration(run_id)
        candidates = configuration.candidates
        prediction_task = asyncio.create_task(asyncio.to_thread(self._predict_output, intent))
        context_task = asyncio.create_task(asyncio.to_thread(self._analyze_context, intent))
        ranking_task = asyncio.create_task(
            asyncio.to_thread(
                self._rank_candidates,
                intent,
                candidates,
                configuration.policy.hard_call_latency_limit_ms or 3_000,
            )
        )
        try:
            prediction, context_result = await asyncio.gather(prediction_task, context_task)
        except BaseException:
            await asyncio.gather(
                prediction_task,
                context_task,
                ranking_task,
                return_exceptions=True,
            )
            raise
        context_blocks, context_analysis_ms = context_result
        resolved_intent = intent.model_copy(
            update={"predicted_output_tokens": prediction.predicted_tokens}
        )
        raw_input_tokens = sum(estimate_tokens(message) for message in resolved_intent.messages)
        raw_input_tokens += sum(estimate_tokens(tool) for tool in resolved_intent.tools)
        cache = self.cache.analyze(
            session_id=resolved_intent.session_id,
            messages=resolved_intent.messages,
            tools=resolved_intent.tools,
            candidates=candidates,
            predicted_output_tokens=prediction.predicted_tokens,
            task=resolved_intent.task,
            context_blocks=context_blocks,
        )
        allocation_started = time.perf_counter()
        allocation_task = asyncio.create_task(
            asyncio.to_thread(
                self.controller.allocate_call,
                run_id=run_id,
                task=resolved_intent.task,
                input_tokens=raw_input_tokens,
                predicted_output_tokens=prediction.predicted_tokens,
                cache=cache,
                candidates=candidates,
                priority=resolved_intent.priority,
                required_capabilities=resolved_intent.required_capabilities,
            )
        )
        try:
            budget = await allocation_task
        except BaseException:
            await asyncio.gather(ranking_task, return_exceptions=True)
            raise
        minimum_reachable_context = self.compaction_planner.minimum_reachable_tokens(cache)
        if budget.required_context_tokens < minimum_reachable_context:
            budget = budget.model_copy(
                update={
                    "required_context_tokens": minimum_reachable_context,
                    "allocator_reason": (
                        f"{budget.allocator_reason} Physical context floor raised to "
                        f"{minimum_reachable_context} tokens to preserve structurally protected "
                        "messages."
                    ),
                }
            )
        gemma_allocation_ms = math.ceil((time.perf_counter() - allocation_started) * 1_000)
        try:
            router_scores, semantic_ranking_ms = await ranking_task
            feasibility_started = time.perf_counter()
            ranked_decisions = await asyncio.to_thread(
                self.model_router.select_ranked,
                intent=resolved_intent,
                candidates=candidates,
                cache=cache,
                budget=budget,
                policy=configuration.policy,
                preparation_elapsed_ms=math.floor(
                    (time.perf_counter() - preparation_started) * 1_000
                ),
                router_scores=router_scores,
            )
            decision = ranked_decisions[0]
            candidate_feasibility_ms = math.ceil(
                (time.perf_counter() - feasibility_started) * 1_000
            )
            compaction_plan = self.compaction_planner.plan(
                cache=cache,
                decision=decision,
                budget=budget,
            )
            provider_future = asyncio.to_thread(
                self._plan_provider_timed,
                decision,
                cache,
                budget,
                configuration.policy,
                math.floor((time.perf_counter() - preparation_started) * 1_000),
            )
            compaction_future = asyncio.to_thread(
                self._compact_timed,
                resolved_intent,
                cache,
                compaction_plan,
            )
            provider_result, compaction_result = await asyncio.gather(
                provider_future, compaction_future
            )
            _, provider_planning_ms = provider_result
            compaction, compaction_ms = compaction_result
            if not compaction.target_met:
                raise CompactionError(
                    "Gemma's required context target cannot be reached without changing "
                    "structurally immutable context"
                )
            preparation_latency_ms = math.ceil((time.perf_counter() - preparation_started) * 1_000)
            provider_replan_started = time.perf_counter()
            provider = self.provider_allocator.plan(
                decision=decision,
                cache=cache,
                budget=budget,
                policy=configuration.policy,
                preparation_elapsed_ms=preparation_latency_ms,
            )
            provider_planning_ms += math.ceil(
                (time.perf_counter() - provider_replan_started) * 1_000
            )
            return PreparedCall(
                intent=resolved_intent,
                budget=budget,
                cache=cache,
                model=decision,
                provider=provider,
                compaction=compaction,
                model_alternatives=ranked_decisions[1:],
                preparation_latency_ms=preparation_latency_ms,
                predicted_output_tokens=prediction.predicted_tokens,
                output_prediction_ms=prediction.latency_ms,
                context_analysis_ms=context_analysis_ms,
                gemma_allocation_ms=gemma_allocation_ms,
                semantic_ranking_ms=semantic_ranking_ms,
                candidate_feasibility_ms=candidate_feasibility_ms,
                compaction_ms=compaction_ms,
                provider_planning_ms=provider_planning_ms,
                control_plane_total_ms=preparation_latency_ms,
            )
        except BaseException:
            self.controller.fail_call(
                run_id=run_id,
                call_id=budget.call_id,
                billing_unknown=False,
            )
            raise

    def prepare_call_sync(
        self,
        *,
        run_id: str,
        intent: CallIntent,
    ) -> PreparedCall:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.prepare_call(run_id=run_id, intent=intent))
        raise RuntimeError("prepare_call_sync cannot run inside an active event loop")

    def reroute_prepared(
        self,
        *,
        prepared: PreparedCall,
        excluded_model_ids: frozenset[str],
        additional_elapsed_ms: int = 0,
    ) -> PreparedCall:
        """Switch an active reservation to its next precomputed feasible model."""

        if additional_elapsed_ms < 0:
            raise ValueError("additional_elapsed_ms cannot be negative")
        ranked = (prepared.model, *prepared.model_alternatives)
        decision = next(
            (item for item in ranked if item.candidate.model_id not in excluded_model_ids),
            None,
        )
        if decision is None:
            raise RoutingError("no precomputed feasible model remains for provider failover")
        planning_started = time.perf_counter()
        provider = self.provider_allocator.plan(
            decision=decision,
            cache=prepared.cache,
            budget=prepared.budget,
            policy=self.policy(prepared.budget.run_id),
            preparation_elapsed_ms=prepared.preparation_latency_ms + additional_elapsed_ms,
        )
        planning_ms = math.ceil((time.perf_counter() - planning_started) * 1_000)
        remaining = tuple(
            item
            for item in ranked
            if item.candidate.model_id != decision.candidate.model_id
            and item.candidate.model_id not in excluded_model_ids
        )
        return prepared.model_copy(
            update={
                "model": decision,
                "model_alternatives": remaining,
                "provider": provider,
                "provider_planning_ms": prepared.provider_planning_ms + planning_ms,
            }
        )

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
        compacted_indices = {record.message_index for record in prepared.compaction.records}
        cache_observation = prepared.cache
        cache_usage = usage
        if compacted_indices:
            configuration = self._configuration(prepared.budget.run_id)
            cache_observation = self.cache.analyze(
                session_id=prepared.intent.session_id,
                messages=prepared.compaction.messages,
                tools=prepared.intent.tools,
                candidates=configuration.candidates,
                predicted_output_tokens=prepared.predicted_output_tokens,
                task=prepared.intent.task,
            )
        if compacted_indices.intersection(prepared.cache.cacheable_message_indices):
            # A read of the old prefix cannot prove that the rewritten prefix was stored.
            # Preserve a provider-reported write, which does establish the new prefix.
            cache_usage = usage.model_copy(update={"cache_read_tokens": 0})
        self.cache.observe(
            session_id=prepared.intent.session_id,
            analysis=cache_observation,
            route=route,
            usage=cache_usage,
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
        return snapshot

    def close(self) -> None:
        predictor = self.output_predictor
        if predictor is not None:
            predictor.close()
            self.output_predictor = None

    def _predict_output(self, intent: CallIntent) -> OutputLengthPrediction:
        # Explicit values are an injection seam for deterministic tests and callers that
        # already ran the same LeRouter predictor; production demo calls leave it unset.
        if intent.predicted_output_tokens is not None:
            predicted = min(
                intent.predicted_output_tokens,
                intent.max_output_tokens or intent.predicted_output_tokens,
            )
            return OutputLengthPrediction(
                predicted_tokens=predicted,
                raw_predicted_tokens=float(intent.predicted_output_tokens),
                latency_ms=0,
                model_id="precomputed-lerouter-prediction",
            )
        predictor = self.output_predictor
        if predictor is None:
            predictor = LeRouterOutputLengthPredictor.from_env()
            self.output_predictor = predictor
        return predictor.predict(
            messages=intent.messages,
            max_output_tokens=intent.max_output_tokens,
        )

    @staticmethod
    def _analyze_context(intent: CallIntent) -> tuple[tuple, int]:
        started = time.perf_counter()
        blocks = PromptCacheCoordinator.build_context_blocks(
            messages=intent.messages,
            task=intent.task,
        )
        return blocks, math.ceil((time.perf_counter() - started) * 1_000)

    def _rank_candidates(
        self,
        intent: CallIntent,
        candidates: tuple[ModelCandidate, ...],
        timeout_ms: int,
    ) -> tuple[dict[str, float], int]:
        started = time.perf_counter()
        scores = self.model_router.rank_candidates(
            intent=intent,
            candidates=candidates,
            timeout_ms=timeout_ms,
        )
        return scores, math.ceil((time.perf_counter() - started) * 1_000)

    def _plan_provider_timed(
        self,
        decision,
        cache,
        budget,
        policy,
        preparation_elapsed_ms: int,
    ) -> tuple[object, int]:
        started = time.perf_counter()
        result = self.provider_allocator.plan(
            decision=decision,
            cache=cache,
            budget=budget,
            policy=policy,
            preparation_elapsed_ms=preparation_elapsed_ms,
        )
        return result, math.ceil((time.perf_counter() - started) * 1_000)

    def _compact_timed(self, intent, cache, plan) -> tuple[object, int]:
        started = time.perf_counter()
        result = self.compactor.compact(
            session_id=intent.session_id,
            messages=intent.messages,
            cache=cache,
            plan=plan,
        )
        return result, math.ceil((time.perf_counter() - started) * 1_000)

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
