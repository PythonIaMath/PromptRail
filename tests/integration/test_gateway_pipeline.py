from __future__ import annotations

import json
import math

import pytest

from promptrail import (
    CacheAwareLeRouter,
    CallIntent,
    CompactionError,
    ModelCandidate,
    OperatingPolicy,
    PromptRailGateway,
    ProviderRoute,
    RunStatus,
    SuppliedPolicyAgent,
)
from promptrail.models import ModelUsage, ProviderRoutingMode


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
        "instruction": "Prefer the cheapest route that remains inside the latency SLO.",
        "workflow_cost_budget_usd": 1.0,
        "workflow_latency_budget_ms": 10_000,
        "expected_llm_calls": 2,
        "input_cost_fraction": 0.02,
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
    gateway = PromptRailGateway(policy_agent=SuppliedPolicyAgent(policy()))
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
        expected_remaining_calls=2,
    )

    first = gateway.prepare_call_sync(run_id=run.run_id, intent=intent)

    assert first.model.candidate.model_id == "model-a"
    assert first.provider.mode is ProviderRoutingMode.DEADLINE
    assert first.provider.start_within_ms != 700
    assert 0 < first.provider.start_within_ms <= 1_800
    execution_budget = (
        first.budget.latency_ms
        - max(first.preparation_latency_ms, 25 + 75)
        - 50
    )
    assert first.provider.start_within_ms == min(
        1_800,
        math.floor((execution_budget - fast.p95_total_latency_ms) * 0.5),
    )
    affordable_tokens = math.floor(first.budget.input_cost_usd * 1_000_000 / 20)
    non_compactable_tokens = first.cache.total_tokens - first.cache.compactable_tokens
    assert first.compaction.target_tokens == max(
        non_compactable_tokens,
        min(first.cache.total_tokens, affordable_tokens),
    )
    assert first.compaction.records
    assert first.compaction.messages[0] == messages[0]
    assert first.compaction.messages[1] == messages[1]
    assert first.compaction.messages[2] == messages[2]
    assert first.compaction.messages[3] != messages[3]
    record = first.compaction.records[0]
    assert (
        gateway.compactor.store.get(
            session_id="session-a",
            retrieval_id=record.retrieval_id,
        )
        == messages[3]["content"]
    )

    gateway.observe_model(
        prepared=first,
        usage=ModelUsage(
            input_tokens=first.compaction.tokens_after,
            output_tokens=128,
            latency_ms=1_600,
            provider="slow-provider",
            model_id="model-a",
            cache_write_tokens=first.cache.cacheable_tokens,
        ),
    )
    second = gateway.prepare_call_sync(run_id=run.run_id, intent=intent)
    assert second.cache.values["model-a"].exact_reuse
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
            cache_read_tokens=second.cache.cacheable_tokens,
        ),
    )
    final = gateway.finish_run(run_id=run.run_id, success=True)
    assert final.status is RunStatus.COMPLETED
    with pytest.raises(CompactionError):
        gateway.compactor.store.get(
            session_id="session-a",
            retrieval_id=record.retrieval_id,
        )


class SequenceRanker:
    def __init__(self, rankings):
        self._rankings = iter(rankings)

    def rank(self, *, intent, candidates):
        del intent, candidates
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
                workflow_cost_budget_usd=3.0,
                workflow_latency_budget_ms=8_000,
                expected_llm_calls=2,
                input_cost_fraction=0.8,
                cache_priority=4.0,
            )
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
        expected_remaining_calls=2,
    )
    first = gateway.prepare_call_sync(run_id=run.run_id, intent=intent)
    assert first.model.candidate.model_id == "model-a"
    gateway.observe_model(
        prepared=first,
        usage=ModelUsage(
            input_tokens=first.compaction.tokens_after,
            output_tokens=32,
            latency_ms=500,
            provider="provider-a",
            model_id="model-a",
            cache_write_tokens=first.cache.cacheable_tokens,
        ),
    )

    second = gateway.prepare_call_sync(run_id=run.run_id, intent=intent)
    assert second.model.base_router_score == 0.50
    assert second.cache.values["model-a"].retained_value_usd > 0
    assert second.model.candidate.model_id == "model-a"
    assert second.provider.mode is ProviderRoutingMode.STICKY
