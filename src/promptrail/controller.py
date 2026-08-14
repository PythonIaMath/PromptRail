"""Gemma-managed per-call budgets with deterministic reservation and settlement."""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from .budgeting import GEMMA_12B_MODEL_ID, CallBudgetAllocator
from .errors import BudgetError
from .models import (
    BudgetAllocationDecision,
    BudgetAllocationRequest,
    BudgetCandidateOption,
    CacheAnalysis,
    CallBudget,
    ModelCandidate,
    OperatingPolicy,
    RunSnapshot,
    RunStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class _Run:
    run_id: str
    session_id: str
    policy: OperatingPolicy
    status: RunStatus = RunStatus.ACTIVE
    completed_calls: int = 0
    spent_cost_usd: float = 0.0
    spent_model_latency_ms: int = 0
    reserved: dict[str, CallBudget] = field(default_factory=dict)
    pending_allocations: set[str] = field(default_factory=set)
    next_sequence: int = 1
    tool_calls: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_monotonic: float = field(default_factory=time.perf_counter)
    finished_at: datetime | None = None


class GlobalController:
    """Ask Gemma for each budget, then enforce reservations and optional caps."""

    def __init__(
        self,
        *,
        allocator: CallBudgetAllocator,
        required_allocator_model_id: str = GEMMA_12B_MODEL_ID,
    ) -> None:
        model_id = allocator.model_id.strip()
        if model_id != required_allocator_model_id:
            raise ValueError(
                "GlobalController requires the pinned Gemma 12B allocator "
                f"{required_allocator_model_id!r}, got {model_id!r}"
            )
        self._allocator = allocator
        self._allocator_model_id = model_id
        self._runs: dict[str, _Run] = {}
        self._lock = threading.RLock()

    def start_run(self, *, session_id: str, policy: OperatingPolicy) -> RunSnapshot:
        """Open an accounting scope; this does not imply a fixed call graph."""

        if not session_id.strip():
            raise ValueError("session_id cannot be empty")
        run = _Run(run_id=f"run_{uuid4().hex}", session_id=session_id, policy=policy)
        with self._lock:
            self._runs[run.run_id] = run
            return self._snapshot(run)

    def allocate_call(
        self,
        *,
        run_id: str,
        task: str,
        input_tokens: int,
        predicted_output_tokens: int,
        cache: CacheAnalysis,
        candidates: tuple[ModelCandidate, ...],
        priority: float = 1.0,
        required_capabilities: frozenset[str] = frozenset(),
    ) -> CallBudget:
        if not task.strip():
            raise ValueError("task cannot be empty")
        if input_tokens < 0:
            raise ValueError("input_tokens cannot be negative")
        if isinstance(predicted_output_tokens, bool) or predicted_output_tokens <= 0:
            raise ValueError("predicted_output_tokens must be positive")
        if not math.isfinite(float(priority)) or priority <= 0:
            raise ValueError("priority must be finite and positive")
        if not candidates:
            raise ValueError("Gemma allocation requires model candidate evidence")
        if input_tokens != cache.total_tokens:
            raise ValueError("cache analysis does not cover the complete call input")

        with self._lock:
            run = self._active_run(run_id)
            reserved_cost, reserved_latency = self._reserved_totals(run)
            remaining_cost = self._remaining_cost(run, reserved_cost)
            remaining_latency = self._remaining_latency(run)
            if remaining_cost is not None and remaining_cost <= 0:
                raise BudgetError("optional hard agent cost limit is exhausted")
            if remaining_latency is not None and remaining_latency <= 0:
                raise BudgetError("optional hard agent latency limit is exhausted")

            call_id = f"call_{uuid4().hex}"
            sequence = run.next_sequence
            run.next_sequence += 1
            run.pending_allocations.add(call_id)
            request = BudgetAllocationRequest(
                run_id=run.run_id,
                session_id=run.session_id,
                call_id=call_id,
                sequence=sequence,
                analytics_insight=run.policy.analytics_insight,
                source_digest=run.policy.source_digest,
                task_rules=run.policy.task_rules,
                cost_priority=run.policy.cost_priority,
                latency_priority=run.policy.latency_priority,
                quality_priority=run.policy.quality_priority,
                cache_priority=run.policy.cache_priority,
                task=task.strip(),
                input_tokens=input_tokens,
                predicted_output_tokens=predicted_output_tokens,
                priority=float(priority),
                required_capabilities=tuple(sorted(required_capabilities)),
                completed_calls=run.completed_calls,
                tool_calls=run.tool_calls,
                elapsed_agent_ms=self._elapsed_ms(run),
                spent_cost_usd=run.spent_cost_usd,
                spent_latency_ms=run.spent_model_latency_ms,
                reserved_cost_usd=reserved_cost,
                reserved_latency_ms=reserved_latency,
                hard_agent_cost_limit_usd=run.policy.hard_agent_cost_limit_usd,
                hard_agent_latency_limit_ms=run.policy.hard_agent_latency_limit_ms,
                hard_call_cost_limit_usd=run.policy.hard_call_cost_limit_usd,
                hard_call_latency_limit_ms=run.policy.hard_call_latency_limit_ms,
                remaining_hard_cost_usd=remaining_cost,
                remaining_hard_latency_ms=remaining_latency,
                cacheable_tokens=cache.cacheable_tokens,
                compactable_tokens=cache.compactable_tokens,
                last_model_id=cache.last_model_id,
                last_provider=cache.last_provider,
                cache_input_cost_usd_by_model={
                    model_id: value.input_cost_usd for model_id, value in cache.values.items()
                },
                exact_cache_models=tuple(
                    sorted(
                        model_id for model_id, value in cache.values.items() if value.exact_reuse
                    )
                ),
                candidate_options=self._candidate_options(
                    policy=run.policy,
                    candidates=candidates,
                    cache=cache,
                    predicted_output_tokens=predicted_output_tokens,
                    required_capabilities=required_capabilities,
                ),
                context_blocks=cache.context_blocks,
            )

        try:
            decision = self._allocator.allocate(request)
        except BaseException:
            self._discard_pending(run_id=run_id, call_id=call_id)
            raise

        with self._lock:
            run = self._run(run_id)
            run.pending_allocations.discard(call_id)
            if run.status is not RunStatus.ACTIVE:
                raise BudgetError(f"agent run became {run.status.value} during allocation")
            reserved_cost, reserved_latency = self._reserved_totals(run)
            remaining_cost = self._remaining_cost(run, reserved_cost)
            remaining_latency = self._remaining_latency(run)
            self._validate_decision(
                policy=run.policy,
                decision=decision,
                remaining_cost=remaining_cost,
                remaining_latency=remaining_latency,
                input_tokens=input_tokens,
                cache=cache,
            )
            input_cost = decision.cost_usd * decision.input_cost_fraction
            budget = CallBudget(
                run_id=run_id,
                call_id=call_id,
                sequence=sequence,
                cost_usd=decision.cost_usd,
                input_cost_usd=input_cost,
                output_cost_usd=decision.cost_usd - input_cost,
                latency_ms=decision.latency_ms,
                allocator_model_id=self._allocator_model_id,
                allocator_reason=decision.reason,
                required_context_tokens=decision.required_context_tokens,
                importance_overrides=decision.importance_overrides,
            )
            run.reserved[call_id] = budget
            return budget

    def settle_call(
        self,
        *,
        run_id: str,
        call_id: str,
        cost_usd: float,
        latency_ms: int,
    ) -> RunSnapshot:
        if not math.isfinite(float(cost_usd)) or cost_usd < 0:
            raise ValueError("cost_usd must be finite and non-negative")
        if latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        with self._lock:
            run = self._active_run(run_id)
            budget = run.reserved.pop(call_id, None)
            if budget is None:
                raise BudgetError(f"unknown or already-settled call reservation: {call_id}")
            run.spent_cost_usd += float(cost_usd)
            run.spent_model_latency_ms += latency_ms
            run.completed_calls += 1

            forecast_variances: list[str] = []
            if cost_usd > budget.cost_usd + 1e-12:
                forecast_variances.append(f"cost ${cost_usd:.12g} exceeded ${budget.cost_usd:.12g}")
            if latency_ms > budget.latency_ms:
                forecast_variances.append(f"latency {latency_ms}ms exceeded {budget.latency_ms}ms")
            if forecast_variances:
                logger.warning(
                    "Gemma allocation forecast variance for run %s call %s: %s; "
                    "authoritative usage was settled and the uncapped run remains active",
                    run_id,
                    call_id,
                    "; ".join(forecast_variances),
                )

            violations: list[str] = []
            if (
                run.policy.hard_call_cost_limit_usd is not None
                and cost_usd > run.policy.hard_call_cost_limit_usd + 1e-12
            ):
                violations.append("provider usage exceeded the optional hard per-call cost limit")
            if (
                run.policy.hard_call_latency_limit_ms is not None
                and latency_ms > run.policy.hard_call_latency_limit_ms
            ):
                violations.append(
                    "provider execution exceeded the optional hard per-call latency limit"
                )
            if (
                run.policy.hard_agent_cost_limit_usd is not None
                and run.spent_cost_usd > run.policy.hard_agent_cost_limit_usd + 1e-12
            ):
                violations.append("provider usage exceeded the optional hard agent cost limit")
            if (
                run.policy.hard_agent_latency_limit_ms is not None
                and self._elapsed_ms(run) > run.policy.hard_agent_latency_limit_ms
            ):
                violations.append("agent execution exceeded its optional hard latency deadline")
            if violations:
                run.status = RunStatus.FAILED
                run.finished_at = datetime.now(UTC)
                raise BudgetError("; ".join(violations))
            return self._snapshot(run)

    def fail_call(self, *, run_id: str, call_id: str, billing_unknown: bool = True) -> RunSnapshot:
        """Release a known-unbilled call or conservatively charge an unknown one."""

        with self._lock:
            run = self._active_run(run_id)
            budget = run.reserved.pop(call_id, None)
            if budget is None:
                raise BudgetError(f"unknown or already-settled call reservation: {call_id}")
            if billing_unknown:
                run.spent_cost_usd += budget.cost_usd
                run.spent_model_latency_ms += budget.latency_ms
            run.completed_calls += 1
            return self._snapshot(run)

    def observe_tool(self, *, run_id: str) -> RunSnapshot:
        with self._lock:
            run = self._active_run(run_id)
            run.tool_calls += 1
            if (
                run.policy.hard_agent_latency_limit_ms is not None
                and self._elapsed_ms(run) > run.policy.hard_agent_latency_limit_ms
            ):
                run.status = RunStatus.FAILED
                run.finished_at = datetime.now(UTC)
                raise BudgetError("agent execution exceeded its optional hard latency deadline")
            return self._snapshot(run)

    def finish_run(self, *, run_id: str, status: RunStatus) -> RunSnapshot:
        if status is RunStatus.ACTIVE:
            raise ValueError("a finished run cannot remain active")
        with self._lock:
            run = self._run(run_id)
            if run.status is not RunStatus.ACTIVE:
                if run.status is status:
                    return self._snapshot(run)
                raise BudgetError(f"agent run is already {run.status.value}")
            if run.pending_allocations:
                raise BudgetError("cannot finish an agent run while Gemma allocations are pending")
            if run.reserved:
                unresolved = tuple(run.reserved.values())
                logger.error(
                    "finishing agent run %s with %d unsettled call reservation(s); "
                    "charging their full allocations because billing is unknown",
                    run.run_id,
                    len(unresolved),
                )
                run.spent_cost_usd += sum(item.cost_usd for item in unresolved)
                run.spent_model_latency_ms += sum(item.latency_ms for item in unresolved)
                run.completed_calls += len(unresolved)
                run.reserved.clear()
            if (
                status is RunStatus.COMPLETED
                and run.policy.hard_agent_latency_limit_ms is not None
                and self._elapsed_ms(run) > run.policy.hard_agent_latency_limit_ms
            ):
                run.status = RunStatus.FAILED
                run.finished_at = datetime.now(UTC)
                raise BudgetError("agent execution exceeded its optional hard latency deadline")
            run.status = status
            run.finished_at = datetime.now(UTC)
            return self._snapshot(run)

    def snapshot(self, run_id: str) -> RunSnapshot:
        with self._lock:
            return self._snapshot(self._run(run_id))

    def _discard_pending(self, *, run_id: str, call_id: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.pending_allocations.discard(call_id)

    @staticmethod
    def _validate_decision(
        *,
        policy: OperatingPolicy,
        decision: BudgetAllocationDecision,
        remaining_cost: float | None,
        remaining_latency: int | None,
        input_tokens: int,
        cache: CacheAnalysis,
    ) -> None:
        if decision.required_context_tokens > input_tokens:
            raise BudgetError("Gemma required_context_tokens exceeds the available input")
        block_ids = {block.block_id for block in cache.context_blocks}
        foreign = [
            item.block_id
            for item in decision.importance_overrides
            if item.block_id not in block_ids
        ]
        if foreign:
            raise BudgetError("Gemma returned an importance override for an unknown context block")
        immutable_ids = {
            block.block_id for block in cache.context_blocks if block.structurally_immutable
        }
        if any(item.block_id in immutable_ids for item in decision.importance_overrides):
            raise BudgetError("Gemma cannot override structurally immutable context")
        if (
            policy.hard_call_cost_limit_usd is not None
            and decision.cost_usd > policy.hard_call_cost_limit_usd + 1e-12
        ):
            raise BudgetError("Gemma allocation exceeds the optional hard per-call cost limit")
        if (
            policy.hard_call_latency_limit_ms is not None
            and decision.latency_ms > policy.hard_call_latency_limit_ms
        ):
            raise BudgetError("Gemma allocation exceeds the optional hard per-call latency limit")
        if remaining_cost is not None and decision.cost_usd > remaining_cost + 1e-12:
            raise BudgetError("Gemma allocation exceeds the remaining hard agent cost limit")
        if remaining_latency is not None and decision.latency_ms > remaining_latency:
            raise BudgetError("Gemma allocation exceeds the remaining hard agent latency limit")

    @staticmethod
    def _candidate_options(
        *,
        policy: OperatingPolicy,
        candidates: tuple[ModelCandidate, ...],
        cache: CacheAnalysis,
        predicted_output_tokens: int,
        required_capabilities: frozenset[str],
    ) -> tuple[BudgetCandidateOption, ...]:
        options: list[BudgetCandidateOption] = []
        for candidate in candidates:
            value = cache.values.get(candidate.model_id)
            route_costs: list[tuple[float, float]] = []
            for route in candidate.routes:
                exact = bool(
                    value
                    and value.exact_reuse
                    and value.provider == route.provider
                    and route.cache_supported
                )
                cached_tokens = min(cache.total_tokens, value.cached_tokens) if exact else 0
                cached_rate = (
                    route.cache_read_price_per_million
                    if cached_tokens and route.cache_read_price_per_million is not None
                    else route.input_price_per_million
                )
                input_cost = (
                    cached_tokens * cached_rate
                    + (cache.total_tokens - cached_tokens) * route.input_price_per_million
                ) / 1_000_000
                output_cost = (
                    predicted_output_tokens * route.output_price_per_million / 1_000_000
                )
                total_cost = input_cost + output_cost
                route_costs.append((total_cost, input_cost))
            cheapest_total_cost, cheapest_input_cost = min(
                route_costs,
                key=lambda item: item[0],
            )
            cheapest_input_fraction = (
                cheapest_input_cost / cheapest_total_cost
                if cheapest_total_cost > 0
                else cache.total_tokens / (cache.total_tokens + predicted_output_tokens)
            )
            options.append(
                BudgetCandidateOption(
                    model_id=candidate.model_id,
                    quality=candidate.quality,
                    context_fits=(
                        cache.total_tokens + predicted_output_tokens
                        <= candidate.context_window_tokens
                    ),
                    required_capabilities_supported=(
                        required_capabilities.issubset(candidate.capabilities)
                    ),
                    cheapest_predicted_cost_usd=cheapest_total_cost,
                    cheapest_input_cost_fraction=min(
                        1 - 0.000001,
                        max(0.000001, cheapest_input_fraction),
                    ),
                    fastest_predicted_latency_ms=(
                        min(route.p95_total_latency_ms for route in candidate.routes)
                        + policy.controller_reserve_ms
                        + policy.compaction_reserve_ms
                        + policy.latency_safety_margin_ms
                    ),
                    exact_cache_reuse=bool(value and value.exact_reuse),
                    cached_tokens=value.cached_tokens if value is not None else 0,
                )
            )
        return tuple(options)

    @staticmethod
    def _reserved_totals(run: _Run) -> tuple[float, int]:
        return (
            sum(item.cost_usd for item in run.reserved.values()),
            sum(item.latency_ms for item in run.reserved.values()),
        )

    @staticmethod
    def _remaining_cost(run: _Run, reserved_cost: float) -> float | None:
        limit = run.policy.hard_agent_cost_limit_usd
        if limit is None:
            return None
        return limit - run.spent_cost_usd - reserved_cost

    @classmethod
    def _remaining_latency(cls, run: _Run) -> int | None:
        limit = run.policy.hard_agent_latency_limit_ms
        if limit is None:
            return None
        return limit - cls._elapsed_ms(run)

    @staticmethod
    def _elapsed_ms(run: _Run) -> int:
        return max(0, math.ceil((time.perf_counter() - run.started_monotonic) * 1_000))

    def _run(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            raise BudgetError(f"unknown agent run: {run_id}")
        return run

    def _active_run(self, run_id: str) -> _Run:
        run = self._run(run_id)
        if run.status is not RunStatus.ACTIVE:
            raise BudgetError(f"agent run is {run.status.value}")
        return run

    @staticmethod
    def _snapshot(run: _Run) -> RunSnapshot:
        return RunSnapshot(
            run_id=run.run_id,
            session_id=run.session_id,
            status=run.status,
            completed_calls=run.completed_calls,
            spent_cost_usd=run.spent_cost_usd,
            spent_model_latency_ms=run.spent_model_latency_ms,
            reserved_cost_usd=sum(item.cost_usd for item in run.reserved.values()),
            reserved_latency_ms=sum(item.latency_ms for item in run.reserved.values()),
            tool_calls=run.tool_calls,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
