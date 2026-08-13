"""
Hermes adapter for LeRouter selection mode.

Flow:
  Hermes request
    -> LeRouterClient.select_route(...)
    -> Hermes native model adapter executes selected_model_id/native_model_id
    -> LeRouterClient.log_usage(...)

Required env:
  LEROUTER_API_URL=https://your-lerouter-api.modal.run
  LEROUTER_AGENT_TOKEN=lr_live_...

Optional env:
  LEROUTER_USER_ID=hermes
  LEROUTER_ROUTE_ID=default
  LEROUTER_INFERENCE_MODE=user_managed
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from typing import Any

try:
    import certifi
except Exception:
    certifi = None


ModelExecutor = Callable[..., dict[str, Any]]
StreamingModelExecutor = Callable[..., Iterable[Any]]
INFERENCE_MODES = {"user_managed", "router_managed"}


def normalize_inference_mode(value: Any, fallback: str = "user_managed") -> str:
    mode = str(value or fallback).strip().lower().replace("-", "_")
    if mode == "lerouter_managed":
        return "router_managed"
    return mode if mode in INFERENCE_MODES else fallback


def normalize_selection(selection: dict[str, Any]) -> dict[str, Any]:
    selected_model = selection.get("selected_model")
    if not isinstance(selected_model, dict):
        selected_model = selection.get("best_model")
    if not isinstance(selected_model, dict):
        selected_model = {}
    if not selected_model:
        return selection
    return {
        **selection,
        "selected_model": selected_model,
        "selected_model_id": selection.get("selected_model_id") or selected_model.get("model_id"),
        "native_model_id": selection.get("native_model_id") or selected_model.get("native_model_id"),
        "provider": selection.get("provider") or selected_model.get("provider"),
    }


class LeRouterClient:
    def __init__(
        self,
        *,
        api_url: str | None = None,
        agent_token: str | None = None,
        user_id: str | None = None,
        route_id: str | None = None,
        inference_mode: str | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        self.api_url = (api_url or os.environ["LEROUTER_API_URL"]).rstrip("/")
        self.agent_token = agent_token or os.environ.get("LEROUTER_AGENT_TOKEN")
        self.user_id = user_id or os.environ.get("LEROUTER_USER_ID", "hermes")
        self.route_id = route_id or os.environ.get("LEROUTER_ROUTE_ID", "default")
        self.inference_mode = normalize_inference_mode(
            inference_mode or os.environ.get("LEROUTER_INFERENCE_MODE"),
            "user_managed",
        )
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Hermes-LeRouterProvider/1.0",
        }
        if self.agent_token:
            headers["Authorization"] = f"Bearer {self.agent_token}"
        return headers

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        context = ssl.create_default_context(cafile=certifi.where()) if certifi else None
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LeRouter HTTP {error.code}: {body}") from error

    def setup_candidate_models(
        self,
        *,
        routes: dict[str, Any],
        model_catalog: list[dict[str, Any]] | None = None,
        candidates_per_route: int = 5,
        metadata: dict[str, Any] | None = None,
        update_schedule: str | None = None,
    ) -> dict[str, Any]:
        path = "/agent/setup" if update_schedule else "/agent/candidate-models"
        setup_metadata = {
            "source": "hermes",
            "inference_mode": self.inference_mode,
            **(metadata or {}),
        }
        return self._post(
            path,
            {
                "user_id": self.user_id,
                "route_id": self.route_id,
                "routes": routes,
                "model_catalog": model_catalog,
                "candidates_per_route": candidates_per_route,
                "update_schedule": update_schedule,
                "metadata": setup_metadata,
            },
        )

    @staticmethod
    def setup_models_message(setup_result: dict[str, Any]) -> str:
        summary = setup_result.get("catalog_summary") or setup_result.get("setup", {}).get("catalog_summary") or {}
        models = summary.get("routable_models") or []
        providers = summary.get("providers") or []
        routes = summary.get("routes") or {}
        if not models:
            return "LeRouter setup completed, but no routable model list was returned."
        lines = [
            "LeRouter is configured to route across these models:",
            *[f"- {model}" for model in models],
            "",
            f"Providers: {', '.join(providers) if providers else 'unknown'}",
            f"Routes covered: {len(routes)}",
        ]
        return "\n".join(lines)

    def select_route(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        previous_query_data: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        provider_options: dict[str, Any] | None = None,
        max_candidates: int = 5,
    ) -> dict[str, Any]:
        return self._post(
            "/lerouter/select",
            {
                "user_id": self.user_id,
                "route_id": self.route_id,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "response_format": response_format,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
                "previous_query_data": previous_query_data,
                "budget": budget,
                "provider_options": provider_options or {},
                "max_candidates": max_candidates,
                "execute": False,
                "inference_mode": "user_managed",
            },
        )

    def log_usage(
        self,
        *,
        selection: dict[str, Any],
        response: dict[str, Any] | None = None,
        success: bool = True,
        spend_usd: float = 0.0,
        metadata: dict[str, Any] | None = None,
        update_counters: bool = True,
    ) -> dict[str, Any]:
        response = response or {}
        selected_model_id = selection.get("selected_model_id")
        provider = selection.get("provider")
        usage = response.get("usage") if isinstance(response, dict) else None
        return self._post(
            "/lerouter/usage-log",
            {
                "user_id": self.user_id,
                "route_id": selection.get("route_id") or self.route_id,
                "route_name": selection.get("route_name"),
                "model_id": response.get("model") or selected_model_id,
                "provider": response.get("provider") or provider,
                "inference_mode": "user_managed",
                "success": success,
                "spend_usd": response.get("spend_usd", spend_usd),
                "metadata": {
                    "source": "hermes_native_adapter",
                    "selection": selection,
                    "usage": usage or {},
                    **(metadata or {}),
                },
                "update_counters": update_counters,
            },
        )

    def chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        execute_model: ModelExecutor | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        previous_query_data: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        provider_options: dict[str, Any] | None = None,
        max_candidates: int = 5,
        inference_mode: str | None = None,
        execute_in_lerouter: bool | None = None,
    ) -> dict[str, Any]:
        mode = normalize_inference_mode(inference_mode, self.inference_mode)
        if execute_in_lerouter is not None:
            mode = "router_managed" if execute_in_lerouter else "user_managed"

        if mode == "router_managed":
            return self._legacy_modal_completion(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
                previous_query_data=previous_query_data,
                budget=budget,
                provider_options=provider_options,
                max_candidates=max_candidates,
            )

        if execute_model is None:
            raise ValueError("execute_model is required so Hermes can use its native model adapter.")

        selection = normalize_selection(self.select_route(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            previous_query_data=previous_query_data,
            budget=budget,
            provider_options=provider_options,
            max_candidates=max_candidates,
        ))

        started = time.perf_counter()
        try:
            response = execute_model(
                model=selection.get("selected_model"),
                model_id=selection.get("selected_model_id"),
                native_model_id=selection.get("native_model_id") or selection.get("selected_model_id"),
                provider=selection.get("provider"),
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
                routing=selection,
            )
        except Exception:
            self.log_usage(
                selection=selection,
                success=False,
                metadata={"latency_ms": round((time.perf_counter() - started) * 1000, 2)},
            )
            raise

        self.log_usage(
            selection=selection,
            response=response,
            success=True,
            metadata={"latency_ms": round((time.perf_counter() - started) * 1000, 2)},
        )
        return {
            **response,
            "routing": selection,
        }

    def _legacy_modal_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        previous_query_data: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        provider_options: dict[str, Any] | None = None,
        max_candidates: int = 5,
    ) -> dict[str, Any]:
        result = self._post(
            "/lerouter/route",
            {
                "user_id": self.user_id,
                "route_id": self.route_id,
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "response_format": response_format,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
                "previous_query_data": previous_query_data,
                "budget": budget,
                "provider_options": provider_options or {},
                "max_candidates": max_candidates,
                "execute": True,
                "inference_mode": "router_managed",
            },
        )
        return {
            "content": result.get("response", {}).get("content", ""),
            "tool_calls": result.get("response", {}).get("tool_calls", []),
            "model": result.get("response", {}).get("model"),
            "provider": result.get("response", {}).get("provider"),
            "usage": result.get("response", {}).get("usage", {}),
            "routing": {
                "route_id": result.get("route_id"),
                "route_name": result.get("route_name"),
                "best_model": result.get("best_model"),
                "provider_attempts": result.get("provider_attempts", []),
                "estimated_spend_usd": result.get("estimated_spend_usd"),
            },
            "raw_lerouter": result,
        }

    def stream_chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        execute_model_stream: StreamingModelExecutor,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        previous_query_data: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        provider_options: dict[str, Any] | None = None,
        max_candidates: int = 5,
        inference_mode: str | None = None,
    ) -> Iterator[Any]:
        mode = normalize_inference_mode(inference_mode, self.inference_mode)
        if mode == "router_managed":
            raise ValueError(
                "Use Hermes' OpenAI-compatible streaming client against "
                f"{self.api_url}/v1/chat/completions for router_managed streaming."
            )

        selection = normalize_selection(self.select_route(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            previous_query_data=previous_query_data,
            budget=budget,
            provider_options=provider_options,
            max_candidates=max_candidates,
        ))

        started = time.perf_counter()
        stream = execute_model_stream(
            model=selection.get("selected_model"),
            model_id=selection.get("selected_model_id"),
            native_model_id=selection.get("native_model_id") or selection.get("selected_model_id"),
            provider=selection.get("provider"),
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            routing=selection,
        )

        success = False
        try:
            for chunk in stream:
                yield chunk
            success = True
        finally:
            self.log_usage(
                selection=selection,
                success=success,
                metadata={
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "stream": True,
                },
            )
