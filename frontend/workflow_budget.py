"""Pure calculations for hierarchical, user-managed workflow budgets."""

from __future__ import annotations

import json
import math
from typing import Any


class WorkflowBudgetError(ValueError):
    """Raised when a workflow budget cannot safely authorize a call."""


def finite_positive(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise WorkflowBudgetError(f"{field} must be a positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise WorkflowBudgetError(f"{field} must be a positive number") from error
    if not math.isfinite(number) or number <= 0:
        raise WorkflowBudgetError(f"{field} must be a positive number")
    return number


def normalized_non_negative(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise WorkflowBudgetError(f"{field} must be non-negative")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise WorkflowBudgetError(f"{field} must be non-negative") from error
    if not math.isfinite(number) or number < 0:
        raise WorkflowBudgetError(f"{field} must be non-negative")
    return number


def scope_budget_view(scope: dict[str, Any]) -> dict[str, float | str]:
    maximum = finite_positive(scope.get("maxUsd"), "scope.maxUsd")
    spent = float(scope.get("spentUsd", 0.0))
    reserved = float(scope.get("reservedUsd", 0.0))
    if not all(math.isfinite(value) and value >= 0 for value in (spent, reserved)):
        raise WorkflowBudgetError("scope spend and reservation values must be non-negative")
    available = maximum - spent - reserved
    if available <= 0:
        raise WorkflowBudgetError("workflow budget is exhausted")
    return {
        "scope_id": str(scope.get("id") or ""),
        "max_usd": maximum,
        "spent_usd": spent,
        "reserved_usd": reserved,
        "available_usd": available,
    }


def effective_scope_target(
    scopes: list[dict[str, Any]],
    horizon_by_scope: dict[str, Any],
    *,
    current_request_weight: float,
) -> dict[str, Any]:
    if not scopes:
        raise WorkflowBudgetError("at least one active workflow scope is required")
    current_weight = finite_positive(current_request_weight, "current_request_weight")
    targets: list[dict[str, Any]] = []
    for scope in scopes:
        view = scope_budget_view(scope)
        scope_id = str(view["scope_id"])
        if not scope_id or scope_id not in horizon_by_scope:
            raise WorkflowBudgetError(f"missing horizon prediction for scope {scope_id or '<unknown>'}")
        prediction = horizon_by_scope[scope_id]
        if not isinstance(prediction, dict):
            raise WorkflowBudgetError(f"invalid horizon prediction for scope {scope_id}")
        remaining_calls = normalized_non_negative(
            prediction.get("remaining_calls_after_current"),
            "remaining_calls_after_current",
        )
        remaining_work_weight = normalized_non_negative(
            prediction.get("remaining_work_weight_after_current"),
            "remaining_work_weight_after_current",
        )
        target = (
            float(view["available_usd"])
            * current_weight
            / (current_weight + remaining_work_weight)
        )
        targets.append(
            {
                **view,
                "remaining_calls_after_current": remaining_calls,
                "remaining_work_weight_after_current": remaining_work_weight,
                "current_request_weight": current_weight,
                "confidence": prediction.get("confidence"),
                "target_usd": target,
            }
        )
    return {
        "effective_target_usd": min(float(item["target_usd"]) for item in targets),
        "available_usd": min(float(item["available_usd"]) for item in targets),
        "scope_targets": targets,
    }


def workflow_request_weight(
    *,
    input_tokens: int,
    output_tokens: float,
    difficulty: float,
    median_weighted_tokens: float,
    output_token_weight: float,
    size_beta: float,
    difficulty_alpha: float,
    minimum: float,
    maximum: float,
) -> dict[str, float]:
    input_length = finite_positive(input_tokens, "input_tokens")
    output_length = finite_positive(output_tokens, "output_tokens")
    normalized_difficulty = normalized_non_negative(difficulty, "difficulty")
    median = finite_positive(median_weighted_tokens, "median_weighted_tokens")
    output_weight = normalized_non_negative(output_token_weight, "output_token_weight")
    beta = normalized_non_negative(size_beta, "size_beta")
    alpha = normalized_non_negative(difficulty_alpha, "difficulty_alpha")
    lower = finite_positive(minimum, "request_weight_min")
    upper = finite_positive(maximum, "request_weight_max")
    if upper < lower:
        raise WorkflowBudgetError("request_weight_max must be greater than or equal to request_weight_min")

    weighted_tokens = input_length + output_weight * output_length
    size_factor = weighted_tokens / median
    unclipped = (size_factor**beta) * math.exp(alpha * normalized_difficulty)
    request_weight = min(upper, max(lower, unclipped))
    return {
        "difficulty": normalized_difficulty,
        "input_length_tokens": input_length,
        "output_length_prediction_tokens": output_length,
        "output_token_weight": output_weight,
        "weighted_tokens": weighted_tokens,
        "median_weighted_tokens": median,
        "size_factor": size_factor,
        "request_size_beta": beta,
        "request_difficulty_alpha": alpha,
        "unclipped_request_weight": unclipped,
        "request_weight_min": lower,
        "request_weight_max": upper,
        "request_weight": request_weight,
    }


def conservative_input_tokens(messages: list[dict[str, Any]], request_options: dict[str, Any]) -> int:
    """A byte-level upper bound including tool and response schemas.

    Supported workflow providers must use byte/sub-byte tokenizers. Protocol
    framing is covered by a fixed per-message allowance.
    """

    serialized = json.dumps(
        {"messages": messages, "tools": request_options.get("tools"), "response_format": request_options.get("response_format")},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return max(1, len(serialized) + (16 * len(messages)) + 32)


def predicted_provider_cost(
    *,
    input_tokens: int,
    output_tokens: float,
    input_price_per_million: Any,
    output_price_per_million: Any,
) -> float:
    input_price = finite_positive(input_price_per_million, "input_price_per_million")
    output_price = finite_positive(output_price_per_million, "output_price_per_million")
    predicted_output = finite_positive(output_tokens, "predicted_output_tokens")
    return (input_tokens * input_price + predicted_output * output_price) / 1_000_000.0


def authorize_candidate(
    *,
    available_usd: float,
    effective_target_usd: float,
    predicted_cost_usd: float,
    input_tokens: int,
    input_price_per_million: Any,
    output_price_per_million: Any,
    caller_max_tokens: int | None,
) -> dict[str, Any]:
    available = finite_positive(available_usd, "available_usd")
    target = finite_positive(effective_target_usd, "effective_target_usd")
    predicted_cost = finite_positive(predicted_cost_usd, "predicted_cost_usd")
    if predicted_cost > available + 1e-12:
        raise WorkflowBudgetError("predicted call cost exceeds the active workflow budget")
    input_price = finite_positive(input_price_per_million, "input_price_per_million")
    output_price = finite_positive(output_price_per_million, "output_price_per_million")
    call_limit = min(available, max(target, predicted_cost))
    input_reserve = input_tokens * input_price / 1_000_000.0
    output_budget = call_limit - input_reserve
    if output_budget <= 0:
        raise WorkflowBudgetError("workflow call limit cannot fund any output tokens")
    max_output_tokens = math.floor(output_budget * 1_000_000.0 / output_price)
    if caller_max_tokens is not None:
        if caller_max_tokens <= 0:
            raise WorkflowBudgetError("caller max_tokens must be positive")
        max_output_tokens = min(max_output_tokens, int(caller_max_tokens))
    if max_output_tokens < 1:
        raise WorkflowBudgetError("workflow call limit cannot fund any output tokens")
    return {
        "call_limit_usd": call_limit,
        "input_reserve_usd": input_reserve,
        "max_output_tokens": max_output_tokens,
    }
