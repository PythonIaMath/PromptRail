"""Public PromptRail exceptions."""


class PromptRailError(RuntimeError):
    """Base error for a visible PromptRail failure."""


class PolicyError(PromptRailError):
    """Enterprise data or generated policy is invalid."""


class BudgetError(PromptRailError):
    """An agent call cannot be authorized or settled within its allocation."""


class RoutingError(PromptRailError):
    """No model/provider route satisfies the call constraints."""


class CompactionError(PromptRailError):
    """Context compaction violated its cache or safety boundary."""


class IntegrationError(PromptRailError):
    """An optional framework or remote service contract is unavailable."""
