"""PromptRail public API."""

from .budgeting import (
    GEMMA_12B_MODEL_ID,
    CallBudgetAllocator,
    Gemma12BBudgetAllocator,
    StructuredBudgetGenerator,
)
from .client import PromptRail
from .clients import (
    LEROUTER_BIENCODER_MODEL_ID,
    PRODUCTION_LEROUTER_LENGTH_PREDICTOR_URL,
    PRODUCTION_LEROUTER_RANKER_URL,
    PRODUCTION_LEROUTER_RUN_ID,
    Gemma12BHTTPGenerator,
    LeRouterHTTPRanker,
    LeRouterOutputLengthPredictor,
    LeRouterPolicyGenerator,
)
from .config import RuntimeConfig
from .context import RuntimeContext, copy_context, run, submit_with_context
from .controller import GlobalController
from .errors import (
    BudgetError,
    CompactionError,
    IntegrationError,
    PolicyError,
    PromptRailError,
    RoutingError,
)
from .gateway import PromptRailGateway
from .historical import (
    HistoricalImportResult,
    TraceSource,
    TraceSourceConfiguration,
    import_historical_traces,
)
from .integrations import async_httpx_request_hook, httpx_request_hook, wrap_openai
from .models import (
    BudgetAllocationDecision,
    BudgetAllocationRequest,
    BudgetCandidateOption,
    CallBudget,
    CallIntent,
    ContextBlock,
    ImportanceOverride,
    ModelCandidate,
    OperatingPolicy,
    ProviderRoute,
    RunSnapshot,
    RunStatus,
    TaskRule,
)
from .policy import EnterprisePolicyAgent, PolicyGenerator, SuppliedPolicyAgent
from .propagation import PromptRailContextPropagator, inject_headers
from .routing import CacheAwareLeRouter, LeRouterRanker, SuppliedLeRouterRanker
from .sdk import (
    current_run_id,
    current_runtime_context,
    current_trace_id,
    current_user_id,
)
from .sdk import (
    emit_event as event,
)
from .tracing import EventType, PromptRailEvent

__all__ = [
    "GEMMA_12B_MODEL_ID",
    "LEROUTER_BIENCODER_MODEL_ID",
    "PRODUCTION_LEROUTER_LENGTH_PREDICTOR_URL",
    "PRODUCTION_LEROUTER_RANKER_URL",
    "PRODUCTION_LEROUTER_RUN_ID",
    "BudgetAllocationDecision",
    "BudgetAllocationRequest",
    "BudgetCandidateOption",
    "BudgetError",
    "CacheAwareLeRouter",
    "CallBudget",
    "CallBudgetAllocator",
    "CallIntent",
    "CompactionError",
    "ContextBlock",
    "EnterprisePolicyAgent",
    "EventType",
    "Gemma12BBudgetAllocator",
    "Gemma12BHTTPGenerator",
    "GlobalController",
    "HistoricalImportResult",
    "ImportanceOverride",
    "IntegrationError",
    "LeRouterHTTPRanker",
    "LeRouterOutputLengthPredictor",
    "LeRouterPolicyGenerator",
    "LeRouterRanker",
    "ModelCandidate",
    "OperatingPolicy",
    "PolicyError",
    "PolicyGenerator",
    "PromptRail",
    "PromptRailContextPropagator",
    "PromptRailError",
    "PromptRailEvent",
    "PromptRailGateway",
    "ProviderRoute",
    "RoutingError",
    "RunSnapshot",
    "RunStatus",
    "RuntimeConfig",
    "RuntimeContext",
    "StructuredBudgetGenerator",
    "SuppliedLeRouterRanker",
    "SuppliedPolicyAgent",
    "TaskRule",
    "TraceSource",
    "TraceSourceConfiguration",
    "async_httpx_request_hook",
    "copy_context",
    "current_run_id",
    "current_runtime_context",
    "current_trace_id",
    "current_user_id",
    "event",
    "httpx_request_hook",
    "inject_headers",
    "import_historical_traces",
    "run",
    "submit_with_context",
    "wrap_openai",
]
