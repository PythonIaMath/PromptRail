"""Narrow HTTP adapters for the existing LeRouter services."""

from __future__ import annotations

import copy
import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from .errors import PolicyError, RoutingError
from .models import CallIntent, ModelCandidate


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
                node[definitions_key] = {
                    key: visit(value) for key, value in definitions.items()
                }

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
    """Call the deployed Gemma catalog ranker and return its probabilities."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        service_token: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._url = endpoint_url
        self._token = service_token
        self._timeout_seconds = timeout_seconds

    def rank(
        self,
        *,
        intent: CallIntent,
        candidates: tuple[ModelCandidate, ...],
    ) -> dict[str, float]:
        catalog = []
        for candidate in candidates:
            payload = dict(candidate.router_payload)
            payload.setdefault("model_id", candidate.model_id)
            if "gemma4_profile_embedding" not in payload:
                raise RoutingError(
                    f"{candidate.model_id} is missing its LeRouter profile embedding"
                )
            catalog.append(payload)
        result = _post_json(
            url=self._url,
            bearer_token=self._token,
            payload={
                "task": intent.task,
                "catalog_models": catalog,
                "expected_output_k_tokens": intent.predicted_output_tokens / 1_000,
            },
            timeout_seconds=self._timeout_seconds,
        )
        gemma = result.get("gemma4")
        ranked = gemma.get("ranked") if isinstance(gemma, Mapping) else None
        if not isinstance(ranked, list):
            raise RoutingError("LeRouter response has no Gemma ranked candidates")
        scores: dict[str, float] = {}
        for item in ranked:
            if not isinstance(item, Mapping):
                raise RoutingError("LeRouter ranked candidate is not an object")
            model_id = str(item.get("model") or "")
            probability = item.get("probability")
            if isinstance(probability, bool) or not isinstance(probability, int | float):
                raise RoutingError(f"LeRouter returned no probability for {model_id!r}")
            scores[model_id] = float(probability)
        return scores
