from __future__ import annotations

import pytest

from promptrail import (
    GEMMA_12B_MODEL_ID,
    BudgetAllocationDecision,
    BudgetError,
    GlobalController,
    ModelCandidate,
    OperatingPolicy,
    ProviderRoute,
    RunStatus,
)
from promptrail.models import CacheAnalysis, CacheValue


def candidates() -> tuple[ModelCandidate, ...]:
    return (
        ModelCandidate(
            model_id="test-model",
            quality=0.9,
            context_window_tokens=32_000,
            routes=(
                ProviderRoute(
                    route_id="test-route",
                    provider="test-provider",
                    native_model_id="vendor/test-model",
                    input_price_per_million=1,
                    output_price_per_million=2,
                    p95_ttft_ms=100,
                    p95_total_latency_ms=500,
                    guaranteed=True,
                ),
            ),
        ),
    )


def cache_analysis(total_tokens: int = 1_000) -> CacheAnalysis:
    return CacheAnalysis(
        total_tokens=total_tokens,
        tool_tokens=0,
        message_tokens=(total_tokens,),
        prefix_hash=None,
        cacheable_message_indices=(),
        protected_message_indices=(0,),
        compactable_message_indices=(),
        cacheable_tokens=0,
        protected_dynamic_tokens=total_tokens,
        compactable_tokens=0,
        values={
            "test-model": CacheValue(
                model_id="test-model",
                provider="test-provider",
                input_cost_usd=total_tokens / 1_000_000,
            )
        },
    )


class RecordingGemmaAllocator:
    model_id = GEMMA_12B_MODEL_ID

    def __init__(self, decision: BudgetAllocationDecision):
        self.decision = decision
        self.requests = []

    def allocate(self, request):
        self.requests.append(request)
        return self.decision


def decision(*, cost_usd: float = 0.2, latency_ms: int = 2_000):
    return BudgetAllocationDecision(
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        input_cost_fraction=0.6,
        required_context_tokens=1,
        reason="Allocate enough capacity for the current agent step.",
    )


def test_gemma_allocates_open_ended_calls_from_analytics_and_session_state():
    allocator = RecordingGemmaAllocator(decision())
    controller = GlobalController(allocator=allocator)
    run = controller.start_run(
        session_id="open-ended-agent",
        policy=OperatingPolicy(
            analytics_insight="Successful resolutions matter more than minimizing call count.",
        ),
    )

    for sequence in range(1, 4):
        budget = controller.allocate_call(
            run_id=run.run_id,
            task=f"agent step {sequence}",
            input_tokens=1_000 * sequence,
            predicted_output_tokens=200,
            cache=cache_analysis(1_000 * sequence),
            candidates=candidates(),
        )
        request = allocator.requests[-1]
        assert request.sequence == sequence
        assert request.completed_calls == sequence - 1
        assert request.analytics_insight.startswith("Successful resolutions")
        assert request.remaining_hard_cost_usd is None
        assert request.remaining_hard_latency_ms is None
        assert budget.cost_usd == 0.2
        assert budget.latency_ms == 2_000
        assert budget.allocator_model_id == GEMMA_12B_MODEL_ID
        controller.settle_call(
            run_id=run.run_id,
            call_id=budget.call_id,
            cost_usd=0.01,
            latency_ms=100,
        )

    final = controller.finish_run(run_id=run.run_id, status=RunStatus.COMPLETED)
    assert final.completed_calls == 3


def test_uncapped_run_survives_provider_usage_above_gemma_forecast():
    allocator = RecordingGemmaAllocator(decision(cost_usd=0.000634, latency_ms=2_000))
    controller = GlobalController(allocator=allocator)
    run = controller.start_run(
        session_id="forecast-miss-agent",
        policy=OperatingPolicy(analytics_insight="Optimize without a spending ceiling."),
    )
    budget = controller.allocate_call(
        run_id=run.run_id,
        task="continue the multi-turn coding task",
        input_tokens=100,
        predicted_output_tokens=100,
        cache=cache_analysis(100),
        candidates=candidates(),
    )

    snapshot = controller.settle_call(
        run_id=run.run_id,
        call_id=budget.call_id,
        cost_usd=0.00076847,
        latency_ms=2_731,
    )

    assert snapshot.status is RunStatus.ACTIVE
    assert snapshot.completed_calls == 1
    assert snapshot.spent_cost_usd == pytest.approx(0.00076847)


def test_explicit_hard_call_limit_still_fails_on_authoritative_usage():
    allocator = RecordingGemmaAllocator(decision(cost_usd=0.4, latency_ms=2_000))
    controller = GlobalController(allocator=allocator)
    run = controller.start_run(
        session_id="hard-capped-agent",
        policy=OperatingPolicy(
            analytics_insight="Respect the explicit call ceiling.",
            hard_call_cost_limit_usd=0.5,
        ),
    )
    budget = controller.allocate_call(
        run_id=run.run_id,
        task="one capped call",
        input_tokens=100,
        predicted_output_tokens=100,
        cache=cache_analysis(100),
        candidates=candidates(),
    )

    with pytest.raises(BudgetError, match="hard per-call cost limit"):
        controller.settle_call(
            run_id=run.run_id,
            call_id=budget.call_id,
            cost_usd=0.6,
            latency_ms=100,
        )

    assert controller.snapshot(run.run_id).status is RunStatus.FAILED


def test_optional_hard_limits_reject_gemma_overallocation_without_clipping():
    allocator = RecordingGemmaAllocator(decision(cost_usd=0.6, latency_ms=2_000))
    controller = GlobalController(allocator=allocator)
    run = controller.start_run(
        session_id="capped-agent",
        policy=OperatingPolicy(
            analytics_insight="Use the explicit cap when it exists.",
            hard_call_cost_limit_usd=0.5,
        ),
    )

    with pytest.raises(BudgetError, match="hard per-call cost"):
        controller.allocate_call(
            run_id=run.run_id,
            task="one call",
            input_tokens=100,
            predicted_output_tokens=100,
            cache=cache_analysis(100),
            candidates=candidates(),
        )

    snapshot = controller.snapshot(run.run_id)
    assert snapshot.reserved_cost_usd == 0
    assert snapshot.completed_calls == 0


def test_controller_refuses_a_non_gemma_budget_owner():
    allocator = RecordingGemmaAllocator(decision())
    allocator.model_id = "some/other-model"

    with pytest.raises(ValueError, match="pinned Gemma 12B"):
        GlobalController(allocator=allocator)


def test_finish_logs_and_charges_unsettled_reservations(caplog):
    allocator = RecordingGemmaAllocator(decision(cost_usd=0.2, latency_ms=2_000))
    controller = GlobalController(allocator=allocator)
    run = controller.start_run(
        session_id="unfinished-call-agent",
        policy=OperatingPolicy(analytics_insight="Keep accounting conservative."),
    )
    controller.allocate_call(
        run_id=run.run_id,
        task="call interrupted during shutdown",
        input_tokens=100,
        predicted_output_tokens=100,
        cache=cache_analysis(100),
        candidates=candidates(),
    )

    with caplog.at_level("ERROR", logger="promptrail.controller"):
        final = controller.finish_run(run_id=run.run_id, status=RunStatus.COMPLETED)

    assert final.status is RunStatus.COMPLETED
    assert final.completed_calls == 1
    assert final.spent_cost_usd == 0.2
    assert final.spent_model_latency_ms == 2_000
    assert final.reserved_cost_usd == 0
    assert "unsettled call reservation" in caplog.text
