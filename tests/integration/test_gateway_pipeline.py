from __future__ import annotations

import json
import math
import threading

from promptrail import (
    GEMMA_12B_MODEL_ID,
    BudgetAllocationDecision,
    CacheAwareLeRouter,
    CallIntent,
    ModelCandidate,
    OperatingPolicy,
    PromptRailGateway,
    ProviderRoute,
    RunStatus,
    SuppliedLeRouterRanker,
    SuppliedPolicyAgent,
)
from promptrail.cache import PromptCacheCoordinator
from promptrail.models import ModelUsage, ProviderRoutingMode


class FixedGemmaAllocator:
    model_id = GEMMA_12B_MODEL_ID

    def __init__(self, *, cost_usd: float, latency_ms: int, input_fraction: float):
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.input_fraction = input_fraction
        self.requests = []

    def allocate(self, request):
        self.requests.append(request)
        return BudgetAllocationDecision(
            cost_usd=self.cost_usd,
            latency_ms=self.latency_ms,
            input_cost_fraction=self.input_fraction,
            required_context_tokens=max(1, request.input_tokens // 2),
            reason="Scripted Gemma allocation for the integration boundary.",
        )


def enterprise_file(tmp_path):
    path = tmp_path / "enterprise.json"
    path.write_text(
        json.dumps(
            {
                "service": "support-agent",
                "monthly_budget_usd": 1000,
                "p95_latency_ms": 5000,
                "risk_tiers": {"payments": "high", "faq": "normal"},
            }
        ),
        encoding="utf-8",
    )
    return path


def route(
    route_id: str,
    provider: str,
    *,
    input_price: float,
    output_price: float,
    latency_ms: int,
    guaranteed: bool,
) -> ProviderRoute:
    return ProviderRoute(
        route_id=route_id,
        provider=provider,
        native_model_id="vendor/model-a",
        input_price_per_million=input_price,
        output_price_per_million=output_price,
        cache_read_price_per_million=1.0,
        cache_write_price_per_million=input_price,
        p95_ttft_ms=max(50, latency_ms // 4),
        p95_total_latency_ms=latency_ms,
        guaranteed=guaranteed,
        cache_supported=True,
        cache_automatic=True,
        capabilities=frozenset({"tools"}),
    )


def policy(**updates):
    values = {
        "analytics_insight": "Prefer cheap routes while preserving latency and cache value.",
        "cost_priority": 1.0,
        "latency_priority": 0.5,
        "quality_priority": 1.0,
        "cache_priority": 2.0,
        "provider_exploration_fraction": 0.5,
        "maximum_provider_exploration_ms": 1_800,
        "controller_reserve_ms": 25,
        "compaction_reserve_ms": 75,
        "latency_safety_margin_ms": 50,
    }
    values.update(updates)
    return OperatingPolicy(**values)


def test_full_call_pipeline_compacts_only_cache_authorized_content_and_becomes_sticky(
    tmp_path,
):
    enterprise = enterprise_file(tmp_path)
    cheap = route(
        "cheap",
        "slow-provider",
        input_price=20,
        output_price=20,
        latency_ms=2_200,
        guaranteed=False,
    )
    fast = route(
        "fast",
        "fast-provider",
        input_price=25,
        output_price=25,
        latency_ms=1_200,
        guaranteed=True,
    )
    candidate = ModelCandidate(
        model_id="model-a",
        quality=0.9,
        context_window_tokens=128_000,
        capabilities=frozenset({"tools"}),
        router_score=0.9,
        routes=(cheap, fast),
    )
    allocator = FixedGemmaAllocator(cost_usd=0.5, latency_ms=5_000, input_fraction=0.02)
    gateway = PromptRailGateway(
        policy_agent=SuppliedPolicyAgent(policy()),
        budget_allocator=allocator,
        model_router=CacheAwareLeRouter(SuppliedLeRouterRanker()),
    )
    run = gateway.start_run(
        session_id="session-a",
        enterprise_json_paths=[enterprise],
        candidates=[candidate],
    )
    messages = (
        {"role": "system", "content": "Never disclose customer secrets."},
        {"role": "user", "content": "Inspect the deployment and summarize the result."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "type": "function"}],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "INFO deployment healthy\n" * 1_500,
        },
    )
    intent = CallIntent(
        session_id="session-a",
        task="deployment inspection with tools",
        messages=messages,
        required_capabilities=frozenset({"tools"}),
        predicted_output_tokens=512,
    )

    first = gateway.prepare_call_sync(run_id=run.run_id, intent=intent)

    assert first.budget.allocator_model_id == GEMMA_12B_MODEL_ID
    assert allocator.requests[0].analytics_insight == policy().analytics_insight
    assert allocator.requests[0].remaining_hard_cost_usd is None
    assert first.model.candidate.model_id == "model-a"
    assert first.provider.mode is ProviderRoutingMode.DEADLINE
    assert first.provider.start_within_ms != 700
    assert 0 < first.provider.start_within_ms <= 1_800
    execution_budget = first.budget.latency_ms - max(first.preparation_latency_ms, 25 + 75) - 50
    assert first.provider.start_within_ms == min(
        1_800,
        math.floor((execution_budget - fast.p95_total_latency_ms) * 0.5),
    )
    immutable_indices = set(first.cache.cacheable_message_indices) | set(
        first.cache.protected_message_indices
    )
    physically_reachable_floor = first.cache.total_tokens - sum(
        max(0, first.cache.message_tokens[index] - 32)
        for index in first.cache.compactable_message_indices
        if index not in immutable_indices
    )
    assert first.compaction.target_tokens == max(
        physically_reachable_floor,
        first.budget.required_context_tokens,
    )
    assert first.compaction.records
    assert first.compaction.messages[0] == messages[0]
    assert first.compaction.messages[1] == messages[1]
    assert first.compaction.messages[2] == messages[2]
    assert first.compaction.messages[3] != messages[3]
    assert "PromptRail inline tool summary" in first.compaction.messages[3]["content"]

    gateway.observe_model(
        prepared=first,
        usage=ModelUsage(
            input_tokens=first.compaction.tokens_after,
            output_tokens=128,
            latency_ms=1_600,
            provider="slow-provider",
            model_id="model-a",
            cache_write_tokens=123,
        ),
    )
    second = gateway.prepare_call_sync(run_id=run.run_id, intent=intent)
    assert second.cache.values["model-a"].exact_reuse
    assert second.cache.values["model-a"].cached_tokens == 123
    assert second.provider.mode is ProviderRoutingMode.STICKY
    assert second.provider.routes[0].provider == "slow-provider"
    assert second.provider.start_within_ms == 0

    gateway.observe_model(
        prepared=second,
        usage=ModelUsage(
            input_tokens=second.compaction.tokens_after,
            output_tokens=96,
            latency_ms=1_500,
            provider="slow-provider",
            model_id="model-a",
            cache_read_tokens=123,
        ),
    )
    final = gateway.finish_run(run_id=run.run_id, success=True)
    assert final.status is RunStatus.COMPLETED
    assert gateway.compactor.store.delete_session("session-a") == 0


def test_allocation_overlaps_ranking_and_failover_reuses_prepared_control_plane(tmp_path):
    ranking_started = threading.Event()
    release_ranking = threading.Event()
    allocation_started = threading.Event()

    class BlockingRanker:
        def __init__(self):
            self.calls = 0

        def rank(self, *, intent, candidates, timeout_ms):
            del intent, timeout_ms
            self.calls += 1
            ranking_started.set()
            assert release_ranking.wait(timeout=1)
            return {
                candidate.model_id: 0.9 - index * 0.1 for index, candidate in enumerate(candidates)
            }

    class PipelinedAllocator:
        model_id = GEMMA_12B_MODEL_ID

        def __init__(self):
            self.calls = 0

        def allocate(self, request):
            self.calls += 1
            assert ranking_started.wait(timeout=1)
            allocation_started.set()
            release_ranking.set()
            return BudgetAllocationDecision(
                cost_usd=0.5,
                latency_ms=5_000,
                input_cost_fraction=0.5,
                required_context_tokens=request.input_tokens,
                reason="Test the pipelined allocation boundary.",
            )

    candidates = tuple(
        ModelCandidate(
            model_id=f"model-{suffix}",
            quality=0.9,
            context_window_tokens=128_000,
            router_score=None,
            routes=(
                route(
                    f"route-{suffix}",
                    f"provider-{suffix}",
                    input_price=1,
                    output_price=1,
                    latency_ms=500,
                    guaranteed=True,
                ).model_copy(update={"native_model_id": f"vendor/model-{suffix}"}),
            ),
        )
        for suffix in ("a", "b")
    )
    ranker = BlockingRanker()
    allocator = PipelinedAllocator()
    gateway = PromptRailGateway(
        policy_agent=SuppliedPolicyAgent(policy()),
        budget_allocator=allocator,
        model_router=CacheAwareLeRouter(ranker),
    )
    run = gateway.start_run(
        session_id="pipelined-session",
        enterprise_json_paths=[enterprise_file(tmp_path)],
        candidates=candidates,
    )
    prepared = gateway.prepare_call_sync(
        run_id=run.run_id,
        intent=CallIntent(
            session_id="pipelined-session",
            task="fix code",
            messages=({"role": "user", "content": "fix code"},),
            predicted_output_tokens=64,
        ),
    )

    assert allocation_started.is_set()
    assert ranker.calls == 1
    assert allocator.calls == 1
    assert prepared.model.candidate.model_id == "model-a"
    assert [item.candidate.model_id for item in prepared.model_alternatives] == ["model-b"]

    rerouted = gateway.reroute_prepared(
        prepared=prepared,
        excluded_model_ids=frozenset({"model-a"}),
    )

    assert rerouted.model.candidate.model_id == "model-b"
    assert rerouted.budget.call_id == prepared.budget.call_id
    assert rerouted.compaction == prepared.compaction
    assert ranker.calls == 1
    assert allocator.calls == 1
    gateway.fail_model(prepared=rerouted, billing_unknown=False)


class SequenceRanker:
    def __init__(self, rankings):
        self._rankings = iter(rankings)
        self.timeouts = []

    def rank(self, *, intent, candidates, timeout_ms):
        del intent, candidates
        self.timeouts.append(timeout_ms)
        return next(self._rankings)


def test_cache_value_can_keep_lerouter_on_the_previous_model(tmp_path):
    enterprise = enterprise_file(tmp_path)
    shared_route_a = route(
        "provider-a-model-a",
        "provider-a",
        input_price=100,
        output_price=10,
        latency_ms=700,
        guaranteed=True,
    )
    shared_route_b = route(
        "provider-a-model-b",
        "provider-a",
        input_price=100,
        output_price=10,
        latency_ms=700,
        guaranteed=True,
    ).model_copy(update={"native_model_id": "vendor/model-b"})
    candidates = (
        ModelCandidate(
            model_id="model-a",
            quality=0.9,
            context_window_tokens=128_000,
            router_score=None,
            routes=(shared_route_a,),
        ),
        ModelCandidate(
            model_id="model-b",
            quality=0.9,
            context_window_tokens=128_000,
            router_score=None,
            routes=(shared_route_b,),
        ),
    )
    ranker = SequenceRanker(
        [
            {"model-a": 0.90, "model-b": 0.10},
            {"model-a": 0.50, "model-b": 0.90},
        ]
    )
    gateway = PromptRailGateway(
        policy_agent=SuppliedPolicyAgent(
            policy(
                cache_priority=4.0,
            )
        ),
        budget_allocator=FixedGemmaAllocator(
            cost_usd=1.5,
            latency_ms=4_000,
            input_fraction=0.8,
        ),
        model_router=CacheAwareLeRouter(ranker),
    )
    run = gateway.start_run(
        session_id="cache-session",
        enterprise_json_paths=[enterprise],
        candidates=candidates,
    )
    messages = (
        {"role": "system", "content": "enterprise handbook\n" * 2_000},
        {"role": "user", "content": "Answer this support request."},
    )
    intent = CallIntent(
        session_id="cache-session",
        task="support",
        messages=messages,
        predicted_output_tokens=64,
    )
    first = gateway.prepare_call_sync(run_id=run.run_id, intent=intent)
    assert first.model.candidate.model_id == "model-a"
    assert 0 < ranker.timeouts[0] <= 3_150
    gateway.observe_model(
        prepared=first,
        usage=ModelUsage(
            input_tokens=first.compaction.tokens_after,
            output_tokens=32,
            latency_ms=500,
            provider="provider-a",
            model_id="model-a",
            cache_write_tokens=10_000,
        ),
    )

    second = gateway.prepare_call_sync(run_id=run.run_id, intent=intent)
    assert second.cache.values["model-a"].cached_tokens == 10_000
    assert second.model.base_router_score == 0.50
    assert second.cache.values["model-a"].retained_value_usd > 0
    assert second.model.candidate.model_id == "model-a"
    assert second.provider.mode is ProviderRoutingMode.STICKY


def test_cache_requires_provider_observation_and_does_not_freeze_a_candidate_prefix():
    candidate_route = route(
        "cache-route",
        "cache-provider",
        input_price=1,
        output_price=1,
        latency_ms=500,
        guaranteed=True,
    )
    candidate = ModelCandidate(
        model_id="model-a",
        quality=0.9,
        context_window_tokens=128_000,
        capabilities=frozenset({"tools"}),
        router_score=0.9,
        routes=(candidate_route,),
    )
    cache = PromptCacheCoordinator(minimum_compactable_tokens=16)
    messages = (
        {"role": "system", "content": "system contract\n" * 500},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer\n" * 500},
        {"role": "user", "content": "current request"},
    )

    first = cache.analyze(
        session_id="cache-truth",
        messages=messages,
        tools=(),
        candidates=(candidate,),
        predicted_output_tokens=64,
        task="current request",
    )

    assert first.cacheable_message_indices == ()
    assert 2 in first.compactable_message_indices
    cache.observe(
        session_id="cache-truth",
        analysis=first,
        route=candidate_route,
        usage=ModelUsage(
            input_tokens=first.total_tokens,
            output_tokens=8,
            latency_ms=100,
            provider="cache-provider",
            model_id="model-a",
        ),
    )
    unobserved = cache.analyze(
        session_id="cache-truth",
        messages=messages,
        tools=(),
        candidates=(candidate,),
        predicted_output_tokens=64,
        task="current request",
    )
    assert not unobserved.values["model-a"].exact_reuse

    cache.observe(
        session_id="cache-truth",
        analysis=first,
        route=candidate_route,
        usage=ModelUsage(
            input_tokens=first.total_tokens,
            output_tokens=8,
            latency_ms=100,
            provider="cache-provider",
            model_id="model-a",
            cache_write_tokens=321,
        ),
    )
    observed = cache.analyze(
        session_id="cache-truth",
        messages=messages,
        tools=(),
        candidates=(candidate,),
        predicted_output_tokens=64,
        task="current request",
    )
    assert observed.values["model-a"].exact_reuse
    assert observed.values["model-a"].cached_tokens == 321
    assert observed.cacheable_tokens == 321


def test_compaction_preserves_confirmed_cache_until_uncached_context_is_insufficient(tmp_path):
    class ContextSequenceAllocator:
        model_id = GEMMA_12B_MODEL_ID

        def allocate(self, request):
            return BudgetAllocationDecision(
                cost_usd=1,
                latency_ms=5_000,
                input_cost_fraction=0.9,
                required_context_tokens=request.input_tokens,
                reason="Exercise cache-aware compaction pressure.",
            )

    candidate_route = route(
        "cache-compaction-route",
        "cache-provider",
        input_price=1,
        output_price=1,
        latency_ms=500,
        guaranteed=True,
    )
    candidate = ModelCandidate(
        model_id="model-a",
        quality=0.9,
        context_window_tokens=128_000,
        capabilities=frozenset({"tools"}),
        router_score=0.9,
        routes=(candidate_route,),
    )
    gateway = PromptRailGateway(
        policy_agent=SuppliedPolicyAgent(policy()),
        budget_allocator=ContextSequenceAllocator(),
        model_router=CacheAwareLeRouter(SuppliedLeRouterRanker()),
    )
    run = gateway.start_run(
        session_id="cache-compaction",
        enterprise_json_paths=[enterprise_file(tmp_path)],
        candidates=(candidate,),
    )
    messages = (
        {"role": "system", "content": "Keep the coding contract."},
        {"role": "user", "content": "Old task"},
        {"role": "assistant", "content": "historical analysis\n" * 1_000},
        {"role": "user", "content": "Current task"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "type": "function"}],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "current tool output\n" * 1_000,
        },
    )
    intent = CallIntent(
        session_id="cache-compaction",
        task="Current task",
        messages=messages,
        required_capabilities=frozenset({"tools"}),
        predicted_output_tokens=64,
    )

    first = gateway.prepare_call_sync(run_id=run.run_id, intent=intent)
    assert not first.compaction.records
    gateway.observe_model(
        prepared=first,
        usage=ModelUsage(
            input_tokens=first.compaction.tokens_after,
            output_tokens=8,
            latency_ms=100,
            provider="cache-provider",
            model_id="model-a",
            cache_write_tokens=3_000,
        ),
    )

    confirmed = gateway.cache.analyze(
        session_id=intent.session_id,
        messages=intent.messages,
        tools=intent.tools,
        candidates=(candidate,),
        predicted_output_tokens=64,
        task=intent.task,
    )
    assert 2 in confirmed.cacheable_message_indices
    cached_decision = first.model.model_copy(
        update={"cache_value": confirmed.values["model-a"]}
    )
    preserve_budget = first.budget.model_copy(
        update={"required_context_tokens": confirmed.total_tokens - 200}
    )
    preserve_plan = gateway.compaction_planner.plan(
        cache=confirmed,
        decision=cached_decision,
        budget=preserve_budget,
    )
    assert 5 in preserve_plan.compactable_message_indices
    assert 2 not in preserve_plan.compactable_message_indices

    sacrifice_budget = first.budget.model_copy(
        update={
            "required_context_tokens": gateway.compaction_planner.minimum_reachable_tokens(
                confirmed
            )
        }
    )
    sacrifice_plan = gateway.compaction_planner.plan(
        cache=confirmed,
        decision=cached_decision,
        budget=sacrifice_budget,
    )
    assert 2 in sacrifice_plan.compactable_message_indices
