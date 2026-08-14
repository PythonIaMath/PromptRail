"""Loopback provider meters for the real Codex comparison demo."""

from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

try:
    from .credentials import ensure_internal_service_token
except ImportError:  # Direct script execution from the repository root.
    from credentials import ensure_internal_service_token

from promptrail import (
    BudgetAllocationDecision,
    BudgetError,
    CacheAwareLeRouter,
    CallIntent,
    ModelCandidate,
    OperatingPolicy,
    PromptRailGateway,
    ProviderRoute,
    RunStatus,
    SuppliedPolicyAgent,
)
from promptrail.controller import GlobalController
from promptrail.models import ModelUsage

OPENROUTER_HOST = "openrouter.ai"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENAI_HOST = "api.openai.com"
MAX_BODY_BYTES = 16 * 1024 * 1024
PROXY_TOKEN = "loopback-only-demo"
MONGO_TIMEOUT_MS = 15_000
RATE_LIMIT_COOLDOWN_SECONDS = 120
NON_ROUTABLE_UPSTREAM_STATUSES = frozenset({401, 402})
PORTABLE_TOOL_INSTRUCTION = (
    "Call only function tools whose exact names appear in the tools list. "
    "To finish the turn, return a normal assistant message; never call a tool named final."
)


def require_paid_controller_model(model: str) -> str:
    model_id = model.strip()
    if not model_id or model_id.endswith(":free"):
        raise RuntimeError(
            "PromptRail controller_model must be an explicit paid OpenRouter model slug"
        )
    return model_id


def should_reroute_provider_error(
    lane: str,
    status: int,
    prepared: Any,
    attempt: int,
    max_attempts: int,
) -> bool:
    return (
        lane == "managed"
        and status >= 400
        and status not in NON_ROUTABLE_UPSTREAM_STATUSES
        and prepared is not None
        and attempt < max_attempts
    )


def should_reroute_rate_limit(
    lane: str,
    status: int,
    prepared: Any,
    attempt: int,
    max_attempts: int,
) -> bool:
    """Backward-compatible alias for the managed provider failover predicate."""

    return should_reroute_provider_error(
        lane,
        status,
        prepared,
        attempt,
        max_attempts,
    )


def _post_json(
    path: str,
    key: str,
    payload: dict[str, Any],
    timeout: float = 120,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{OPENROUTER_BASE}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://promptrail.ai",
            "X-Title": "PromptRail Codex Demo",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        detail = error.read(3000).decode(errors="replace")
        raise RuntimeError(f"OpenRouter HTTP {error.code}: {detail}") from error
    if not isinstance(result, dict):
        raise RuntimeError("OpenRouter returned a non-object response")
    return result


def _openrouter_http2_client(key: str) -> httpx.Client:
    return httpx.Client(
        base_url=OPENROUTER_BASE,
        http2=True,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://promptrail.ai",
            "X-Title": "PromptRail Codex Demo",
        },
        limits=httpx.Limits(max_connections=13, max_keepalive_connections=13),
    )


def _post_json_httpx(
    client: httpx.Client,
    payload: dict[str, Any],
    timeout: float = 120,
) -> dict[str, Any]:
    response = client.post(
        "/chat/completions",
        json=payload,
        timeout=httpx.Timeout(timeout),
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {response.text[:3000]}")
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("OpenRouter returned a non-object response")
    return result


def _stream_chat_completion(
    client: httpx.Client,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[dict[str, Any], float, float, str]:
    """Stream one structured completion and retain terminal provider usage."""

    request_payload = copy.deepcopy(payload)
    request_payload["stream"] = True
    request_payload["stream_options"] = {"include_usage": True}
    started = time.perf_counter()
    first_content_at: float | None = None
    content_parts: list[str] = []
    usage: dict[str, Any] | None = None
    model = str(payload.get("model") or "")
    provider = ""
    http_version = ""
    with client.stream(
        "POST",
        "/chat/completions",
        json=request_payload,
        timeout=httpx.Timeout(timeout),
    ) as response:
        http_version = response.http_version
        if response.status_code >= 400:
            detail = response.read()[:3000].decode(errors="replace")
            raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {detail}")
        if "application/json" in response.headers.get("content-type", ""):
            result = json.loads(response.read())
            completed_at = time.perf_counter()
            if not isinstance(result, dict) or not isinstance(result.get("usage"), dict):
                raise RuntimeError("paid OpenRouter control response omitted terminal usage")
            latency_ms = (completed_at - started) * 1_000
            return result, latency_ms, latency_ms, http_version
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            event = json.loads(data)
            if event.get("error"):
                raise RuntimeError(f"OpenRouter stream error: {event['error']}")
            model = str(event.get("model") or model)
            provider = str(event.get("provider") or provider)
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            choices = event.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                continue
            choice = choices[0]
            if choice.get("error"):
                raise RuntimeError(f"OpenRouter stream error: {choice['error']}")
            delta = choice.get("delta") or {}
            content = delta.get("content") if isinstance(delta, dict) else None
            if isinstance(content, str) and content:
                if first_content_at is None:
                    first_content_at = time.perf_counter()
                content_parts.append(content)
    completed_at = time.perf_counter()
    if first_content_at is None:
        raise RuntimeError("paid OpenRouter control model streamed no content")
    if usage is None:
        raise RuntimeError("paid OpenRouter control stream ended without usage")
    result = {
        "model": model,
        "provider": provider,
        "usage": usage,
        "choices": [{"message": {"content": "".join(content_parts)}}],
    }
    return (
        result,
        (first_content_at - started) * 1_000,
        (completed_at - started) * 1_000,
        http_version,
    )


def _stream_json_number(
    client: httpx.Client,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[dict[str, Any], float, float, str]:
    return _stream_chat_completion(client, payload, timeout)


def _stream_json_object(
    client: httpx.Client,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[dict[str, Any], float, float, str]:
    return _stream_chat_completion(client, payload, timeout)


def _usage_fields(usage: Any) -> tuple[int, int, int, float]:
    if not isinstance(usage, dict):
        return 0, 0, 0, 0.0
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    prompt = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    completion = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    cached = int(details.get("cached_tokens", details.get("cache_read", 0)) or 0)
    cost = float(usage.get("cost", 0) or 0)
    return prompt, completion, cached, cost


def _priced_usage_cost(
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    route: ProviderRoute,
) -> float:
    cached = min(input_tokens, cached_tokens)
    uncached = max(0, input_tokens - cached)
    cached_rate = (
        route.cache_read_price_per_million
        if route.cache_read_price_per_million is not None
        else route.input_price_per_million
    )
    return (
        uncached * route.input_price_per_million
        + cached * cached_rate
        + output_tokens * route.output_price_per_million
    ) / 1_000_000


class EventLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._totals = {
            "baseline": {"cost": 0.0, "tokens": 0, "cached": 0, "calls": 0},
            "managed": {"cost": 0.0, "tokens": 0, "cached": 0, "calls": 0},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def record(self, lane: str, event: str, **data: Any) -> None:
        payload = {"time": time.time(), "lane": lane, "event": event, **data}
        with self._lock:
            if event == "usage":
                totals = self._totals[lane]
                totals["cost"] += float(data.get("cost", 0))
                totals["tokens"] += int(data.get("input_tokens", 0)) + int(
                    data.get("output_tokens", 0)
                )
                totals["cached"] += int(data.get("cached_tokens", 0))
                totals["calls"] += 1
                payload["totals"] = dict(totals)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(payload, separators=(",", ":")) + "\n")


class OpenRouterAllocator:
    """Real LLM allocation adapter used by GlobalController in the demo."""

    def __init__(
        self,
        key: str,
        model: str,
        ledger: EventLedger,
        candidate_limit: int,
        client: httpx.Client | None = None,
        unavailable_models: Callable[[], frozenset[str]] | None = None,
    ) -> None:
        self.key = key
        self.model_id = model
        self.ledger = ledger
        self.candidate_limit = candidate_limit
        self._client = client or _openrouter_http2_client(key)
        self._owns_client = client is None
        self._unavailable_models = unavailable_models or frozenset

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def allocate(self, request: Any) -> BudgetAllocationDecision:
        unavailable = self._unavailable_models()
        normalized_task = request.task.casefold()
        matched_quality = [
            rule.minimum_quality
            for rule in getattr(request, "task_rules", ())
            if rule.minimum_quality is not None
            and rule.match_terms
            and any(term in normalized_task for term in rule.match_terms)
        ]
        minimum_quality = max([0.0, *matched_quality])
        feasible = tuple(
            item
            for item in request.candidate_options
            if item.model_id not in unavailable
            and item.context_fits
            and item.required_capabilities_supported
            and item.quality >= minimum_quality
        )
        if not feasible:
            raise RuntimeError("no available catalog candidate fits context and capabilities")
        cheapest_fallback = min(
            feasible,
            key=lambda item: (
                item.cheapest_predicted_cost_usd,
                item.fastest_predicted_latency_ms,
                -item.quality,
                item.model_id,
            ),
        )
        eligible = sorted(
            (
                item
                for item in request.candidate_options
                if item.model_id not in unavailable and item.quality >= minimum_quality
            ),
            key=lambda item: (
                not item.context_fits,
                not item.required_capabilities_supported,
                not item.exact_cache_reuse,
                -item.quality,
                item.cheapest_predicted_cost_usd,
                item.fastest_predicted_latency_ms,
                item.model_id,
            ),
        )
        shortlisted = [cheapest_fallback]
        shortlisted.extend(item for item in eligible if item.model_id != cheapest_fallback.model_id)
        selected_options = shortlisted[: self.candidate_limit]
        option_fields = (
            "model_id",
            "quality",
            "context_fits",
            "capabilities_fit",
            "predicted_cost_microdollars",
            "latency_ms",
            "exact_cache_reuse",
            "cached_tokens",
        )
        option_rows = [
            [
                item.model_id,
                item.quality,
                item.context_fits,
                item.required_capabilities_supported,
                math.ceil(item.cheapest_predicted_cost_usd * 1_000_000),
                item.fastest_predicted_latency_ms,
                item.exact_cache_reuse,
                int(getattr(item, "cached_tokens", 0)),
            ]
            for item in selected_options
        ]
        context_blocks = tuple(getattr(request, "context_blocks", ()))
        context_block_fields = (
            "block_id",
            "type",
            "tokens",
            "age",
            "task_overlap",
            "redundancy",
            "cached",
            "cache_invalidation_cost",
            "importance",
            "immutable",
        )
        context_block_rows = [
            [
                block.block_id,
                block.block_type,
                block.tokens,
                block.age,
                block.task_overlap,
                block.redundancy,
                block.cached,
                block.cache_invalidation_cost,
                block.importance,
                block.structurally_immutable,
            ]
            for block in context_blocks
        ]
        self.ledger.record(
            "managed",
            "allocator_shortlist",
            catalog_count=len(request.candidate_options),
            candidate_count=len(option_rows),
            models=[item.model_id for item in selected_options],
            cheapest_fallback_model=cheapest_fallback.model_id,
            minimum_cost_usd=cheapest_fallback.cheapest_predicted_cost_usd,
            minimum_latency_ms=cheapest_fallback.fastest_predicted_latency_ms,
            minimum_quality=minimum_quality,
            rate_limited_models=sorted(unavailable),
        )
        result, ttft_ms, total_latency_ms, http_version = _stream_json_object(
            self._client,
            {
                "model": self.model_id,
                "temperature": 0,
                "provider": {"sort": "latency", "allow_fallbacks": True},
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Allocate one real coding-agent call. Return only one JSON object "
                            "with exactly these fields: cost_microdollars as a positive integer "
                            "where 1 USD = 1,000,000; latency_ms as a positive integer; "
                            "input_cost_fraction strictly between 0 and 1; "
                            "required_context_tokens as the minimum input context needed to "
                            "complete the task correctly; and importance_overrides as an array "
                            "of at most 12 objects with block_id and importance fields. Use an "
                            "empty array when no corrections are needed. Every override block_id "
                            "must appear in overrideable_block_ids; IDs absent from that "
                            "list are immutable and forbidden. Prefer an empty array unless a "
                            "deterministic importance value is clearly wrong. The budget must "
                            "admit at least one supplied candidate. No explanation or markdown."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "policy": request.analytics_insight,
                                "task": request.task,
                                "input_tokens": request.input_tokens,
                                "predicted_output_tokens": request.predicted_output_tokens,
                                "candidate_fields": option_fields,
                                "candidates": option_rows,
                                "cacheable_tokens": request.cacheable_tokens,
                                "compactable_tokens": request.compactable_tokens,
                                "context_block_fields": context_block_fields,
                                "context_blocks": context_block_rows,
                                "overrideable_block_ids": [
                                    block.block_id
                                    for block in context_blocks
                                    if not block.structurally_immutable
                                ],
                                "minimum_admissible": {
                                    "model_id": cheapest_fallback.model_id,
                                    "cost_microdollars": math.ceil(
                                        cheapest_fallback.cheapest_predicted_cost_usd * 1_000_000
                                    ),
                                    "latency_ms": (cheapest_fallback.fastest_predicted_latency_ms),
                                },
                            },
                            separators=(",", ":"),
                        ),
                    },
                ],
            },
            120,
        )
        self._record_control_usage(result, "budget_controller")
        self.ledger.record(
            "managed",
            "control_timing",
            purpose="budget_controller",
            ttft_ms=round(ttft_ms, 3),
            total_latency_ms=round(total_latency_ms, 3),
            http_version=http_version,
        )
        content = result["choices"][0]["message"]["content"]
        values = json.loads(content)
        raw_cost_usd = values["cost_microdollars"] / 1_000_000
        raw_latency_ms = values["latency_ms"]
        raw_input_cost_fraction = values.get("input_cost_fraction")
        valid_input_fraction = (
            isinstance(raw_input_cost_fraction, int | float)
            and not isinstance(raw_input_cost_fraction, bool)
            and math.isfinite(raw_input_cost_fraction)
            and 0.000001 <= raw_input_cost_fraction < 1
        )
        effective_cost_usd = max(
            raw_cost_usd,
            cheapest_fallback.cheapest_predicted_cost_usd,
        )
        effective_latency_ms = max(
            raw_latency_ms,
            cheapest_fallback.fastest_predicted_latency_ms,
        )
        effective_input_cost_fraction = (
            raw_input_cost_fraction
            if valid_input_fraction
            else cheapest_fallback.cheapest_input_cost_fraction
        )
        repaired = (
            effective_cost_usd != raw_cost_usd
            or effective_latency_ms != raw_latency_ms
            or not valid_input_fraction
        )
        self.ledger.record(
            "managed",
            "allocation",
            raw_cost_usd=raw_cost_usd,
            raw_latency_ms=raw_latency_ms,
            raw_input_cost_fraction=raw_input_cost_fraction,
            effective_cost_usd=effective_cost_usd,
            effective_latency_ms=effective_latency_ms,
            effective_input_cost_fraction=effective_input_cost_fraction,
            repaired=repaired,
            fallback_model=cheapest_fallback.model_id,
        )
        return BudgetAllocationDecision(
            cost_usd=effective_cost_usd,
            latency_ms=effective_latency_ms,
            input_cost_fraction=effective_input_cost_fraction,
            required_context_tokens=values["required_context_tokens"],
            importance_overrides=tuple(values["importance_overrides"]),
            reason=(
                "Paid Gemma compact allocation repaired to the cheapest feasible "
                f"catalog route {cheapest_fallback.model_id}."
                if repaired
                else "Paid Gemma compact allocation."
            ),
        )

    def _record_control_usage(self, result: dict[str, Any], purpose: str) -> None:
        prompt, completion, cached, cost = _usage_fields(result.get("usage"))
        self.ledger.record(
            "managed",
            "usage",
            purpose=purpose,
            model=result.get("model", self.model_id),
            input_tokens=prompt,
            output_tokens=completion,
            cached_tokens=cached,
            cost=cost,
        )


class OpenRouterRanker:
    """Real semantic ranker feeding the cache-aware LeRouter decision layer."""

    def __init__(
        self,
        key: str,
        model: str,
        ledger: EventLedger,
        candidate_limit: int,
        batch_size: int,
        read_timeout_seconds: float,
        transport_attempts: int,
        client: httpx.Client | None = None,
    ) -> None:
        self.key = key
        self.model = model
        self.ledger = ledger
        self.candidate_limit = candidate_limit
        if batch_size != 1:
            raise ValueError("demo ranker batch size must be exactly one model per request")
        self.batch_size = batch_size
        if not math.isfinite(read_timeout_seconds) or read_timeout_seconds <= 0:
            raise ValueError("ranker read timeout must be positive and finite")
        if (
            isinstance(transport_attempts, bool)
            or not isinstance(transport_attempts, int)
            or transport_attempts < 1
        ):
            raise ValueError("ranker transport attempts must be a positive integer")
        self.read_timeout_seconds = read_timeout_seconds
        self.transport_attempts = transport_attempts
        self._client = client or _openrouter_http2_client(key)
        self._owns_client = client is None
        self._rate_limited_until: dict[str, float] = {}
        self._rate_limit_lock = threading.Lock()
        self._prefetch_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="promptrail-ranker-prefetch",
        )
        self._prefetch_lock = threading.Lock()
        self._prefetched: dict[int, Future[dict[str, float]]] = {}
        self._score_cache_lock = threading.Lock()
        self._score_cache: dict[str, dict[str, float]] = {}
        self._score_cache_limit = 128

    def mark_unavailable(self, model_id: str, retry_after_seconds: int | None = None) -> None:
        cooldown = max(1, retry_after_seconds or RATE_LIMIT_COOLDOWN_SECONDS)
        with self._rate_limit_lock:
            self._rate_limited_until[model_id] = time.monotonic() + cooldown

    def mark_rate_limited(self, model_id: str, retry_after_seconds: int | None = None) -> None:
        """Backward-compatible name for temporarily excluding a failed model."""

        self.mark_unavailable(model_id, retry_after_seconds)

    def rate_limited_models(self) -> frozenset[str]:
        now = time.monotonic()
        with self._rate_limit_lock:
            self._rate_limited_until = {
                model_id: deadline
                for model_id, deadline in self._rate_limited_until.items()
                if deadline > now
            }
            return frozenset(self._rate_limited_until)

    def close(self) -> None:
        self._prefetch_executor.shutdown(wait=True, cancel_futures=True)
        with self._score_cache_lock:
            self._score_cache.clear()
        if self._owns_client:
            self._client.close()

    def prefetch(
        self,
        *,
        intent: CallIntent,
        candidates: tuple[ModelCandidate, ...],
    ) -> None:
        future = self._prefetch_executor.submit(
            self._rank_now,
            intent,
            candidates,
            round(self.read_timeout_seconds * 1_000),
        )
        with self._prefetch_lock:
            self._prefetched[id(intent)] = future

    def discard_prefetch(self, intent: CallIntent) -> None:
        with self._prefetch_lock:
            future = self._prefetched.pop(id(intent), None)
        if future is not None:
            future.cancel()

    def rank(
        self,
        *,
        intent: CallIntent,
        candidates: tuple[ModelCandidate, ...],
        timeout_ms: int,
    ) -> dict[str, float]:
        with self._prefetch_lock:
            future = self._prefetched.pop(id(intent), None)
        if future is not None:
            return future.result()
        return self._rank_now(intent, candidates, timeout_ms)

    def _rank_now(
        self,
        intent: CallIntent,
        candidates: tuple[ModelCandidate, ...],
        timeout_ms: int,
    ) -> dict[str, float]:
        rate_limited = self.rate_limited_models()
        available = tuple(item for item in candidates if item.model_id not in rate_limited)
        if not available:
            raise RuntimeError("all paid demo models are temporarily rate-limited")
        cache_key = self._score_cache_key(intent, candidates, rate_limited)
        with self._score_cache_lock:
            cached_scores = self._score_cache.get(cache_key)
        if cached_scores is not None:
            self.ledger.record(
                "managed",
                "ranker_cache_hit",
                cache_key=cache_key[:16],
                candidate_count=len(candidates),
                task=intent.task,
            )
            return dict(cached_scores)
        shortlisted = shortlist_candidates(intent.task, available, self.candidate_limit)
        batches = tuple(
            shortlisted[index : index + self.batch_size]
            for index in range(0, len(shortlisted), self.batch_size)
        )
        self.ledger.record(
            "managed",
            "ranker_shortlist",
            catalog_count=len(candidates),
            candidate_count=len(shortlisted),
            models=[item.model_id for item in shortlisted],
            rate_limited_models=sorted(rate_limited),
            allocated_timeout_ms=timeout_ms,
            batch_size=self.batch_size,
            batch_count=len(batches),
        )
        read_timeout = max(self.read_timeout_seconds, timeout_ms / 1_000)
        with ThreadPoolExecutor(
            max_workers=len(batches),
            thread_name_prefix="promptrail-ranker",
        ) as executor:
            futures = [
                executor.submit(
                    self._rank_batch,
                    intent,
                    batch,
                    read_timeout,
                    batch_index,
                    len(batches),
                )
                for batch_index, batch in enumerate(batches, start=1)
            ]
            ranked_batches = [future.result() for future in futures]

        normalized_scores = {
            model_id: score
            for batch_scores in ranked_batches
            for model_id, score in self._normalize_batch_scores(batch_scores).items()
        }
        result_scores = {
            item.model_id: (-1_000_000.0 if item.model_id in rate_limited else 0.0)
            for item in candidates
        }
        result_scores.update(normalized_scores)
        with self._score_cache_lock:
            self._score_cache[cache_key] = dict(result_scores)
            while len(self._score_cache) > self._score_cache_limit:
                self._score_cache.pop(next(iter(self._score_cache)))
        return result_scores

    def _score_cache_key(
        self,
        intent: CallIntent,
        candidates: tuple[ModelCandidate, ...],
        unavailable: frozenset[str],
    ) -> str:
        normalized_task = " ".join(intent.task.casefold().split())
        catalog = [candidate.model_dump(mode="json") for candidate in candidates]
        encoded = json.dumps(
            {
                "contract": "demo-gemma-single-score-v1",
                "controller_model": self.model,
                "session_id": intent.session_id,
                "task": normalized_task,
                "required_capabilities": sorted(intent.required_capabilities),
                "catalog": catalog,
                "unavailable_models": sorted(unavailable),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _rank_batch(
        self,
        intent: CallIntent,
        batch: tuple[ModelCandidate, ...],
        read_timeout: float,
        batch_index: int,
        batch_count: int,
    ) -> dict[str, float]:
        if len(batch) != 1:
            raise RuntimeError("demo ranker requests must contain exactly one model")
        candidate = batch[0]
        score_schema = {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        }
        payload = {
            "model": self.model,
            "temperature": 0,
            "provider": {"sort": "latency", "allow_fallbacks": True},
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "promptrail_model_score",
                    "strict": True,
                    "schema": score_schema,
                },
            },
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Score the candidate model from 0 to 1 for the coding task. "
                        "Return only one JSON number, such as 0.82. Do not return an "
                        "object, model ID, label, explanation, or markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": intent.task,
                            "candidate": {
                                "id": candidate.model_id,
                                "quality": candidate.quality,
                                "strengths": sorted(candidate.strengths),
                            },
                        },
                        separators=(",", ":"),
                    ),
                },
            ],
        }
        for attempt in range(1, self.transport_attempts + 1):
            try:
                result, ttft_ms, total_latency_ms, http_version = _stream_json_number(
                    self._client,
                    payload,
                    read_timeout,
                )
                break
            except (TimeoutError, urllib.error.URLError, httpx.TransportError) as error:
                if attempt >= self.transport_attempts:
                    raise RuntimeError(
                        "paid OpenRouter model ranker timed out after "
                        f"{self.transport_attempts} local attempts"
                    ) from error
                self.ledger.record(
                    "managed",
                    "control_retry",
                    purpose="model_ranker",
                    attempt=attempt,
                    next_attempt=attempt + 1,
                    error_type=type(error).__name__,
                    batch_index=batch_index,
                    batch_count=batch_count,
                )
        prompt, completion, cached, cost = _usage_fields(result.get("usage"))
        self.ledger.record(
            "managed",
            "usage",
            purpose="model_ranker",
            model=result.get("model", self.model),
            input_tokens=prompt,
            output_tokens=completion,
            cached_tokens=cached,
            cost=cost,
            batch_index=batch_index,
            batch_count=batch_count,
            ttft_ms=round(ttft_ms, 3),
            total_latency_ms=round(total_latency_ms, 3),
            http_version=http_version,
        )
        content = result["choices"][0]["message"]["content"]
        score = float(json.loads(content))
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise RuntimeError("paid OpenRouter model ranker returned an invalid score")
        return {candidate.model_id: score}

    @staticmethod
    def _normalize_batch_scores(scores: dict[str, float]) -> dict[str, float]:
        """Remove each independent batch's scale and offset before merging scores."""

        if len(scores) <= 1:
            return dict(scores)
        values = tuple(scores.values())
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        if variance <= 1e-12:
            return {model_id: 0.5 for model_id in scores}
        deviation = math.sqrt(variance)
        return {
            model_id: 0.5 * (1 + math.erf(((value - mean) / deviation) / math.sqrt(2)))
            for model_id, value in scores.items()
        }


def load_model_profiles(
    uri: str,
    database_name: str,
    collection_name: str,
) -> tuple[dict[str, Any], ...]:
    """Load the demo's read-only tool-capable model universe from MongoDB."""

    import certifi
    from pymongo import MongoClient
    from pymongo.server_api import ServerApi

    client = MongoClient(
        uri,
        server_api=ServerApi("1"),
        serverSelectionTimeoutMS=MONGO_TIMEOUT_MS,
        connectTimeoutMS=MONGO_TIMEOUT_MS,
        socketTimeoutMS=MONGO_TIMEOUT_MS,
        tlsCAFile=certifi.where(),
    )
    try:
        client.admin.command("ping")
        documents = client[database_name][collection_name].find(
            {"supports_tools": True},
            {
                "_id": 0,
                "model": 1,
                "model_cost": 1,
                "model_latency": 1,
                "model_context_window": 1,
                "forces": 1,
                "quality_calibration": 1,
                "benchmark_results": 1,
                "model_size": 1,
            },
            sort=[("model", 1)],
        )
        profiles = tuple(dict(document) for document in documents)
    finally:
        client.close()
    if not profiles:
        raise RuntimeError(
            f"MongoDB {database_name}.{collection_name} has no tool-capable model profiles"
        )
    return profiles


def _coding_quality(profile: dict[str, Any]) -> float:
    calibration = profile.get("quality_calibration") or {}
    coding = (calibration.get("routes") or {}).get("coding_debugging") or {}
    measured = coding.get("mean_quality_score") if coding.get("measured") else None
    if isinstance(measured, int | float) and math.isfinite(float(measured)):
        return min(1.0, max(0.0, float(measured)))
    return 0.0


def _numeric(value: Any, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def build_candidates(profiles: tuple[dict[str, Any], ...]) -> tuple[ModelCandidate, ...]:
    candidates = []
    seen: set[str] = set()
    for profile in profiles:
        model_id = str(profile.get("model") or "").strip()
        pricing = profile.get("model_cost") or {}
        input_price = max(0.0, _numeric(pricing.get("input_usd_per_million"), 0.0))
        output_price = max(0.0, _numeric(pricing.get("output_usd_per_million"), 0.0))
        if (
            not model_id
            or model_id.endswith(":free")
            or (input_price == 0 and output_price == 0)
            or model_id in seen
        ):
            continue
        seen.add(model_id)
        cache_read_price = max(
            0.0,
            _numeric(pricing.get("input_cache_read_usd_per_million"), input_price),
        )
        cache_write_price = max(
            0.0,
            _numeric(pricing.get("input_cache_write_usd_per_million"), input_price),
        )
        context = max(1, int(_numeric(profile.get("model_context_window"), 128_000)))
        latency = max(1, int(_numeric(profile.get("model_latency"), 5_000)))
        strengths = frozenset(
            str(value).strip().casefold()
            for value in profile.get("forces") or ()
            if str(value).strip()
        )
        route = ProviderRoute(
            route_id=f"openrouter:{model_id}",
            provider="openrouter",
            native_model_id=model_id,
            input_price_per_million=input_price,
            output_price_per_million=output_price,
            cache_read_price_per_million=cache_read_price,
            cache_write_price_per_million=cache_write_price,
            p95_ttft_ms=max(1, latency // 4),
            p95_total_latency_ms=latency,
            guaranteed=True,
            cache_supported=True,
            cache_automatic=True,
            capabilities=frozenset({"tools"}),
        )
        candidates.append(
            ModelCandidate(
                model_id=model_id,
                quality=_coding_quality(profile),
                context_window_tokens=context,
                strengths=strengths,
                capabilities=frozenset({"tools"}),
                router_payload={
                    "forces": sorted(strengths),
                    "model_size": profile.get("model_size"),
                    "benchmark_results": profile.get("benchmark_results") or {},
                },
                routes=(route,),
            )
        )
    return tuple(candidates)


def openrouter_model_for_decision(decision: Any) -> str:
    """Resolve a catalog candidate to the native OpenRouter model actually billed."""

    return str(decision.route.native_model_id)


def shortlist_candidates(
    task: str,
    candidates: tuple[ModelCandidate, ...],
    limit: int,
) -> tuple[ModelCandidate, ...]:
    """Bound LLM control-plane context using only MongoDB profile evidence."""

    task_terms = set(re.findall(r"[a-z0-9]+", task.casefold()))

    def evidence(candidate: ModelCandidate) -> tuple[Any, ...]:
        strength_terms = {
            term for strength in candidate.strengths for term in re.findall(r"[a-z0-9]+", strength)
        }
        route = candidate.routes[0]
        overlap = len(task_terms & strength_terms)
        coding_fit = int("coding" in strength_terms or "code" in strength_terms)
        predicted_price = route.input_price_per_million + route.output_price_per_million
        return (
            -overlap,
            -coding_fit,
            -candidate.quality,
            predicted_price,
            route.p95_total_latency_ms,
            candidate.model_id,
        )

    return tuple(sorted(candidates, key=evidence)[: max(1, limit)])


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, separators=(",", ":"), default=str)
    parts = []
    for part in content:
        if isinstance(part, dict) and part.get("type") in {"input_text", "output_text", "text"}:
            parts.append(str(part.get("text") or ""))
    return "\n".join(parts)


def responses_to_messages(body: dict[str, Any]) -> tuple[tuple[dict[str, Any], ...], list[int]]:
    messages: list[dict[str, Any]] = []
    source_indices: list[int] = []
    instructions = str(body.get("instructions") or "").strip()
    if instructions:
        messages.append({"role": "developer", "content": instructions})
        source_indices.append(-1)
    raw_input = body.get("input")
    if isinstance(raw_input, str):
        return (*messages, {"role": "user", "content": raw_input}), [*source_indices, -2]
    for index, item in enumerate(raw_input or []):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message" or "role" in item:
            messages.append(
                {
                    "role": str(item.get("role") or "user"),
                    "content": _text_content(item.get("content")),
                }
            )
            source_indices.append(index)
        elif item_type == "function_call":
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": item.get("call_id", item.get("id", "call")),
                            "type": "function",
                            "function": {
                                "name": item.get("name", "tool"),
                                "arguments": item.get("arguments", "{}"),
                            },
                        }
                    ],
                }
            )
            source_indices.append(index)
        elif item_type == "function_call_output":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id", "call"),
                    "content": str(item.get("output") or ""),
                }
            )
            source_indices.append(index)
    if not messages:
        raise ValueError("Codex request contains no usable messages")
    return tuple(messages), source_indices


def task_from_messages(messages: tuple[dict[str, Any], ...]) -> str:
    """Keep the user's goal stable while Codex appends assistant/tool messages."""

    for message in reversed(messages):
        if message.get("role") == "user":
            task = _text_content(message.get("content")).strip()
            if task:
                return task[:2000]
    return "coding agent continuation"


def openrouter_compatible_body(body: dict[str, Any]) -> dict[str, Any]:
    """Remove Codex-hosted tool declarations OpenRouter cannot execute.

    The portable model catalog exposes local shell execution as ordinary Responses function tools.
    Namespace tools and OpenAI-hosted web search require Codex-backend capabilities, so neither
    comparison lane advertises them.
    """

    result = copy.deepcopy(body)
    tools = result.get("tools")
    if isinstance(tools, list):
        result["tools"] = [
            item
            for item in tools
            if (
                isinstance(item, dict)
                and item.get("type") == "function"
                and item.get("name") != "view_image"
            )
        ]
    existing = str(result.get("instructions") or "").strip()
    result["instructions"] = "\n\n".join(
        item for item in (existing, PORTABLE_TOOL_INSTRUCTION) if item
    )
    return result


def managed_provider_context(
    body: dict[str, Any],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], list[int]]:
    """Return the exact OpenRouter-shaped request used by the control plane."""

    provider_body = openrouter_compatible_body(body)
    messages, source_indices = responses_to_messages(provider_body)
    return provider_body, messages, source_indices


def normalize_reasoning(body: dict[str, Any], model: str) -> None:
    if model.startswith("openai/"):
        existing = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
        effort = str(existing.get("effort") or "").strip()
        if model == "openai/gpt-5.6-sol":
            effort = "medium"
        elif effort in {"", "none"}:
            effort = "low"
        body["reasoning"] = {**existing, "effort": effort, "context": "auto"}
        if model in {"openai/gpt-5.2-codex", "openai/gpt-5.6-sol"}:
            existing_text = body.get("text") if isinstance(body.get("text"), dict) else {}
            body["text"] = {**existing_text, "verbosity": "medium"}
    else:
        body.pop("reasoning", None)
        # Provider defaults vary widely. Keep the same prompt/model decision
        # reproducible across demo runs and avoid stochastic tool-loop drift.
        body["temperature"] = 0


def apply_compaction(
    body: dict[str, Any],
    original: tuple[dict[str, Any], ...],
    compacted: tuple[dict[str, Any], ...],
    source_indices: list[int],
) -> dict[str, Any]:
    result = copy.deepcopy(body)
    raw_input = result.get("input")
    if not isinstance(raw_input, list):
        return result
    for before, after, source_index in zip(original, compacted, source_indices, strict=True):
        if before.get("content") == after.get("content") or source_index < 0:
            continue
        item = raw_input[source_index]
        if item.get("type") == "function_call_output":
            item["output"] = after["content"]
            continue
        content = item.get("content")
        if isinstance(content, str):
            item["content"] = after["content"]
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in {
                    "input_text",
                    "output_text",
                    "text",
                }:
                    part["text"] = after["content"]
                    break
    return result


@dataclass
class ManagedState:
    gateway: PromptRailGateway
    run_id: str
    session_id: str
    candidates: tuple[ModelCandidate, ...]


class DemoService:
    def __init__(
        self,
        root: Path,
        openai_key: str,
        openrouter_key: str,
        mongodb_uri: str,
        ledger: EventLedger,
    ) -> None:
        self.root = root
        self.openai_key = openai_key
        self.openrouter_key = openrouter_key
        self.ledger = ledger
        if not os.getenv("LEROUTER_INTERNAL_SERVICE_TOKEN", "").strip():
            raise RuntimeError(
                "LEROUTER_INTERNAL_SERVICE_TOKEN is required for real ModernBERT "
                "output-length prediction"
            )
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        config["baseline_model"] = os.getenv(
            "PROMPTRAIL_DEMO_BASELINE_MODEL", config["baseline_model"]
        )
        controller_model = require_paid_controller_model(
            os.getenv("PROMPTRAIL_DEMO_CONTROLLER_MODEL", config["controller_model"])
        )
        self.baseline_model = config["baseline_model"]
        configured_baselines = tuple(str(item["model"]) for item in config["comparison_models"])
        self.baseline_models = tuple(dict.fromkeys((self.baseline_model, *configured_baselines)))
        profiles = load_model_profiles(
            mongodb_uri,
            str(config["mongodb_database"]),
            str(config["mongodb_collection"]),
        )
        candidates = build_candidates(profiles)
        candidate_ids = {item.model_id for item in candidates}
        missing_baselines = [
            model_id for model_id in self.baseline_models if model_id not in candidate_ids
        ]
        if missing_baselines:
            raise RuntimeError(
                "Comparison model is missing from the MongoDB tool catalog: "
                + ", ".join(missing_baselines)
            )
        self.baseline_routes = {
            item.model_id: item.routes[0]
            for item in candidates
            if item.model_id in self.baseline_models
        }
        self.ledger.record(
            "managed",
            "catalog_loaded",
            source=f"{config['mongodb_database']}.{config['mongodb_collection']}",
            candidate_count=len(candidates),
            controller_model=controller_model,
        )
        self.model_ids = tuple(
            dict.fromkeys([*self.baseline_models, *(item.model_id for item in candidates)])
        )
        policy = OperatingPolicy.model_validate_json((root / "policy.json").read_text())
        self.control_client = _openrouter_http2_client(openrouter_key)
        ranker = OpenRouterRanker(
            openrouter_key,
            controller_model,
            ledger,
            int(config["ranker_candidate_limit"]),
            int(config["ranker_batch_size"]),
            float(config["ranker_read_timeout_seconds"]),
            int(config["ranker_transport_attempts"]),
            self.control_client,
        )
        allocator = OpenRouterAllocator(
            openrouter_key,
            controller_model,
            ledger,
            int(config["allocator_candidate_limit"]),
            self.control_client,
            ranker.rate_limited_models,
        )
        controller = GlobalController(
            allocator=allocator,
            required_allocator_model_id=controller_model,
        )
        self.ranker = ranker
        gateway = PromptRailGateway(
            policy_agent=SuppliedPolicyAgent(policy),
            controller=controller,
            model_router=CacheAwareLeRouter(ranker),
        )
        session_id = f"demo-{int(time.time())}"
        run = gateway.start_run(
            session_id=session_id,
            enterprise_json_paths=(root / "demo_input.json",),
            candidates=candidates,
        )
        self.managed = ManagedState(
            gateway=gateway,
            run_id=run.run_id,
            session_id=session_id,
            candidates=candidates,
        )

    def close(self) -> None:
        self.managed.gateway.close()
        self.ranker.close()
        self.control_client.close()

    def prepare(self, lane: str, body: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        self.ledger.record(
            lane,
            "protocol",
            tool_types=[
                str(item.get("type") or "")
                for item in body.get("tools") or []
                if isinstance(item, dict)
            ],
            request_keys=sorted(body),
        )
        if lane == "baseline":
            requested_model = str(body.get("model") or self.baseline_model)
            if requested_model not in self.baseline_models:
                raise ValueError(f"baseline comparison model is not allowed: {requested_model}")
            result = openrouter_compatible_body(body)
            normalize_reasoning(result, requested_model)
            result["model"] = requested_model
            result.pop("provider", None)
            return result, None
        provider_body, messages, indices = managed_provider_context(body)
        provider_tools = tuple(provider_body.get("tools") or ())
        intent = CallIntent(
            session_id=self.managed.session_id,
            task=task_from_messages(messages),
            messages=messages,
            tools=provider_tools,
            required_capabilities=frozenset({"tools"}) if provider_tools else frozenset(),
        )
        run_status = self.managed.gateway.controller.snapshot(self.managed.run_id).status
        if run_status is not RunStatus.ACTIVE:
            raise BudgetError(f"agent run is {run_status.value}")
        self.ranker.prefetch(intent=intent, candidates=self.managed.candidates)
        try:
            prepared = self.managed.gateway.prepare_call_sync(
                run_id=self.managed.run_id,
                intent=intent,
            )
        except BaseException:
            self.ranker.discard_prefetch(intent)
            raise
        result = apply_compaction(
            provider_body,
            messages,
            prepared.compaction.messages,
            indices,
        )
        result["model"] = openrouter_model_for_decision(prepared.model)
        normalize_reasoning(result, result["model"])
        result["provider"] = {"sort": "price", "allow_fallbacks": True}
        self.ledger.record(
            "managed",
            "decision",
            call_id=prepared.budget.call_id,
            model=result["model"],
            catalog_model=prepared.model.candidate.model_id,
            compacted_tokens=prepared.compaction.tokens_before - prepared.compaction.tokens_after,
            cached_tokens=prepared.model.cache_value.cached_tokens,
            reason="; ".join(prepared.model.reasons),
            predicted_output_tokens=prepared.predicted_output_tokens,
            required_context_tokens=prepared.budget.required_context_tokens,
            output_prediction_ms=prepared.output_prediction_ms,
            context_analysis_ms=prepared.context_analysis_ms,
            gemma_allocation_ms=prepared.gemma_allocation_ms,
            semantic_ranking_ms=prepared.semantic_ranking_ms,
            candidate_feasibility_ms=prepared.candidate_feasibility_ms,
            compaction_ms=prepared.compaction_ms,
            provider_planning_ms=prepared.provider_planning_ms,
            control_plane_total_ms=prepared.control_plane_total_ms,
            predictor_on_critical_path=(
                prepared.output_prediction_ms
                >= max(prepared.context_analysis_ms, prepared.semantic_ranking_ms)
            ),
        )
        self.ledger.record(
            "managed",
            "control_plane",
            call_id=prepared.budget.call_id,
            output_prediction_ms=prepared.output_prediction_ms,
            context_analysis_ms=prepared.context_analysis_ms,
            gemma_allocation_ms=prepared.gemma_allocation_ms,
            semantic_ranking_ms=prepared.semantic_ranking_ms,
            candidate_feasibility_ms=prepared.candidate_feasibility_ms,
            compaction_ms=prepared.compaction_ms,
            provider_planning_ms=prepared.provider_planning_ms,
            total_ms=prepared.control_plane_total_ms,
        )
        return result, prepared

    def upstream(
        self, lane: str, payload: dict[str, Any]
    ) -> tuple[str, str, str, dict[str, Any], str]:
        result = copy.deepcopy(payload)
        result.pop("max_output_tokens", None)
        result.pop("max_tokens", None)
        if lane == "baseline":
            requested_model = str(result.get("model") or self.baseline_model)
            if requested_model.startswith("openai/"):
                result["model"] = requested_model.removeprefix("openai/")
                result.pop("provider", None)
                return (
                    OPENAI_HOST,
                    "/v1/responses",
                    self.openai_key,
                    result,
                    "openai",
                )
            result["provider"] = {"sort": "price", "allow_fallbacks": True}
            return (
                OPENROUTER_HOST,
                "/api/v1/responses",
                self.openrouter_key,
                result,
                "openrouter",
            )
        return (
            OPENROUTER_HOST,
            "/api/v1/responses",
            self.openrouter_key,
            result,
            "openrouter",
        )

    def settle(
        self,
        lane: str,
        prepared: Any,
        response: dict[str, Any],
        elapsed_ms: int,
        provider: str,
        requested_model: str = "",
        provider_ttft_ms: int | None = None,
        end_to_end_ttft_ms: int | None = None,
    ) -> None:
        usage = response.get("usage") or {}
        input_tokens, output_tokens, cached_tokens, cost = _usage_fields(usage)
        if lane == "baseline" and provider == "openai" and "cost" not in usage:
            route = self.baseline_routes.get(requested_model)
            if route is None:
                raise RuntimeError(f"missing baseline pricing route for {requested_model!r}")
            cost = _priced_usage_cost(
                input_tokens,
                output_tokens,
                cached_tokens,
                route,
            )
        fallback_model = prepared.model.candidate.model_id if prepared else ""
        model = requested_model or str(response.get("model") or fallback_model)
        self.ledger.record(
            lane,
            "usage",
            call_id=prepared.budget.call_id if prepared is not None else None,
            purpose="agent",
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cost=cost,
            output_types=sorted(
                {
                    str(item.get("type") or "")
                    for item in response.get("output") or []
                    if isinstance(item, dict)
                }
            ),
            provider_ttft_ms=provider_ttft_ms,
            end_to_end_ttft_ms=end_to_end_ttft_ms,
        )
        if prepared is not None:
            try:
                self.managed.gateway.observe_model(
                    prepared=prepared,
                    usage=ModelUsage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=elapsed_ms,
                        provider=provider,
                        model_id=prepared.model.candidate.model_id,
                        cache_read_tokens=cached_tokens,
                        cost_usd=cost,
                    ),
                )
            except Exception as error:
                # The provider call and cost are already authoritative at this point.
                # Keep the delivered response intact and surface settlement separately.
                self.ledger.record(
                    "managed",
                    "settlement_error",
                    message=f"{type(error).__name__}: {error}",
                )

    def fail_upstream(
        self,
        prepared: Any,
        status: int,
        retry_after_seconds: int | None = None,
    ) -> None:
        if prepared is None:
            return
        model_id = prepared.model.candidate.model_id
        if status >= 400 and status not in NON_ROUTABLE_UPSTREAM_STATUSES:
            self.ranker.mark_unavailable(model_id, retry_after_seconds)
            self.ledger.record(
                "managed",
                "provider_error_cooldown",
                model=prepared.model.route.native_model_id,
                status=status,
                retry_after_seconds=retry_after_seconds or RATE_LIMIT_COOLDOWN_SECONDS,
            )
        elif status >= 400:
            self.ledger.record(
                "managed",
                "provider_error_terminal",
                model=prepared.model.route.native_model_id,
                status=status,
                reason="account-wide error cannot be repaired by changing models",
            )
        try:
            self.managed.gateway.fail_model(
                prepared=prepared,
                billing_unknown=status >= 500,
            )
        except Exception as error:
            self.ledger.record(
                "managed",
                "settlement_error",
                message=f"{type(error).__name__}: {error}",
            )

    def reroute_upstream(
        self,
        payload: dict[str, Any],
        prepared: Any,
        status: int,
        retry_after_seconds: int | None,
        excluded_model_ids: frozenset[str],
        additional_elapsed_ms: int,
    ) -> tuple[dict[str, Any], Any]:
        """Reuse the active budget/compaction and switch to a ranked alternative."""

        model_id = prepared.model.candidate.model_id
        self.ranker.mark_unavailable(model_id, retry_after_seconds)
        self.ledger.record(
            "managed",
            "provider_error_cooldown",
            model=prepared.model.route.native_model_id,
            status=status,
            retry_after_seconds=retry_after_seconds or RATE_LIMIT_COOLDOWN_SECONDS,
        )
        rerouted = self.managed.gateway.reroute_prepared(
            prepared=prepared,
            excluded_model_ids=(excluded_model_ids | self.ranker.rate_limited_models()),
            additional_elapsed_ms=additional_elapsed_ms,
        )
        result = copy.deepcopy(payload)
        result["model"] = openrouter_model_for_decision(rerouted.model)
        normalize_reasoning(result, result["model"])
        result["provider"] = {"sort": "price", "allow_fallbacks": True}
        return result, rerouted


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PromptRailDemo/0.1"

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/health":
            self._json(200, {"status": "ok", "lane": self.server.lane})
        elif path == "/models":
            self._json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": model_id,
                            "object": "model",
                            "created": 0,
                            "owned_by": "openrouter",
                        }
                        for model_id in self.server.service.model_ids
                    ],
                },
            )
        else:
            self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        self._response_started = False
        self._request_started = time.perf_counter()
        if self.headers.get("authorization") != f"Bearer {PROXY_TOKEN}":
            self._json(401, {"error": "invalid_demo_proxy_token"})
            return
        if self.path.rstrip("/") != "/responses":
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("invalid request body size")
            body = json.loads(self.rfile.read(length))
            forwarded, prepared = self.server.service.prepare(self.server.lane, body)
            self._forward(body, forwarded, prepared)
        except Exception as error:
            self.server.service.ledger.record(
                self.server.lane, "error", message=f"{type(error).__name__}: {error}"
            )
            if not self._response_started:
                self._json(502, {"error": "demo_proxy_error", "message": str(error)})
            else:
                self.close_connection = True

    def _forward(
        self,
        original: dict[str, Any],
        payload: dict[str, Any],
        prepared: Any,
    ) -> None:
        del original
        started = time.perf_counter()
        attempt = 1
        attempted_models: set[str] = set()
        while True:
            host, path, key, forwarded, provider = self.server.service.upstream(
                self.server.lane, payload
            )
            connection = http.client.HTTPSConnection(host, timeout=600)
            data = json.dumps(forwarded, separators=(",", ":")).encode()
            connection.request(
                "POST",
                path,
                body=data,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(data)),
                    "Accept": self.headers.get("accept", "text/event-stream"),
                    "HTTP-Referer": "https://promptrail.ai",
                    "X-Title": "PromptRail Codex Demo",
                },
            )
            provider_started = time.perf_counter()
            upstream = connection.getresponse()
            raw_retry_after = upstream.getheader("retry-after")
            retry_after = None
            if raw_retry_after and raw_retry_after.isdigit():
                retry_after = int(raw_retry_after)
            available_alternatives = 0
            if prepared is not None:
                unavailable_models = self.server.service.ranker.rate_limited_models()
                available_alternatives = sum(
                    item.candidate.model_id not in unavailable_models
                    for item in prepared.model_alternatives
                )
            if should_reroute_provider_error(
                self.server.lane,
                upstream.status,
                prepared,
                attempt,
                attempt + available_alternatives,
            ):
                previous_model = prepared.model.route.native_model_id
                attempted_models.add(prepared.model.candidate.model_id)
                upstream.read()
                connection.close()
                payload, prepared = self.server.service.reroute_upstream(
                    payload,
                    prepared,
                    upstream.status,
                    retry_after,
                    frozenset(attempted_models),
                    math.ceil((time.perf_counter() - started) * 1_000),
                )
                self.server.service.ledger.record(
                    "managed",
                    "provider_error_reroute",
                    previous_model=previous_model,
                    model=prepared.model.route.native_model_id,
                    status=upstream.status,
                    attempt=attempt + 1,
                )
                attempt += 1
                continue
            if upstream.status >= 400:
                self.server.service.fail_upstream(prepared, upstream.status, retry_after)
            break
        self._response_started = True
        self.send_response(upstream.status)
        content_type = upstream.getheader("content-type", "application/json")
        self.send_header("content-type", content_type)
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        captured = bytearray()
        first_chunk_at: float | None = None
        while True:
            chunk = upstream.read(4096)
            if not chunk:
                break
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter()
            captured.extend(chunk)
            self.wfile.write(chunk)
            self.wfile.flush()
        connection.close()
        completed = extract_completed_response(bytes(captured), content_type)
        if upstream.status < 400 and completed is not None:
            self.server.service.settle(
                self.server.lane,
                prepared,
                completed,
                math.ceil((time.perf_counter() - started) * 1000),
                provider,
                str(payload.get("model") or ""),
                (
                    math.ceil((first_chunk_at - provider_started) * 1000)
                    if first_chunk_at is not None
                    else None
                ),
                (
                    math.ceil((first_chunk_at - self._request_started) * 1000)
                    if first_chunk_at is not None
                    else None
                ),
            )
        self.close_connection = True

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        self._response_started = True
        data = (json.dumps(payload) + "\n").encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


def extract_completed_response(raw: bytes, content_type: str) -> dict[str, Any] | None:
    text = raw.decode(errors="replace")
    if "text/event-stream" not in content_type:
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None
    completed = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "response.completed":
            completed = event.get("response") or event
    return completed


class LaneServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], lane: str, service: DemoService) -> None:
        self.lane = lane
        self.service = service
        super().__init__(address, ProxyHandler)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() and name.strip() not in os.environ:
            os.environ[name.strip()] = value.strip().strip('"').strip("'")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--baseline-port", type=int, default=8765)
    parser.add_argument("--managed-port", type=int, default=8766)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.env_file:
        load_env_file(args.env_file)
    load_env_file(root / ".env")
    load_env_file(root.parent / ".env")
    ensure_internal_service_token()
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not openrouter_key:
        raise SystemExit("OPENROUTER_API_KEY is missing; use demo/.env or --env-file PATH")
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_key:
        raise SystemExit("OPENAI_API_KEY is missing; use demo/.env or --env-file PATH")
    mongodb_uri = os.getenv("MONGODB_URI", "").strip()
    if not mongodb_uri:
        raise SystemExit("MONGODB_URI is missing; use demo/.env or --env-file PATH")
    ledger = EventLedger(args.events)
    service = DemoService(root, openai_key, openrouter_key, mongodb_uri, ledger)
    servers = [
        LaneServer(("127.0.0.1", args.baseline_port), "baseline", service),
        LaneServer(("127.0.0.1", args.managed_port), "managed", service),
    ]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    print(
        json.dumps(
            {
                "status": "ready",
                "baseline": args.baseline_port,
                "managed": args.managed_port,
            }
        ),
        flush=True,
    )
    try:
        while all(thread.is_alive() for thread in threads):
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
