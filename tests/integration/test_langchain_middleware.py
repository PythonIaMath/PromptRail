from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import PrivateAttr

from promptrail import ModelCandidate, OperatingPolicy, PromptRailGateway, ProviderRoute
from promptrail.errors import CompactionError
from promptrail.langchain import PromptRailContext, PromptRailMiddleware
from promptrail.models import PreparedCall, ProviderRoutingMode, RunStatus
from promptrail.policy import SuppliedPolicyAgent


class ScriptedChatModel(BaseChatModel):
    _responses: list[AIMessage] = PrivateAttr()
    _requests: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    def __init__(self, responses: list[AIMessage]) -> None:
        super().__init__()
        self._responses = responses

    @property
    def _llm_type(self) -> str:
        return "promptrail-scripted-test"

    @property
    def requests(self) -> tuple[tuple[BaseMessage, ...], ...]:
        return tuple(tuple(request) for request in self._requests)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        del tools, tool_choice, kwargs
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        self._requests.append(list(messages))
        if not self._responses:
            raise AssertionError("scripted model received an unexpected call")
        return ChatResult(generations=[ChatGeneration(message=self._responses.pop(0))])


class CapturingModelFactory:
    def __init__(self, model: BaseChatModel) -> None:
        self.model = model
        self.prepared: list[PreparedCall] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, *, prepared, provider_router_headers):
        self.prepared.append(prepared)
        self.headers.append(dict(provider_router_headers))
        return self.model


def _route(
    route_id: str,
    provider: str,
    *,
    latency_ms: int,
    guaranteed: bool,
) -> ProviderRoute:
    return ProviderRoute(
        route_id=route_id,
        provider=provider,
        native_model_id="vendor/model-a",
        input_price_per_million=20 if not guaranteed else 25,
        output_price_per_million=20 if not guaranteed else 25,
        cache_read_price_per_million=1,
        cache_write_price_per_million=20,
        p95_ttft_ms=max(50, latency_ms // 4),
        p95_total_latency_ms=latency_ms,
        guaranteed=guaranteed,
        cache_supported=True,
        cache_automatic=True,
        capabilities=frozenset({"tools"}),
    )


def _policy() -> OperatingPolicy:
    return OperatingPolicy(
        instruction="Prefer cheap providers while preserving the latency SLO and prompt cache.",
        workflow_cost_budget_usd=1,
        workflow_latency_budget_ms=10_000,
        expected_llm_calls=2,
        input_cost_fraction=0.02,
        latency_priority=0.5,
        cache_priority=2,
        provider_exploration_fraction=0.5,
        maximum_provider_exploration_ms=1_800,
        controller_reserve_ms=25,
        compaction_reserve_ms=75,
        latency_safety_margin_ms=50,
    )


def _usage(input_tokens: int, output_tokens: int, *, cache_creation: int = 0):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_token_details": {
            "cache_creation": cache_creation,
            "cache_read": 0,
        },
    }


@pytest.mark.parametrize("async_invocation", [False, True])
def test_langchain_middleware_controls_full_agent_lifecycle(tmp_path, async_invocation):
    enterprise = tmp_path / "enterprise.json"
    enterprise.write_text(
        json.dumps({"service": "deployment-agent", "p95_latency_ms": 5000}),
        encoding="utf-8",
    )
    cheap = _route("cheap", "slow-provider", latency_ms=2_200, guaranteed=False)
    fast = _route("fast", "fast-provider", latency_ms=1_200, guaranteed=True)
    candidate = ModelCandidate(
        model_id="model-a",
        quality=0.9,
        context_window_tokens=128_000,
        capabilities=frozenset({"tools"}),
        router_score=0.9,
        routes=(cheap, fast),
    )

    @tool
    def inspect_deployment() -> str:
        """Inspect the deployment and return its verbose log."""

        return "INFO deployment healthy\n" * 1_500

    scripted = ScriptedChatModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "inspect_deployment",
                        "args": {},
                        "id": "tool-call-1",
                        "type": "tool_call",
                    }
                ],
                usage_metadata=_usage(32, 12, cache_creation=8),
                response_metadata={
                    "headers": {
                        "x-promptrail-route-id": "cheap",
                        "x-promptrail-provider": "slow-provider",
                    }
                },
            ),
            AIMessage(
                content="Deployment is healthy.",
                usage_metadata=_usage(900, 16),
                response_metadata={"x-promptrail-provider": "slow-provider"},
            ),
        ]
    )
    factory = CapturingModelFactory(scripted)
    gateway = PromptRailGateway(policy_agent=SuppliedPolicyAgent(_policy()))
    middleware = PromptRailMiddleware(gateway=gateway, model_factory=factory)
    agent = create_agent(
        model=scripted,
        tools=[inspect_deployment],
        system_prompt="Never disclose customer secrets.",
        middleware=[middleware],
        context_schema=PromptRailContext,
    )
    context = PromptRailContext(
        session_id=f"langchain-{'async' if async_invocation else 'sync'}",
        enterprise_json_paths=(enterprise,),
        candidates=(candidate,),
        task="inspect a deployment with tools",
        predicted_output_tokens=64,
    )

    if async_invocation:
        result = asyncio.run(
            agent.ainvoke({"messages": [HumanMessage("Inspect the deployment.")]}, context=context)
        )
    else:
        result = agent.invoke(
            {"messages": [HumanMessage("Inspect the deployment.")]},
            context=context,
        )

    assert result["messages"][-1].content == "Deployment is healthy."
    assert "promptrail_run_id" not in result
    assert len(factory.prepared) == 2
    first, second = factory.prepared
    assert first.provider.mode is ProviderRoutingMode.DEADLINE
    assert first.provider.start_within_ms != 700
    assert factory.headers[0]["x-promptrail-start-within-ms"] == str(
        first.provider.start_within_ms
    )
    assert second.provider.mode is ProviderRoutingMode.STICKY
    assert second.provider.routes[0].provider == "slow-provider"
    assert "x-promptrail-start-within-ms" not in factory.headers[1]
    assert factory.headers[1]["x-promptrail-routing-mode"] == "standard"

    assert second.compaction.records
    second_request = scripted.requests[1]
    assert second_request[0].content == "Never disclose customer secrets."
    assert second_request[1].content == "Inspect the deployment."
    tool_message = next(message for message in second_request if isinstance(message, ToolMessage))
    assert "PromptRail compacted content" in str(tool_message.content)
    assert len(str(tool_message.content)) < len("INFO deployment healthy\n" * 1_500)

    run_id = first.budget.run_id
    snapshot = gateway.controller.snapshot(run_id)
    assert snapshot.status is RunStatus.COMPLETED
    assert snapshot.completed_calls == 2
    assert snapshot.tool_calls == 1
    record = second.compaction.records[0]
    with pytest.raises(CompactionError):
        gateway.compactor.store.get(
            session_id=context.session_id,
            retrieval_id=record.retrieval_id,
        )
