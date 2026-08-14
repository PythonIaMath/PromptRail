"""LangChain 1.x middleware for the PromptRail control plane.

Install :class:`PromptRailMiddleware` in ``create_agent(middleware=[...])`` and
pass a :class:`PromptRailContext` for each invocation.  LangChain remains an
optional dependency of the core package.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NotRequired, Protocol, cast, runtime_checkable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ToolCallRequest,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_openai_messages
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.runtime import Runtime
from langgraph.types import Command
from typing_extensions import override

from .cache import canonical_json
from .errors import IntegrationError
from .gateway import PromptRailGateway
from .models import (
    CallIntent,
    ModelCandidate,
    ModelUsage,
    PreparedCall,
    ProviderRoutingMode,
)

_RUN_ID = "promptrail_run_id"
_PENDING_CALL_ID = "promptrail_pending_call_id"


@dataclass(frozen=True)
class PromptRailContext:
    """Run-scoped inputs supplied through LangChain's ``context=`` argument."""

    session_id: str
    enterprise_json_paths: tuple[str | Path, ...]
    candidates: tuple[ModelCandidate, ...]
    task: str = "LangChain agent model call"
    max_output_tokens: int | None = None
    priority: float = 1.0
    required_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "enterprise_json_paths", tuple(self.enterprise_json_paths))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(
            self,
            "required_capabilities",
            frozenset(self.required_capabilities),
        )
        if not self.session_id.strip():
            raise ValueError("PromptRailContext.session_id cannot be empty")
        if not self.enterprise_json_paths:
            raise ValueError("PromptRailContext requires enterprise JSON paths")
        if not self.candidates:
            raise ValueError("PromptRailContext requires model candidates")


class PromptRailAgentState(AgentState[Any]):
    """Private lifecycle fields merged into the LangChain agent state."""

    promptrail_run_id: NotRequired[Annotated[str | None, PrivateStateAttr]]
    promptrail_pending_call_id: NotRequired[Annotated[str | None, PrivateStateAttr]]


@runtime_checkable
class LangChainModelFactory(Protocol):
    """Bind an authorized PromptRail plan to a concrete LangChain chat model.

    For ``deadline`` plans the factory normally returns a model configured for
    Provider_Router and applies ``provider_router_headers``.  For ``sticky`` and
    ``direct`` plans it must bind the sole route in ``prepared.provider.routes``
    directly, which is what preserves provider-local prompt caches.
    """

    def __call__(
        self,
        *,
        prepared: PreparedCall,
        provider_router_headers: Mapping[str, str],
    ) -> BaseChatModel: ...


@runtime_checkable
class ModelUsageExtractor(Protocol):
    """Convert a LangChain response into authoritative provider usage."""

    def __call__(
        self,
        *,
        prepared: PreparedCall,
        response: ModelResponse[Any],
        execution_latency_ms: int,
    ) -> ModelUsage: ...


@dataclass(frozen=True)
class _PendingModelCall:
    prepared: PreparedCall
    usage: ModelUsage


def provider_router_headers(prepared: PreparedCall) -> dict[str, str]:
    """Build the request headers understood by Provider_Router.

    Provider_Router requires ``start-within`` to be positive.  A zero window is
    therefore represented by omitting that header and using ``standard`` mode;
    the model factory must bind the plan's only route directly in that case.
    """

    deadline = prepared.provider.mode is ProviderRoutingMode.DEADLINE
    headers = {
        "x-promptrail-request-id": prepared.budget.call_id,
        "x-promptrail-routing-mode": "cheap_first" if deadline else "standard",
        "x-promptrail-policy-version": "v1",
    }
    if deadline:
        if prepared.provider.start_within_ms <= 0:
            raise IntegrationError("deadline provider plan requires a positive exploration window")
        headers["x-promptrail-start-within-ms"] = str(prepared.provider.start_within_ms)
    return headers


class LangChainUsageExtractor:
    """Read standard LangChain usage and PromptRail response headers."""

    def __call__(
        self,
        *,
        prepared: PreparedCall,
        response: ModelResponse[Any],
        execution_latency_ms: int,
    ) -> ModelUsage:
        message = next(
            (item for item in reversed(response.result) if isinstance(item, AIMessage)),
            None,
        )
        if message is None:
            raise IntegrationError("model response did not contain an AIMessage")

        metadata = message.response_metadata or {}
        usage = message.usage_metadata
        if usage is not None:
            input_tokens = self._count(usage.get("input_tokens"), "input_tokens")
            output_tokens = self._count(usage.get("output_tokens"), "output_tokens")
            details = usage.get("input_token_details") or {}
            cache_read_tokens = self._count(
                details.get("cache_read", 0),
                "input_token_details.cache_read",
            )
            cache_write_tokens = self._count(
                details.get("cache_creation", 0),
                "input_token_details.cache_creation",
            )
        else:
            raw_usage = self._usage_mapping(metadata)
            if raw_usage is None:
                raise IntegrationError(
                    "model response omitted token usage; configure the provider client to return it"
                )
            input_tokens = self._first_count(
                raw_usage,
                ("input_tokens", "prompt_tokens"),
                "input tokens",
            )
            output_tokens = self._first_count(
                raw_usage,
                ("output_tokens", "completion_tokens"),
                "output tokens",
            )
            prompt_details = raw_usage.get("prompt_tokens_details")
            if not isinstance(prompt_details, Mapping):
                prompt_details = {}
            cache_read_tokens = self._optional_count(
                raw_usage,
                prompt_details,
                keys=("cache_read_tokens", "cache_read_input_tokens", "cached_tokens"),
            )
            cache_write_tokens = self._optional_count(
                raw_usage,
                {},
                keys=("cache_write_tokens", "cache_creation_input_tokens"),
            )

        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=execution_latency_ms,
            provider=self._provider(prepared, metadata),
            model_id=prepared.model.candidate.model_id,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cost_usd=self._cost(metadata),
        )

    @staticmethod
    def _metadata_mappings(metadata: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        mappings: list[Mapping[str, Any]] = [metadata]
        for key in ("headers", "response_headers", "http_headers"):
            nested = metadata.get(key)
            if isinstance(nested, Mapping):
                mappings.append(nested)
        return tuple(mappings)

    @classmethod
    def _provider(cls, prepared: PreparedCall, metadata: Mapping[str, Any]) -> str:
        route_by_id = {route.route_id: route for route in prepared.provider.routes}
        allowed_providers = {route.provider for route in prepared.provider.routes}
        route_matches: set[str] = set()
        provider_matches: set[str] = set()
        for mapping in cls._metadata_mappings(metadata):
            normalized = {str(key).casefold(): value for key, value in mapping.items()}
            for key in ("x-promptrail-route-id", "route_id"):
                value = normalized.get(key)
                if value is not None and str(value) in route_by_id:
                    route_matches.add(str(value))
            for key in (
                "x-promptrail-provider",
                "provider",
                "provider_name",
                "model_provider",
            ):
                value = normalized.get(key)
                if value is not None and str(value) in allowed_providers:
                    provider_matches.add(str(value))

        if len(route_matches) > 1 or len(provider_matches) > 1:
            raise IntegrationError("model response contains conflicting provider routing metadata")
        if route_matches:
            route_provider = route_by_id[next(iter(route_matches))].provider
            if provider_matches and route_provider not in provider_matches:
                raise IntegrationError("provider and route response metadata disagree")
            return route_provider
        if provider_matches:
            return next(iter(provider_matches))
        if len(prepared.provider.routes) == 1:
            return prepared.provider.routes[0].provider
        raise IntegrationError(
            "provider-routed response omitted x-promptrail-route-id/provider metadata"
        )

    @staticmethod
    def _usage_mapping(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
        for key in ("token_usage", "usage", "usage_metadata"):
            value = metadata.get(key)
            if isinstance(value, Mapping):
                return value
        return None

    @classmethod
    def _cost(cls, metadata: Mapping[str, Any]) -> float | None:
        mappings = list(cls._metadata_mappings(metadata))
        usage = cls._usage_mapping(metadata)
        if usage is not None:
            mappings.append(usage)
        for mapping in mappings:
            for key in ("cost_usd", "total_cost_usd"):
                value = mapping.get(key)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise IntegrationError(f"{key} must be numeric")
                number = float(value)
                if not math.isfinite(number) or number < 0:
                    raise IntegrationError(f"{key} must be finite and non-negative")
                return number
        return None

    @staticmethod
    def _count(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise IntegrationError(f"{field} must be a non-negative integer")
        return value

    @classmethod
    def _first_count(
        cls,
        mapping: Mapping[str, Any],
        keys: tuple[str, ...],
        field: str,
    ) -> int:
        for key in keys:
            if key in mapping:
                return cls._count(mapping[key], key)
        raise IntegrationError(f"model response usage omitted {field}")

    @classmethod
    def _optional_count(
        cls,
        primary: Mapping[str, Any],
        secondary: Mapping[str, Any],
        *,
        keys: tuple[str, ...],
    ) -> int:
        for mapping in (primary, secondary):
            for key in keys:
                if key in mapping:
                    return cls._count(mapping[key], key)
        return 0


class PromptRailMiddleware(AgentMiddleware[PromptRailAgentState, PromptRailContext, Any]):
    """Apply PromptRail policy to every model call in a LangChain agent."""

    state_schema = PromptRailAgentState

    def __init__(
        self,
        *,
        gateway: PromptRailGateway,
        model_factory: LangChainModelFactory,
        usage_extractor: ModelUsageExtractor | None = None,
    ) -> None:
        super().__init__()
        self.gateway = gateway
        self._model_factory = model_factory
        self._usage_extractor = usage_extractor or LangChainUsageExtractor()
        self._pending: dict[str, _PendingModelCall] = {}
        self._pending_lock = threading.RLock()

    @override
    def before_agent(
        self,
        state: PromptRailAgentState,
        runtime: Runtime[PromptRailContext],
    ) -> dict[str, Any]:
        del state
        context = self._context(runtime)
        snapshot = self.gateway.start_run(
            session_id=context.session_id,
            enterprise_json_paths=context.enterprise_json_paths,
            candidates=context.candidates,
        )
        return {_RUN_ID: snapshot.run_id, _PENDING_CALL_ID: None}

    @override
    async def abefore_agent(
        self,
        state: PromptRailAgentState,
        runtime: Runtime[PromptRailContext],
    ) -> dict[str, Any]:
        del state
        context = self._context(runtime)
        snapshot = await asyncio.to_thread(
            self.gateway.start_run,
            session_id=context.session_id,
            enterprise_json_paths=context.enterprise_json_paths,
            candidates=context.candidates,
        )
        return {_RUN_ID: snapshot.run_id, _PENDING_CALL_ID: None}

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[PromptRailContext],
        handler: Callable[[ModelRequest[PromptRailContext]], ModelResponse[Any]],
    ) -> ExtendedModelResponse[Any]:
        run_id = self._run_id(request.state)
        intent = self._intent(request)
        prepared = self.gateway.prepare_call_sync(run_id=run_id, intent=intent)
        try:
            routed_request = self._routed_request(request, prepared)
        except BaseException:
            self.gateway.fail_model(prepared=prepared, billing_unknown=False)
            raise

        execution_started = time.perf_counter()
        try:
            response = handler(routed_request)
        except BaseException:
            self.gateway.fail_model(prepared=prepared, billing_unknown=True)
            raise
        latency_ms = math.ceil((time.perf_counter() - execution_started) * 1_000)
        try:
            usage = self._usage_extractor(
                prepared=prepared,
                response=response,
                execution_latency_ms=latency_ms,
            )
        except BaseException:
            self.gateway.fail_model(prepared=prepared, billing_unknown=True)
            raise
        self._store_pending(_PendingModelCall(prepared=prepared, usage=usage))
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={_PENDING_CALL_ID: prepared.budget.call_id}),
        )

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[PromptRailContext],
        handler: Callable[[ModelRequest[PromptRailContext]], Any],
    ) -> ExtendedModelResponse[Any]:
        run_id = self._run_id(request.state)
        intent = self._intent(request)
        prepared = await self.gateway.prepare_call(run_id=run_id, intent=intent)
        try:
            routed_request = self._routed_request(request, prepared)
        except BaseException:
            self.gateway.fail_model(prepared=prepared, billing_unknown=False)
            raise

        execution_started = time.perf_counter()
        try:
            response = await handler(routed_request)
        except BaseException:
            self.gateway.fail_model(prepared=prepared, billing_unknown=True)
            raise
        latency_ms = math.ceil((time.perf_counter() - execution_started) * 1_000)
        try:
            usage = self._usage_extractor(
                prepared=prepared,
                response=response,
                execution_latency_ms=latency_ms,
            )
        except BaseException:
            self.gateway.fail_model(prepared=prepared, billing_unknown=True)
            raise
        self._store_pending(_PendingModelCall(prepared=prepared, usage=usage))
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={_PENDING_CALL_ID: prepared.budget.call_id}),
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        run_id = self._run_id(request.state)
        try:
            return handler(request)
        finally:
            self.gateway.observe_tool(run_id=run_id)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        run_id = self._run_id(request.state)
        try:
            return await handler(request)
        finally:
            self.gateway.observe_tool(run_id=run_id)

    @override
    def after_model(
        self,
        state: PromptRailAgentState,
        runtime: Runtime[PromptRailContext],
    ) -> dict[str, Any]:
        del runtime
        pending = self._take_pending(self._pending_id(state))
        self.gateway.observe_model(prepared=pending.prepared, usage=pending.usage)
        return {_PENDING_CALL_ID: None}

    @override
    async def aafter_model(
        self,
        state: PromptRailAgentState,
        runtime: Runtime[PromptRailContext],
    ) -> dict[str, Any]:
        del runtime
        pending = self._take_pending(self._pending_id(state))
        await asyncio.to_thread(
            self.gateway.observe_model,
            prepared=pending.prepared,
            usage=pending.usage,
        )
        return {_PENDING_CALL_ID: None}

    @override
    def after_agent(
        self,
        state: PromptRailAgentState,
        runtime: Runtime[PromptRailContext],
    ) -> dict[str, Any]:
        del runtime
        self.gateway.finish_run(run_id=self._run_id(state), success=True)
        return {_RUN_ID: None, _PENDING_CALL_ID: None}

    @override
    async def aafter_agent(
        self,
        state: PromptRailAgentState,
        runtime: Runtime[PromptRailContext],
    ) -> dict[str, Any]:
        del runtime
        await asyncio.to_thread(
            self.gateway.finish_run,
            run_id=self._run_id(state),
            success=True,
        )
        return {_RUN_ID: None, _PENDING_CALL_ID: None}

    def _intent(self, request: ModelRequest[PromptRailContext]) -> CallIntent:
        context = self._context(request.runtime)
        messages = self._openai_messages(request)
        tools = tuple(self._tool_schema(tool) for tool in request.tools)
        capabilities = set(context.required_capabilities)
        if tools:
            capabilities.add("tools")
        if request.response_format is not None:
            capabilities.add("structured_output")
        return CallIntent(
            session_id=context.session_id,
            task=context.task,
            messages=messages,
            tools=tools,
            required_capabilities=frozenset(capabilities),
            max_output_tokens=context.max_output_tokens,
            priority=context.priority,
        )

    def _routed_request(
        self,
        request: ModelRequest[PromptRailContext],
        prepared: PreparedCall,
    ) -> ModelRequest[PromptRailContext]:
        headers = provider_router_headers(prepared)
        model = self._model_factory(
            prepared=prepared,
            provider_router_headers=headers,
        )
        if not isinstance(model, BaseChatModel):
            raise IntegrationError("model_factory must return a LangChain BaseChatModel")

        original: list[BaseMessage] = []
        if request.system_message is not None:
            original.append(request.system_message)
        original.extend(request.messages)
        compacted = prepared.compaction.messages
        if len(original) != len(compacted):
            raise IntegrationError("compacted context no longer matches LangChain messages")

        transformed: list[BaseMessage] = []
        raw_before = self._openai_messages(request)
        for message, before, after in zip(original, raw_before, compacted, strict=True):
            if canonical_json(before) == canonical_json(after):
                transformed.append(message)
                continue
            changed_keys = {
                key
                for key in before.keys() | after.keys()
                if canonical_json(before.get(key)) != canonical_json(after.get(key))
            }
            if changed_keys != {"content"}:
                raise IntegrationError(
                    "context compaction attempted to change non-content message fields"
                )
            transformed.append(message.model_copy(update={"content": after["content"]}))

        system_message = None
        if request.system_message is not None:
            system_message = cast(Any, transformed.pop(0))
        return request.override(
            model=model,
            messages=transformed,
            system_message=system_message,
        )

    @staticmethod
    def _openai_messages(
        request: ModelRequest[PromptRailContext],
    ) -> tuple[dict[str, Any], ...]:
        messages: list[BaseMessage] = []
        if request.system_message is not None:
            messages.append(request.system_message)
        messages.extend(request.messages)
        converted = convert_to_openai_messages(messages, include_id=False)
        if not isinstance(converted, list) or len(converted) != len(messages):
            raise IntegrationError("LangChain message conversion was not one-to-one")
        if not all(isinstance(item, dict) for item in converted):
            raise IntegrationError("LangChain returned an invalid converted message")
        return tuple(cast(dict[str, Any], item) for item in converted)

    @staticmethod
    def _tool_schema(tool: BaseTool | dict[str, Any]) -> dict[str, Any]:
        try:
            converted = convert_to_openai_tool(tool) if isinstance(tool, BaseTool) else dict(tool)
        except (TypeError, ValueError) as exc:
            raise IntegrationError("could not serialize a LangChain tool schema") from exc
        return cast(dict[str, Any], converted)

    @staticmethod
    def _context(runtime: Runtime[PromptRailContext] | None) -> PromptRailContext:
        if runtime is None or not isinstance(runtime.context, PromptRailContext):
            raise IntegrationError(
                "invoke the agent with context=PromptRailContext(...) and "
                "context_schema=PromptRailContext"
            )
        return runtime.context

    @staticmethod
    def _run_id(state: Mapping[str, Any]) -> str:
        run_id = state.get(_RUN_ID)
        if not isinstance(run_id, str) or not run_id:
            raise IntegrationError("PromptRail before_agent did not establish a run")
        return run_id

    @staticmethod
    def _pending_id(state: Mapping[str, Any]) -> str:
        call_id = state.get(_PENDING_CALL_ID)
        if not isinstance(call_id, str) or not call_id:
            raise IntegrationError("PromptRail model call has no pending usage record")
        return call_id

    def _store_pending(self, pending: _PendingModelCall) -> None:
        call_id = pending.prepared.budget.call_id
        with self._pending_lock:
            if call_id in self._pending:
                raise IntegrationError(f"duplicate pending PromptRail call: {call_id}")
            self._pending[call_id] = pending

    def _take_pending(self, call_id: str) -> _PendingModelCall:
        with self._pending_lock:
            pending = self._pending.pop(call_id, None)
        if pending is None:
            raise IntegrationError(f"unknown or already-recorded PromptRail call: {call_id}")
        return pending


__all__ = [
    "LangChainModelFactory",
    "LangChainUsageExtractor",
    "ModelUsageExtractor",
    "PromptRailAgentState",
    "PromptRailContext",
    "PromptRailMiddleware",
    "provider_router_headers",
]
