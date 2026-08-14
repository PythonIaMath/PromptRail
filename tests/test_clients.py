from __future__ import annotations

import json

import httpx
import pytest

from promptrail import GEMMA_12B_MODEL_ID, clients
from promptrail.clients import (
    LEROUTER_BIENCODER_MODEL_ID,
    LEROUTER_RANKER_CONTRACT_VERSION,
    LEROUTER_RANKER_RUN_ID_ENV,
    LEROUTER_RANKER_TOKEN_ENV,
    LEROUTER_RANKER_URL_ENV,
    PRODUCTION_LEROUTER_RANKER_URL,
    PRODUCTION_LEROUTER_RUN_ID,
    Gemma12BHTTPGenerator,
    LeRouterHTTPRanker,
    LeRouterOutputLengthPredictor,
    LeRouterPolicyGenerator,
)
from promptrail.errors import RoutingError
from promptrail.models import (
    BudgetAllocationDecision,
    CallIntent,
    ModelCandidate,
    OperatingPolicy,
    ProviderRoute,
)


def _candidate(model_id: str, quality: float) -> ModelCandidate:
    return ModelCandidate(
        model_id=model_id,
        quality=quality,
        context_window_tokens=128_000,
        strengths=frozenset({"coding", "tool use"}),
        capabilities=frozenset({"tools"}),
        router_payload={
            "benchmark_results": {
                "swe_bench_pro": quality,
            }
        },
        routes=(
            ProviderRoute(
                route_id=f"route-{model_id}",
                provider="provider-a",
                native_model_id=model_id,
                input_price_per_million=1,
                output_price_per_million=2,
                p95_ttft_ms=100,
                p95_total_latency_ms=500,
                guaranteed=True,
            ),
        ),
    )


def test_lerouter_policy_generator_sends_strict_compatible_schema(monkeypatch):
    captured = {}

    def fake_post_json(**kwargs):
        captured.update(kwargs["payload"])
        return {
            "response": {
                "content": json.dumps(
                    {
                        "analytics_insight": "Control spend while preserving user outcomes.",
                    }
                )
            }
        }

    monkeypatch.setattr(clients, "_post_json", fake_post_json)
    generator = LeRouterPolicyGenerator(
        api_url="https://lerouter.example",
        agent_token="test-token",
        user_id="test-user",
    )

    generated = generator.generate_policy(
        system_instruction="Generate policy.",
        enterprise_data={"enterprise.json": {"budget": 1}},
        output_schema=OperatingPolicy.model_json_schema(),
    )

    assert generated["analytics_insight"].startswith("Control spend")
    schema = captured["response_format"]["json_schema"]["schema"]
    assert schema["required"] == list(schema["properties"])
    task_rule = schema["$defs"]["TaskRule"]
    assert task_rule["required"] == list(task_rule["properties"])
    assert task_rule["additionalProperties"] is False
    assert "default" not in schema["properties"]["source_digest"]


def test_lerouter_ranker_calls_live_semantic_contract_without_precomputed_embeddings(
    monkeypatch,
):
    captured = {}
    candidates = (
        _candidate("openai/gpt-5.5", 0.9),
        _candidate("qwen/qwen3-coder-plus", 0.8),
    )

    def fake_post_json(**kwargs):
        captured.update(kwargs)
        return {
            "contract_version": LEROUTER_RANKER_CONTRACT_VERSION,
            "branch": "free",
            "models": {
                "semantic": LEROUTER_BIENCODER_MODEL_ID,
                "semantic_run": PRODUCTION_LEROUTER_RUN_ID,
                "worker": "production-worker-v1",
            },
            "ranked": [
                {
                    "candidate_id": "qwen/qwen3-coder-plus",
                    "semantic_task_fit": 0.86,
                    "predicted_success": 0.84,
                    "rank": 1,
                },
                {
                    "candidate_id": "openai/gpt-5.5",
                    "semantic_task_fit": 0.63,
                    "predicted_success": 0.71,
                    "rank": 2,
                },
            ],
        }

    monkeypatch.setattr(clients, "_post_json", fake_post_json)
    ranker = LeRouterHTTPRanker.from_env(
        {
            LEROUTER_RANKER_URL_ENV: PRODUCTION_LEROUTER_RANKER_URL,
            LEROUTER_RANKER_TOKEN_ENV: "test-service-token",
            LEROUTER_RANKER_RUN_ID_ENV: PRODUCTION_LEROUTER_RUN_ID,
        }
    )
    scores = ranker.rank(
        intent=CallIntent(
            session_id="session-a",
            task="Repair a Python service and use its deployment tools.",
            messages=(
                {"role": "user", "content": "Inspect the failing deployment."},
                {"role": "assistant", "content": "I found a Python traceback."},
            ),
            tools=({"name": "inspect_deployment"},),
            required_capabilities=frozenset({"tools"}),
            predicted_output_tokens=700,
        ),
        candidates=candidates,
        timeout_ms=2_500,
    )

    assert scores == {
        "qwen/qwen3-coder-plus": 0.84,
        "openai/gpt-5.5": 0.71,
    }
    assert captured["url"] == PRODUCTION_LEROUTER_RANKER_URL
    assert captured["bearer_token"] == "test-service-token"
    assert captured["timeout_seconds"] == 2.5
    payload = captured["payload"]
    assert payload["mode"] == "infinite_route_v2"
    assert payload["semantic_context"] == {
        "current_user_prompt": (
            "Task: Repair a Python service and use its deployment tools.\n\n"
            "Current user message:\nInspect the failing deployment."
        ),
        "previous_user_prompt": "",
        "previous_assistant_summary": "I found a Python traceback.",
        "client_type": "promptrail_gateway",
        "tool_presence": True,
        "capability_requirements": ["tools"],
        "requested_output_tokens": 700,
    }
    assert [item["candidate_id"] for item in payload["candidates"]] == [
        "openai/gpt-5.5",
        "qwen/qwen3-coder-plus",
    ]
    assert all("gemma4_profile_embedding" not in item for item in payload["candidates"])
    assert payload["candidates"][0]["forces"] == ["coding", "tool use", "tools"]
    assert payload["candidates"][0]["benchmark_results"] == {"swe_bench_pro": 0.9}


def test_lerouter_ranker_fails_closed_on_model_run_drift(monkeypatch):
    monkeypatch.setattr(
        clients,
        "_post_json",
        lambda **kwargs: {
            "contract_version": LEROUTER_RANKER_CONTRACT_VERSION,
            "branch": "free",
            "models": {
                "semantic": LEROUTER_BIENCODER_MODEL_ID,
                "semantic_run": "unapproved-run",
            },
            "ranked": [
                {
                    "candidate_id": "openai/gpt-5.5",
                    "semantic_task_fit": 0.8,
                    "predicted_success": 0.8,
                    "rank": 1,
                }
            ],
        },
    )
    ranker = LeRouterHTTPRanker(
        endpoint_url=PRODUCTION_LEROUTER_RANKER_URL,
        service_token="test-service-token",
    )

    with pytest.raises(RoutingError, match="unexpected semantic model run"):
        ranker.rank(
            intent=CallIntent(
                session_id="session-a",
                task="answer a question",
                messages=({"role": "user", "content": "Answer this."},),
            ),
            candidates=(_candidate("openai/gpt-5.5", 0.9),),
            timeout_ms=2_500,
        )


def test_lerouter_ranker_requires_scoped_service_token():
    with pytest.raises(ValueError, match=LEROUTER_RANKER_TOKEN_ENV):
        LeRouterHTTPRanker.from_env({})


def test_gemma_generator_pins_model_and_sends_strict_budget_schema(monkeypatch):
    captured = {}

    def fake_post_json(**kwargs):
        captured.update(kwargs["payload"])
        return {
            "model": GEMMA_12B_MODEL_ID,
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "schema_version": 2,
                                "cost_usd": 0.2,
                                "latency_ms": 2500,
                                "input_cost_fraction": 0.6,
                                "required_context_tokens": 100,
                                "importance_overrides": [],
                                "reason": "Preserve interactive latency.",
                            }
                        )
                    }
                }
            ],
        }

    monkeypatch.setattr(clients, "_post_json", fake_post_json)
    generator = Gemma12BHTTPGenerator(
        endpoint_url="https://gemma.example/v1/chat/completions",
        service_token="test-token",
    )
    result = generator.generate_budget(
        model_id=GEMMA_12B_MODEL_ID,
        system_instruction="Allocate this call.",
        request={"analytics_insight": "Keep support interactions responsive."},
        output_schema=BudgetAllocationDecision.model_json_schema(),
    )

    assert captured["model"] == GEMMA_12B_MODEL_ID
    assert captured["temperature"] == 0
    assert result.model_id == GEMMA_12B_MODEL_ID
    assert result.payload["latency_ms"] == 2500
    schema = captured["response_format"]["json_schema"]["schema"]
    assert schema["required"] == list(schema["properties"])
    fraction = schema["properties"]["input_cost_fraction"]
    assert fraction["minimum"] == 0.000001
    assert fraction["exclusiveMaximum"] == 1


def test_output_length_predictor_formats_conversation_and_applies_caller_ceiling():
    captured = []
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        captured.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer internal-token"
        return httpx.Response(
            200,
            json={"predicted_tokens": 812.6, "predicted_tokens_rounded": 813},
        )

    with httpx.Client(transport=httpx.MockTransport(handler), http2=True) as client:
        predictor = LeRouterOutputLengthPredictor(
            endpoint_url="https://predictor.example/predict",
            service_token="internal-token",
            client=client,
        )
        result = predictor.predict(
            messages=(
                {"role": "system", "content": "Be exact."},
                {"role": "user", "content": "Explain this repository."},
            ),
            max_output_tokens=500,
        )
        repeated = predictor.predict(
            messages=({"role": "user", "content": "Short answer."},),
            max_output_tokens=500,
        )

    assert result.predicted_tokens == 500
    assert result.raw_predicted_tokens == 812.6
    assert repeated.predicted_tokens == 500
    assert requests == 2
    assert captured[0] == {
        "verbosity_multiplier": 1.0,
        "prompt": "SYSTEM:\nBe exact.\n\nUSER:\nExplain this repository.",
    }


@pytest.mark.parametrize("payload", [{}, {"predicted_tokens": 0}, {"predicted_tokens": "8"}])
def test_output_length_predictor_fails_closed_on_invalid_response(payload):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with httpx.Client(transport=httpx.MockTransport(handler), http2=True) as client:
        predictor = LeRouterOutputLengthPredictor(
            endpoint_url="https://predictor.example/predict",
            service_token="internal-token",
            client=client,
        )

        with pytest.raises(RuntimeError, match="output-length predictor"):
            predictor.predict(messages=({"role": "user", "content": "hello"},))
