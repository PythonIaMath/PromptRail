"""Use an explicit PromptRail runtime run around existing application work."""

from __future__ import annotations

import os

from promptrail import PromptRail, current_runtime_context, event, run


def classify_ticket(text: str) -> str:
    event("ticket.classification.started", {"chars": len(text)})
    label = "billing" if "invoice" in text.lower() else "general"
    event("ticket.classification.completed", {"label": label})
    return label


def main() -> None:
    rail = PromptRail(project=os.environ.get("PROMPTRAIL_PROJECT", "examples"))
    ticket = os.environ.get("EXAMPLE_TICKET", "Please help me find my invoice.")

    with run("explicit-ticket-run", runtime=rail, metadata={"example": "explicit_run"}):
        ctx = current_runtime_context()
        print(f"run={getattr(ctx, 'run_id', 'unknown')} label={classify_ticket(ticket)}")


if __name__ == "__main__":
    main()
