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
    """Optional analytics guidance for a class of agent work."""

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
    """Validated analytics insight and optional enterprise hard limits."""

    schema_version: Literal[1] = 1
    analytics_insight: str
    hard_agent_cost_limit_usd: float | None = None
    hard_agent_latency_limit_ms: int | None = None
    hard_call_cost_limit_usd: float | None = None
    hard_call_latency_limit_ms: int | None = None
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

    @field_validator("analytics_insight")
    @classmethod
    def validate_analytics_insight(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("analytics insight cannot be empty")
        if len(normalized) > 8_000:
            raise ValueError("analytics insight exceeds 8,000 characters")
        return normalized

    @field_validator("hard_agent_cost_limit_usd", "hard_call_cost_limit_usd")
    @classmethod
    def validate_optional_cost(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite(value, "hard cost limit", minimum=0.000001)

    @field_validator("hard_agent_latency_limit_ms", "hard_call_latency_limit_ms")
    @classmethod
    def validate_optional_latency(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or value <= 0:
            raise ValueError("hard latency limits must be positive integers")
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
    predicted_output_tokens: int | None = None
    max_output_tokens: int | None = None
    priority: float = 1.0

    @field_validator("session_id", "task")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("session_id and task cannot be empty")
        return value.strip()

    @field_validator("predicted_output_tokens", "max_output_tokens")
    @classmethod
    def validate_output_tokens(cls, value: int | None) -> int | None:
        if value is not None and (isinstance(value, bool) or value <= 0):
            raise ValueError("output token values must be positive")
        return value

    @field_validator("priority")
    @classmethod
    def validate_call_priority(cls, value: float) -> float:
        return _finite(value, "call priority", minimum=0.05)


class ContextBlock(FrozenModel):
    """One independently compactable unit of conversation context."""

    block_id: str
    message_indices: tuple[int, ...]
    block_type: Literal["protocol", "user", "assistant", "tool", "test", "patch", "generic"]
    tokens: int
    age: int
    cached: bool = False
    task_overlap: float = Field(ge=0, le=1)
    redundancy: float = Field(ge=0, le=1)
    cache_invalidation_cost: float = Field(ge=0)
    importance: float = Field(ge=0, le=1)
    structurally_immutable: bool = False


class ImportanceOverride(FrozenModel):
    block_id: str
    importance: float = Field(ge=0, le=1)


class OutputLengthPrediction(FrozenModel):
    predicted_tokens: int
    raw_predicted_tokens: float
    latency_ms: int
    model_id: str = "lerouter-modernbert-output-length"

    @field_validator("predicted_tokens")
    @classmethod
    def validate_predicted_tokens(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("predicted_tokens must be a positive integer")
        return value

    @field_validator("latency_ms")
    @classmethod
    def validate_prediction_latency(cls, value: int) -> int:
        if isinstance(value, bool) or value < 0:
            raise ValueError("prediction latency must be a non-negative integer")
        return value

    @field_validator("raw_predicted_tokens")
    @classmethod
    def validate_raw_prediction(cls, value: float) -> float:
        return _finite(value, "raw_predicted_tokens", minimum=0.000001)


class BudgetAllocationDecision(FrozenModel):
    """The per-call allocation proposed by the pinned Gemma 12B controller."""

    schema_version: Literal[2] = 2
    cost_usd: float
    latency_ms: int
    input_cost_fraction: float = Field(ge=0.000001, lt=1)
    required_context_tokens: int
    importance_overrides: tuple[ImportanceOverride, ...] = ()
    reason: str

    @field_validator("required_context_tokens")
    @classmethod
    def validate_required_context(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("required_context_tokens must be positive")
        return value

    @field_validator("importance_overrides")
    @classmethod
    def validate_importance_overrides(
        cls, value: tuple[ImportanceOverride, ...]
    ) -> tuple[ImportanceOverride, ...]:
        if len(value) > 12:
            raise ValueError("Gemma may override at most 12 ambiguous context blocks")
        if len({item.block_id for item in value}) != len(value):
            raise ValueError("importance override block IDs must be unique")
        return value

    @field_validator("cost_usd")
    @classmethod
    def validate_decision_cost(cls, value: float) -> float:
        return _finite(value, "allocated call cost", minimum=0.000001)

    @field_validator("latency_ms")
    @classmethod
    def validate_decision_latency(cls, value: int) -> int:
        if isinstance(value, bool) or value <= 0:
            raise ValueError("allocated call latency must be a positive integer")
        return value

    @field_validator("input_cost_fraction")
    @classmethod
    def validate_input_fraction(cls, value: float) -> float:
        number = _finite(value, "input_cost_fraction", minimum=0.000001)
        if number >= 1:
            raise ValueError("input_cost_fraction must be < 1")
        return number

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("budget allocation reason cannot be empty")
        if len(normalized) > 1_000:
            raise ValueError("budget allocation reason exceeds 1,000 characters")
        return normalized


class CallBudget(FrozenModel):
    run_id: str
    call_id: str
    sequence: int
    cost_usd: float
    input_cost_usd: float
    output_cost_usd: float
    latency_ms: int
    allocator_model_id: str
    allocator_reason: str
    required_context_tokens: int
    importance_overrides: tuple[ImportanceOverride, ...] = ()
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
    tool_tokens: int
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
    context_blocks: tuple[ContextBlock, ...] = ()


class BudgetCandidateOption(FrozenModel):
    """Feasibility evidence for Gemma; downstream LeRouter still chooses."""

    model_id: str
    quality: float
    context_fits: bool
    required_capabilities_supported: bool
    cheapest_predicted_cost_usd: float
    cheapest_input_cost_fraction: float = Field(ge=0.000001, lt=1)
    fastest_predicted_latency_ms: int
    exact_cache_reuse: bool
    cached_tokens: int


class BudgetAllocationRequest(FrozenModel):
    """Bounded context supplied to Gemma for one open-ended agent call."""

    schema_version: Literal[2] = 2
    run_id: str
    session_id: str
    call_id: str
    sequence: int
    analytics_insight: str
    source_digest: str | None = None
    task_rules: tuple[TaskRule, ...] = ()
    cost_priority: float
    latency_priority: float
    quality_priority: float
    cache_priority: float
    task: str
    input_tokens: int
    predicted_output_tokens: int
    priority: float
    required_capabilities: tuple[str, ...] = ()
    completed_calls: int
    tool_calls: int
    elapsed_agent_ms: int
    spent_cost_usd: float
    spent_latency_ms: int
    reserved_cost_usd: float
    reserved_latency_ms: int
    hard_agent_cost_limit_usd: float | None = None
    hard_agent_latency_limit_ms: int | None = None
    hard_call_cost_limit_usd: float | None = None
    hard_call_latency_limit_ms: int | None = None
    remaining_hard_cost_usd: float | None = None
    remaining_hard_latency_ms: int | None = None
    cacheable_tokens: int
    compactable_tokens: int
    last_model_id: str | None = None
    last_provider: str | None = None
    cache_input_cost_usd_by_model: dict[str, float] = Field(default_factory=dict)
    exact_cache_models: tuple[str, ...] = ()
    candidate_options: tuple[BudgetCandidateOption, ...]
    context_blocks: tuple[ContextBlock, ...]


class ModelDecision(FrozenModel):
    candidate: ModelCandidate
    route: ProviderRoute
    score: float
    base_router_score: float
    predicted_cost_usd: float
    predicted_input_cost_usd: float
    predicted_output_tokens: int
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
    importance_by_message_index: dict[int, float] = Field(default_factory=dict)


class CompactionRecord(FrozenModel):
    message_index: int
    original_hash: str
    tokens_before: int
    tokens_after: int
    block_type: str
    importance: float


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
    model_alternatives: tuple[ModelDecision, ...] = ()
    preparation_latency_ms: int = 0
    predicted_output_tokens: int
    output_prediction_ms: int = 0
    context_analysis_ms: int = 0
    gemma_allocation_ms: int = 0
    semantic_ranking_ms: int = 0
    candidate_feasibility_ms: int = 0
    compaction_ms: int = 0
    provider_planning_ms: int = 0
    control_plane_total_ms: int = 0


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
