"""Deterministic workflow and per-call budget allocation."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

from .errors import BudgetError
from .models import CallBudget, OperatingPolicy, RunSnapshot, RunStatus, TaskRule


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
    tool_calls: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class GlobalController:
    """Reserve cost and latency before every model call, then settle actual usage."""

    def __init__(self, *, median_call_tokens: int = 4_000) -> None:
        if median_call_tokens <= 0:
            raise ValueError("median_call_tokens must be positive")
        self._median_call_tokens = median_call_tokens
        self._runs: dict[str, _Run] = {}
        self._lock = threading.RLock()

    def start_run(self, *, session_id: str, policy: OperatingPolicy) -> RunSnapshot:
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
        priority: float = 1.0,
        expected_remaining_calls: int | None = None,
    ) -> CallBudget:
        if input_tokens < 0:
            raise ValueError("input_tokens cannot be negative")
        with self._lock:
            run = self._active_run(run_id)
            reserved_cost = sum(item.cost_usd for item in run.reserved.values())
            reserved_latency = sum(item.latency_ms for item in run.reserved.values())
            remaining_cost = (
                run.policy.workflow_cost_budget_usd - run.spent_cost_usd - reserved_cost
            )
            remaining_latency = (
                run.policy.workflow_latency_budget_ms
                - run.spent_model_latency_ms
                - reserved_latency
            )
            if remaining_cost <= 0:
                raise BudgetError("workflow cost budget is exhausted")
            if remaining_latency <= 0:
                raise BudgetError("workflow model-latency budget is exhausted")

            remaining_calls = expected_remaining_calls
            if remaining_calls is None:
                remaining_calls = max(1, run.policy.expected_llm_calls - run.completed_calls)
            if remaining_calls <= 0:
                raise BudgetError("expected_remaining_calls must include the current call")

            rule = self._matching_rule(run.policy, task)
            size_factor = max(0.25, min(4.0, input_tokens / self._median_call_tokens))
            base_weight = max(0.05, float(priority)) * math.sqrt(size_factor)
            cost_weight = min(8.0, max(0.05, base_weight * (rule.cost_weight if rule else 1.0)))
            latency_weight = min(
                8.0,
                max(0.05, base_weight * (rule.latency_weight if rule else 1.0)),
            )
            future_calls = max(0, remaining_calls - 1)
            cost_share = cost_weight / (cost_weight + future_calls)
            latency_share = latency_weight / (latency_weight + future_calls)
            call_cost = remaining_cost * cost_share
            call_latency = max(1, math.floor(remaining_latency * latency_share))
            input_cost = call_cost * run.policy.input_cost_fraction
            call_id = f"call_{uuid4().hex}"
            budget = CallBudget(
                run_id=run_id,
                call_id=call_id,
                sequence=run.completed_calls + len(run.reserved) + 1,
                cost_usd=call_cost,
                input_cost_usd=input_cost,
                output_cost_usd=call_cost - input_cost,
                latency_ms=call_latency,
                allocation_weight=base_weight,
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
            if run.spent_cost_usd > run.policy.workflow_cost_budget_usd + 1e-12:
                run.status = RunStatus.FAILED
                raise BudgetError("authoritative provider usage exceeded the workflow cost budget")
            if run.spent_model_latency_ms > run.policy.workflow_latency_budget_ms:
                run.status = RunStatus.FAILED
                raise BudgetError(
                    "authoritative model latency exceeded the workflow latency budget"
                )
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
            return self._snapshot(run)

    def finish_run(self, *, run_id: str, status: RunStatus) -> RunSnapshot:
        if status is RunStatus.ACTIVE:
            raise ValueError("a finished run cannot remain active")
        with self._lock:
            run = self._run(run_id)
            if run.status is not RunStatus.ACTIVE:
                if run.status is status:
                    return self._snapshot(run)
                raise BudgetError(f"run is already {run.status.value}")
            if run.reserved:
                raise BudgetError("cannot finish a run with unsettled model-call reservations")
            run.status = status
            run.finished_at = datetime.now(UTC)
            return self._snapshot(run)

    def snapshot(self, run_id: str) -> RunSnapshot:
        with self._lock:
            return self._snapshot(self._run(run_id))

    @staticmethod
    def _matching_rule(policy: OperatingPolicy, task: str) -> TaskRule | None:
        normalized = task.casefold()
        return next(
            (
                rule
                for rule in policy.task_rules
                if rule.match_terms and any(term in normalized for term in rule.match_terms)
            ),
            None,
        )

    def _run(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            raise BudgetError(f"unknown workflow run: {run_id}")
        return run

    def _active_run(self, run_id: str) -> _Run:
        run = self._run(run_id)
        if run.status is not RunStatus.ACTIVE:
            raise BudgetError(f"workflow run is {run.status.value}")
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
