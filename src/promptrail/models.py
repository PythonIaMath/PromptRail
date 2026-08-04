"""Typed contracts shared by the PromptRail control plane."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())


def _finite(value: float, field: str, *, minimum: float = 0.0) -> float:
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise ValueError(f"{field} must be finite and >= {minimum}")
    return number


class TaskRule(FrozenModel):
    """Optional deterministic weighting for a class of agent work."""

    name: str
    match_terms: tuple[str, ...] = ()
    cost_weight: float = 1.0
    latency_weight: float = 1.0
    minimum_quality: float | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task rule name cannot be empty")
        return value.strip()

    @field_validator("match_terms")
    @classmethod
    def normalize_terms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(term.strip().casefold() for term in value if term.strip()))

    @field_validator("cost_weight", "latency_weight")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        return _finite(value, "task weight", minimum=0.05)

    @field_validator("minimum_quality")
    @classmethod
    def validate_quality(cls, value: float | None) -> float | None:
        if value is None:
            return None
        number = _finite(value, "minimum_quality")
        if number > 1:
            raise ValueError("minimum_quality must be <= 1")
        return number


class OperatingPolicy(FrozenModel):
    """Validated instruction produced from enterprise JSON data."""

    schema_version: Literal[1] = 1
    instruction: str
    workflow_cost_budget_usd: float
    workflow_latency_budget_ms: int
    expected_llm_calls: int
    input_cost_fraction: float = 0.55
    cost_priority: float = 1.0
    latency_priority: float = 1.0
    quality_priority: float = 1.0
    cache_priority: float = 1.0
    minimum_quality: float = 0.0
    provider_exploration_fraction: float = 0.5
    maximum_provider_exploration_ms: int = 2_000
    controller_reserve_ms: int = 25
    compaction_reserve_ms: int = 75
    latency_safety_margin_ms: int = 50
    task_rules: tuple[TaskRule, ...] = ()
    source_digest: str | None = None

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("policy instruction cannot be empty")
        if len(normalized) > 8_000:
            raise ValueError("policy instruction exceeds 8,000 characters")
        return normalized

    @field_validator("workflow_cost_budget_usd")
    @classmethod
    def validate_cost(cls, value: float) -> float:
        return _finite(value, "workflow_cost_budget_usd", minimum=0.000001)

    @field_validator("workflow_latency_budget_ms", "expected_llm_calls")
    @classmethod
    def validate_positive_int(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("budget counts and durations must be positive integers")
        return value

    @field_validator(
        "maximum_provider_exploration_ms",
        "controller_reserve_ms",
        "compaction_reserve_ms",
        "latency_safety_margin_ms",
    )
    @classmethod
    def validate_nonnegative_int(cls, value: int) -> int:
        if isinstance(value, bool) or value < 0:
            raise ValueError("latency allocation values must be non-negative integers")
        return value

    @field_validator(
        "input_cost_fraction",
        "provider_exploration_fraction",
        "minimum_quality",
    )
    @classmethod
    def validate_fraction(cls, value: float) -> float:
        number = _finite(value, "policy fraction")
        if number > 1:
            raise ValueError("policy fractions must be <= 1")
        return number

    @field_validator("cost_priority", "latency_priority", "quality_priority", "cache_priority")
    @classmethod
    def validate_priority(cls, value: float) -> float:
        return _finite(value, "policy priority")


class ProviderRoute(FrozenModel):
    route_id: str
    provider: str
    native_model_id: str
    input_price_per_million: float
    output_price_per_million: float
    cache_read_price_per_million: float | None = None
    cache_write_price_per_million: float | None = None
    p95_ttft_ms: int
    p95_total_latency_ms: int
    guaranteed: bool = False
    cache_supported: bool = False
    cache_automatic: bool = False
    cache_ttl_seconds: int = 300
    capabilities: frozenset[str] = frozenset()

    @field_validator("route_id", "provider", "native_model_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("route identifiers cannot be empty")
        return value.strip()

    @field_validator(
        "input_price_per_million",
        "output_price_per_million",
        "cache_read_price_per_million",
        "cache_write_price_per_million",
    )
    @classmethod
    def validate_price(cls, value: float | None) -> float | None:
        return None if value is None else _finite(value, "provider price")

    @field_validator("p95_ttft_ms", "p95_total_latency_ms", "cache_ttl_seconds")
    @classmethod
    def validate_route_duration(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("route durations must be positive integers")
        return value

    @model_validator(mode="after")
    def validate_latency_order(self) -> ProviderRoute:
        if self.p95_total_latency_ms < self.p95_ttft_ms:
            raise ValueError("p95_total_latency_ms cannot be smaller than p95_ttft_ms")
        return self


class ModelCandidate(FrozenModel):
    model_id: str
    quality: float
    context_window_tokens: int
    strengths: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()
    router_score: float | None = None
    router_payload: dict[str, Any] = Field(default_factory=dict)
    routes: tuple[ProviderRoute, ...]

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model_id cannot be empty")
        return value.strip()

    @field_validator("quality", "router_score")
    @classmethod
    def validate_score(cls, value: float | None) -> float | None:
        if value is None:
            return None
        number = _finite(value, "model score")
        if number > 1:
            raise ValueError("model scores must be <= 1")
        return number

    @field_validator("context_window_tokens")
    @classmethod
    def validate_context_window(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("context_window_tokens must be positive")
        return value

    @model_validator(mode="after")
    def validate_routes(self) -> ModelCandidate:
        if not self.routes:
            raise ValueError("a model candidate requires at least one provider route")
        if len({route.route_id for route in self.routes}) != len(self.routes):
            raise ValueError("provider route IDs must be unique within a model")
        return self


class CallIntent(FrozenModel):
    session_id: str
    task: str
    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...] = ()
    required_capabilities: frozenset[str] = frozenset()
    predicted_output_tokens: int = 512
    priority: float = 1.0
    expected_remaining_calls: int | None = None

    @field_validator("session_id", "task")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("session_id and task cannot be empty")
        return value.strip()

    @field_validator("predicted_output_tokens")
    @classmethod
    def validate_output_tokens(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("predicted_output_tokens must be positive")
        return value

    @field_validator("priority")
    @classmethod
    def validate_call_priority(cls, value: float) -> float:
        return _finite(value, "call priority", minimum=0.05)


class CallBudget(FrozenModel):
    run_id: str
    call_id: str
    sequence: int
    cost_usd: float
    input_cost_usd: float
    output_cost_usd: float
    latency_ms: int
    allocation_weight: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CacheValue(FrozenModel):
    model_id: str
    provider: str | None = None
    exact_reuse: bool = False
    cached_tokens: int = 0
    input_cost_usd: float
    retained_value_usd: float = 0.0
    switch_cost_usd: float = 0.0


class CacheAnalysis(FrozenModel):
    total_tokens: int
    message_tokens: tuple[int, ...]
    prefix_hash: str | None
    cacheable_message_indices: tuple[int, ...]
    protected_message_indices: tuple[int, ...]
    compactable_message_indices: tuple[int, ...]
    cacheable_tokens: int
    protected_dynamic_tokens: int
    compactable_tokens: int
    last_model_id: str | None = None
    last_provider: str | None = None
    values: dict[str, CacheValue] = Field(default_factory=dict)


class ModelDecision(FrozenModel):
    candidate: ModelCandidate
    route: ProviderRoute
    score: float
    base_router_score: float
    predicted_cost_usd: float
    predicted_latency_ms: int
    cache_value: CacheValue
    reasons: tuple[str, ...] = ()


class ProviderRoutingMode(StrEnum):
    STICKY = "sticky"
    DEADLINE = "deadline"
    DIRECT = "direct"


class ProviderRoutingPlan(FrozenModel):
    mode: ProviderRoutingMode
    model_id: str
    routes: tuple[ProviderRoute, ...]
    start_within_ms: int
    total_timeout_ms: int
    latency_budget_ms: int
    reason: str


class CompactionPlan(FrozenModel):
    input_budget_usd: float
    estimated_input_cost_usd: float
    target_tokens: int
    required_reduction_tokens: int
    compactable_message_indices: tuple[int, ...]


class CompactionRecord(FrozenModel):
    message_index: int
    retrieval_id: str
    original_hash: str
    tokens_before: int
    tokens_after: int


class CompactionResult(FrozenModel):
    messages: tuple[dict[str, Any], ...]
    tokens_before: int
    tokens_after: int
    target_tokens: int
    target_met: bool
    records: tuple[CompactionRecord, ...] = ()


class PreparedCall(FrozenModel):
    intent: CallIntent
    budget: CallBudget
    cache: CacheAnalysis
    model: ModelDecision
    provider: ProviderRoutingPlan
    compaction: CompactionResult
    preparation_latency_ms: int = 0


class ModelUsage(FrozenModel):
    input_tokens: int
    output_tokens: int
    latency_ms: int
    provider: str
    model_id: str
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None

    @field_validator(
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "cache_read_tokens",
        "cache_write_tokens",
    )
    @classmethod
    def validate_usage_counts(cls, value: int) -> int:
        if isinstance(value, bool) or value < 0:
            raise ValueError("usage counts must be non-negative integers")
        return value


class RunStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class RunSnapshot(FrozenModel):
    run_id: str
    session_id: str
    status: RunStatus
    completed_calls: int
    spent_cost_usd: float
    spent_model_latency_ms: int
    reserved_cost_usd: float
    reserved_latency_ms: int
    tool_calls: int
    started_at: datetime
    finished_at: datetime | None = None
