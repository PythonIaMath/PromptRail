"""Narrow HTTP adapters for the existing LeRouter services."""

from __future__ import annotations

import copy
import json
import math
import os
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from .budgeting import BudgetModelResponse
from .errors import PolicyError, RoutingError
from .models import CallIntent, ModelCandidate, OutputLengthPrediction

PRODUCTION_LEROUTER_RANKER_URL = (
    "https://promptrail--lerouter-routing-worker-lerouterroutingworker-web.modal.run"
)
LEROUTER_RANKER_URL_ENV = "LEROUTER_ROUTING_WORKER_URL"
LEROUTER_RANKER_TOKEN_ENV = "PROMPTRAIL_BIENCODEUR_SERVICE_TOKEN"
LEROUTER_RANKER_RUN_ID_ENV = "LEROUTER_GEMMA4_ROUTER_RUN_ID"
LEROUTER_RANKER_CONTRACT_VERSION = "infinite-router-v2"
LEROUTER_BIENCODER_MODEL_ID = "google/gemma-4-12B"
PRODUCTION_LEROUTER_RUN_ID = "gemma4-12b-biencoder-debiased-v2-20260725"
PRODUCTION_LEROUTER_LENGTH_PREDICTOR_URL = (
    "https://buygenius-savings--lerouter-lenght-prediction-output-len-71bcf6.modal.run"
)
LEROUTER_LENGTH_PREDICTOR_URL_ENV = "LEROUTER_LENGTH_PREDICTOR_URL"
LEROUTER_INTERNAL_SERVICE_TOKEN_ENV = "LEROUTER_INTERNAL_SERVICE_TOKEN"


def _strict_json_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize Pydantic JSON Schema for strict OpenAI-compatible outputs."""

    normalized = copy.deepcopy(dict(schema))

    def visit(node: Any) -> Any:
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node

        for definitions_key in ("$defs", "definitions"):
            definitions = node.get(definitions_key)
            if isinstance(definitions, dict):
                node[definitions_key] = {key: visit(value) for key, value in definitions.items()}

        properties = node.get("properties")
        if isinstance(properties, dict):
            node["properties"] = {key: visit(value) for key, value in properties.items()}
            node["required"] = list(properties)
        if node.get("type") == "object":
            node.setdefault("additionalProperties", False)

        items = node.get("items")
        if isinstance(items, dict):
            node["items"] = visit(items)
        for union_key in ("anyOf", "oneOf", "allOf"):
            variants = node.get(union_key)
            if isinstance(variants, list):
                node[union_key] = [visit(item) for item in variants]

        if node.get("default", object()) is None:
            node.pop("default", None)
        return node

    return visit(normalized)


def _post_json(
    *,
    url: str,
    bearer_token: str,
    payload: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise ValueError("service URL must use HTTPS or loopback HTTP")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "promptrail/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            context=ssl.create_default_context(),
            timeout=timeout_seconds,
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read(2_000).decode("utf-8", errors="replace")
        raise RuntimeError(f"service HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(f"service request failed: {type(error).__name__}") from error
    if not isinstance(result, dict):
        raise RuntimeError("service returned a non-object response")
    return result


class LeRouterOutputLengthPredictor:
    """Fail-closed adapter for LeRouter's deployed ModernBERT predictor."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        service_token: str,
        timeout_seconds: float = 120.0,
        client: Any | None = None,
    ) -> None:
        if not endpoint_url.strip():
            raise ValueError("output-length predictor endpoint_url is required")
        if not service_token.strip():
            raise ValueError("output-length predictor service_token is required")
        if timeout_seconds <= 0:
            raise ValueError("output-length predictor timeout_seconds must be positive")
        self._url = endpoint_url.strip()
        self._token = service_token.strip()
        self._timeout_seconds = timeout_seconds
        try:
            import httpx
        except ImportError as error:  # pragma: no cover - exercised without the HTTP extra.
            raise RuntimeError(
                "LeRouterOutputLengthPredictor requires the promptrail[http] extra"
            ) from error
        self._client = client or httpx.Client(
            http2=True,
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "promptrail/0.1",
            },
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float = 120.0,
    ) -> LeRouterOutputLengthPredictor:
        values = os.environ if environ is None else environ
        token = values.get(LEROUTER_INTERNAL_SERVICE_TOKEN_ENV, "").strip()
        if not token:
            raise ValueError(
                f"{LEROUTER_INTERNAL_SERVICE_TOKEN_ENV} is required for output prediction"
            )
        return cls(
            endpoint_url=values.get(
                LEROUTER_LENGTH_PREDICTOR_URL_ENV,
                PRODUCTION_LEROUTER_LENGTH_PREDICTOR_URL,
            ),
            service_token=token,
            timeout_seconds=timeout_seconds,
        )

    def predict(
        self,
        *,
        messages: tuple[dict[str, Any], ...],
        max_output_tokens: int | None = None,
    ) -> OutputLengthPrediction:
        import time

        started = time.perf_counter()
        try:
            response = self._client.post(
                self._url,
                json={
                    "verbosity_multiplier": 1.0,
                    "prompt": self.format_conversation(messages),
                },
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as error:
            raise RuntimeError(
                f"output-length predictor request failed: {type(error).__name__}"
            ) from error
        if not isinstance(result, dict):
            raise RuntimeError("output-length predictor returned a non-object response")
        raw = result.get("predicted_tokens")
        if raw is None:
            raw = result.get("predicted_tokens_rounded")
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise RuntimeError("output-length predictor returned no numeric prediction")
        value = float(raw)
        if not math.isfinite(value) or value <= 0:
            raise RuntimeError("output-length predictor returned a non-positive prediction")
        predicted = max(1, round(value))
        if max_output_tokens is not None:
            predicted = min(predicted, max_output_tokens)
        return OutputLengthPrediction(
            predicted_tokens=predicted,
            raw_predicted_tokens=value,
            latency_ms=math.ceil((time.perf_counter() - started) * 1_000),
        )

    @staticmethod
    def format_conversation(messages: tuple[dict[str, Any], ...]) -> str:
        rendered: list[str] = []
        for message in messages:
            role = str(message.get("role") or "unknown").upper()
            content = message.get("content")
            if isinstance(content, str):
                text = content
            else:
                try:
                    text = json.dumps(content, ensure_ascii=False, sort_keys=True)
                except (TypeError, ValueError):
                    text = str(content)
            rendered.append(f"{role}:\n{text}")
        return "\n\n".join(rendered)


class LeRouterPolicyGenerator:
    """Generate the enterprise policy through LeRouter's routed model endpoint."""

    def __init__(
        self,
        *,
        api_url: str,
        agent_token: str,
        user_id: str,
        route_id: str = "default",
        timeout_seconds: float = 120.0,
    ) -> None:
        self._url = f"{api_url.rstrip('/')}/lerouter/route"
        self._token = agent_token
        self._user_id = user_id
        self._route_id = route_id
        self._timeout_seconds = timeout_seconds

    def generate_policy(
        self,
        *,
        system_instruction: str,
        enterprise_data: Mapping[str, Any],
        output_schema: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        response = _post_json(
            url=self._url,
            bearer_token=self._token,
            payload={
                "user_id": self._user_id,
                "route_id": self._route_id,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"enterprise_data": enterprise_data},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "promptrail_operating_policy",
                        "strict": True,
                        "schema": _strict_json_schema(output_schema),
                    },
                },
                "temperature": 0,
                "max_tokens": 2_500,
                "execute": True,
                "inference_mode": "router_managed",
            },
            timeout_seconds=self._timeout_seconds,
        )
        normalized = response.get("response")
        if not isinstance(normalized, Mapping):
            raise PolicyError("LeRouter policy response has no normalized model response")
        content = normalized.get("content")
        if not isinstance(content, str) or not content.strip():
            raise PolicyError("LeRouter policy response has no content")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise PolicyError("LeRouter policy response is not valid JSON") from error
        if not isinstance(parsed, dict):
            raise PolicyError("LeRouter policy response must be a JSON object")
        return parsed


class LeRouterHTTPRanker:
    """Call LeRouter's deployed Gemma routing worker.

    The production ``infinite_route_v2`` boundary computes and caches model
    profile embeddings on a cache miss. PromptRail can therefore use the real
    bi-encoder before offline profile embeddings are available, without a local
    heuristic or synthetic embedding fallback.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        service_token: str,
        timeout_seconds: float = 30.0,
        expected_model_id: str = LEROUTER_BIENCODER_MODEL_ID,
        expected_model_run_id: str = PRODUCTION_LEROUTER_RUN_ID,
    ) -> None:
        if not endpoint_url.strip():
            raise ValueError("LeRouter endpoint_url is required")
        if not service_token.strip():
            raise ValueError("LeRouter service_token is required")
        if len(service_token) > 4_096 or any(ord(character) < 32 for character in service_token):
            raise ValueError("LeRouter service_token is invalid")
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not expected_model_id.strip():
            raise ValueError("expected_model_id is required")
        if not expected_model_run_id.strip():
            raise ValueError("expected_model_run_id is required")
        self._url = endpoint_url.strip()
        self._token = service_token.strip()
        self._timeout_seconds = timeout_seconds
        self._expected_model_id = expected_model_id.strip()
        self._expected_model_run_id = expected_model_run_id.strip()

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> LeRouterHTTPRanker:
        """Build the fail-closed production ranker from deployment settings."""

        values = os.environ if environ is None else environ
        endpoint_url = values.get(
            LEROUTER_RANKER_URL_ENV,
            PRODUCTION_LEROUTER_RANKER_URL,
        ).strip()
        service_token = values.get(LEROUTER_RANKER_TOKEN_ENV, "").strip()
        if not service_token:
            raise ValueError(
                f"{LEROUTER_RANKER_TOKEN_ENV} is required for production LeRouter routing"
            )
        expected_run_id = values.get(
            LEROUTER_RANKER_RUN_ID_ENV,
            PRODUCTION_LEROUTER_RUN_ID,
        ).strip()
        return cls(
            endpoint_url=endpoint_url,
            service_token=service_token,
            timeout_seconds=timeout_seconds,
            expected_model_run_id=expected_run_id,
        )

    def rank(
        self,
        *,
        intent: CallIntent,
        candidates: tuple[ModelCandidate, ...],
        timeout_ms: int,
    ) -> dict[str, float]:
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise RoutingError("LeRouter timeout_ms must be a positive integer")
        if not candidates or len(candidates) > 256:
            raise RoutingError("LeRouter requires between 1 and 256 model candidates")
        capabilities = sorted(intent.required_capabilities)
        if len(capabilities) > 20 or any(len(item) > 100 for item in capabilities):
            raise RoutingError("LeRouter capability requirements exceed its request contract")
        candidate_payloads = [self._candidate_payload(candidate) for candidate in candidates]
        result = _post_json(
            url=self._url,
            bearer_token=self._token,
            payload={
                "mode": "infinite_route_v2",
                "semantic_context": {
                    "current_user_prompt": self._routing_prompt(intent),
                    "previous_user_prompt": self._message_text(intent, "user", -2),
                    "previous_assistant_summary": self._message_text(
                        intent,
                        "assistant",
                        -1,
                    ),
                    "client_type": "promptrail_gateway",
                    "tool_presence": bool(intent.tools),
                    "capability_requirements": capabilities,
                    **(
                        {"requested_output_tokens": intent.predicted_output_tokens}
                        if intent.predicted_output_tokens is not None
                        else {}
                    ),
                },
                "candidates": candidate_payloads,
            },
            timeout_seconds=min(self._timeout_seconds, timeout_ms / 1_000),
        )
        if result.get("contract_version") != LEROUTER_RANKER_CONTRACT_VERSION:
            raise RoutingError("LeRouter response has an unexpected contract version")
        if result.get("branch") != "free":
            raise RoutingError("LeRouter semantic ranker did not execute its Gemma branch")
        models = result.get("models")
        if not isinstance(models, Mapping):
            raise RoutingError("LeRouter response has no model identity evidence")
        if models.get("semantic") != self._expected_model_id:
            raise RoutingError("LeRouter response came from an unexpected semantic model")
        if models.get("semantic_run") != self._expected_model_run_id:
            raise RoutingError("LeRouter response came from an unexpected semantic model run")
        ranked = result.get("ranked")
        if not isinstance(ranked, list):
            raise RoutingError("LeRouter response has no ranked candidates")
        scores: dict[str, float] = {}
        ranks: set[int] = set()
        for item in ranked:
            if not isinstance(item, Mapping):
                raise RoutingError("LeRouter ranked candidate is not an object")
            model_id = str(item.get("candidate_id") or "").strip()
            score = item.get("predicted_success")
            rank = item.get("rank")
            if not model_id or model_id in scores:
                raise RoutingError("LeRouter returned a missing or duplicate candidate identity")
            if isinstance(score, bool) or not isinstance(score, int | float):
                raise RoutingError(f"LeRouter returned no routing score for {model_id!r}")
            normalized_score = float(score)
            if not math.isfinite(normalized_score) or not 0 <= normalized_score <= 1:
                raise RoutingError(f"LeRouter returned an invalid routing score for {model_id!r}")
            if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
                raise RoutingError(f"LeRouter returned an invalid rank for {model_id!r}")
            scores[model_id] = normalized_score
            ranks.add(rank)
        expected_ids = {candidate.model_id for candidate in candidates}
        if set(scores) != expected_ids or ranks != set(range(1, len(candidates) + 1)):
            raise RoutingError("LeRouter returned an incomplete or foreign candidate ranking")
        return scores

    @staticmethod
    def _candidate_payload(candidate: ModelCandidate) -> dict[str, Any]:
        router_payload = candidate.router_payload
        raw_forces = router_payload.get("forces")
        if raw_forces is None:
            forces = sorted(candidate.strengths | candidate.capabilities)
            if not forces:
                forces = [f"general language model {candidate.model_id}"]
        elif (
            not isinstance(raw_forces, list | tuple)
            or not raw_forces
            or any(not isinstance(item, str) or not item.strip() for item in raw_forces)
        ):
            raise RoutingError(f"{candidate.model_id} has invalid LeRouter forces")
        else:
            forces = [item.strip() for item in raw_forces]
        if len(forces) > 40 or any(len(item) > 500 for item in forces):
            raise RoutingError(f"{candidate.model_id} LeRouter forces exceed contract bounds")

        raw_profile = router_payload.get("profile_text")
        if raw_profile is None:
            profile_text = f"{candidate.model_id}: {', '.join(forces)}"
        elif not isinstance(raw_profile, str) or not raw_profile.strip():
            raise RoutingError(f"{candidate.model_id} has invalid LeRouter profile_text")
        else:
            profile_text = raw_profile.strip()
        if len(profile_text) > 4_000:
            raise RoutingError(f"{candidate.model_id} LeRouter profile_text exceeds 4,000 chars")

        benchmark_results = router_payload.get("benchmark_results", {})
        if not isinstance(benchmark_results, Mapping):
            raise RoutingError(f"{candidate.model_id} has invalid LeRouter benchmark_results")
        return {
            "candidate_id": candidate.model_id,
            "model_id": candidate.model_id,
            "profile_text": profile_text,
            "completion_success_prior": candidate.quality,
            "forces": forces,
            "context_window": candidate.context_window_tokens,
            "benchmark_results": dict(benchmark_results),
        }

    @classmethod
    def _routing_prompt(cls, intent: CallIntent) -> str:
        latest_user = cls._message_text(intent, "user", -1)
        if not latest_user or latest_user.strip() == intent.task:
            return intent.task[:8_192]
        return f"Task: {intent.task}\n\nCurrent user message:\n{latest_user}"[:8_192]

    @staticmethod
    def _message_text(intent: CallIntent, role: str, position: int) -> str:
        matching = [
            message.get("content")
            for message in intent.messages
            if str(message.get("role") or "").casefold() == role
        ]
        if not matching or not -len(matching) <= position < len(matching):
            return ""
        value = matching[position]
        if isinstance(value, str):
            return value[:2_048]
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)[:2_048]
        except (TypeError, ValueError):
            return ""


class Gemma12BHTTPGenerator:
    """Call a dedicated OpenAI-compatible Gemma 12B generation endpoint.

    This endpoint is deliberately separate from LeRouter's Gemma bi-encoder
    ranker. The returned model identity is verified by Gemma12BBudgetAllocator.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        service_token: str,
        timeout_seconds: float = 60.0,
        max_output_tokens: int = 800,
    ) -> None:
        if not endpoint_url.strip():
            raise ValueError("Gemma endpoint_url is required")
        if not service_token.strip():
            raise ValueError("Gemma service_token is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        self._url = endpoint_url.strip()
        self._token = service_token.strip()
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens

    def generate_budget(
        self,
        *,
        model_id: str,
        system_instruction: str,
        request: Mapping[str, Any],
        output_schema: Mapping[str, Any],
    ) -> BudgetModelResponse:
        response = _post_json(
            url=self._url,
            bearer_token=self._token,
            payload={
                "model": model_id,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"budget_allocation_request": dict(request)},
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                        ),
                    },
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "promptrail_call_budget",
                        "strict": True,
                        "schema": _strict_json_schema(output_schema),
                    },
                },
                "temperature": 0,
                "max_tokens": self._max_output_tokens,
                "stream": False,
            },
            timeout_seconds=self._timeout_seconds,
        )
        returned_model = response.get("model")
        if not isinstance(returned_model, str) or not returned_model.strip():
            raise RuntimeError("Gemma budget response omitted its model identity")
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("Gemma budget response has no choices")
        first = choices[0]
        message = first.get("message") if isinstance(first, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Gemma budget response has no JSON content")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError("Gemma budget response content is not valid JSON") from error
        if not isinstance(payload, dict):
            raise RuntimeError("Gemma budget response content must be a JSON object")
        return BudgetModelResponse(payload=payload, model_id=returned_model.strip())
