"""Gemma-owned per-call budget allocation boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from .errors import BudgetError
from .models import BudgetAllocationDecision, BudgetAllocationRequest

# This is the Gemma 12B identity used by LeRouter. The budget allocator must be
# served separately from LeRouter's bi-encoder ranking artifact because that
# artifact produces embeddings, not structured generation.
GEMMA_12B_MODEL_ID = "google/gemma-4-12B"


@dataclass(frozen=True, slots=True)
class BudgetModelResponse:
    """Structured model output plus transport-attested model identity."""

    payload: Mapping[str, Any]
    model_id: str


class StructuredBudgetGenerator(Protocol):
    """Generate one strict JSON object with a specifically requested model."""

    def generate_budget(
        self,
        *,
        model_id: str,
        system_instruction: str,
        request: Mapping[str, Any],
        output_schema: Mapping[str, Any],
    ) -> BudgetModelResponse: ...


class CallBudgetAllocator(Protocol):
    """Allocate one call without assuming how many later calls will exist."""

    @property
    def model_id(self) -> str: ...

    def allocate(self, request: BudgetAllocationRequest) -> BudgetAllocationDecision: ...


class Gemma12BBudgetAllocator:
    """Ask a pinned Gemma 12B deployment for every per-call allocation."""

    SYSTEM_INSTRUCTION = """You are PromptRail's per-call cost and latency controller.
Allocate a cost budget and an end-to-end latency budget for exactly one LLM call in an
open-ended agent execution. There is no known total number of calls. Use the enterprise
analytics insight, current task, cumulative agent state, cache economics, and optional hard
limits supplied in the request. A null hard limit means no hard limit was defined; never invent
one. Treat non-null remaining and per-call hard limits as ceilings. Choose an input-cost fraction
that leaves a viable output budget. Decide the minimum required_context_tokens needed to do the
task correctly. Review the deterministic importance assigned to context blocks and override only
ambiguous mistakes, at most 12 blocks; protocol blocks and the latest user request are immutable.
Return only the requested JSON object with a short,
auditable reason. Do not choose a model or provider; downstream systems do that within your
allocation."""

    def __init__(
        self,
        generator: StructuredBudgetGenerator,
        *,
        model_id: str = GEMMA_12B_MODEL_ID,
    ) -> None:
        normalized = model_id.strip()
        if normalized != GEMMA_12B_MODEL_ID:
            raise ValueError(f"budget allocator must use {GEMMA_12B_MODEL_ID}, got {normalized!r}")
        self._generator = generator
        self._model_id = normalized

    @property
    def model_id(self) -> str:
        return self._model_id

    def allocate(self, request: BudgetAllocationRequest) -> BudgetAllocationDecision:
        response = self._generator.generate_budget(
            model_id=self._model_id,
            system_instruction=self.SYSTEM_INSTRUCTION,
            request=request.model_dump(mode="json"),
            output_schema=BudgetAllocationDecision.model_json_schema(),
        )
        if response.model_id.strip() != self._model_id:
            raise BudgetError(
                "budget endpoint did not attest the required Gemma 12B model: "
                f"expected {self._model_id!r}, got {response.model_id!r}"
            )
        if not isinstance(response.payload, Mapping):
            raise BudgetError("Gemma budget endpoint returned a non-object allocation")
        try:
            return BudgetAllocationDecision.model_validate(response.payload)
        except ValidationError as error:
            raise BudgetError(f"Gemma returned an invalid call allocation: {error}") from error


__all__ = [
    "GEMMA_12B_MODEL_ID",
    "BudgetModelResponse",
    "CallBudgetAllocator",
    "Gemma12BBudgetAllocator",
    "StructuredBudgetGenerator",
]
