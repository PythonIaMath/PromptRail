"""Use an explicit PromptRail run around an existing workflow."""

from __future__ import annotations

import os

from promptrail import PromptRail, current_runtime_context, event, run


def classify_ticket(text: str) -> str:
    event("workflow.stage", name="classify", attributes={"input_size_bytes": len(text.encode())})
    return "billing" if "invoice" in text.casefold() else "general"


def main() -> None:
    PromptRail.init(
        api_key=os.environ.get("PROMPTRAIL_API_KEY"),
        application="ticket-classifier",
        environment="development",
        export_enabled=bool(os.environ.get("PROMPTRAIL_API_KEY")),
    )
    ticket = os.environ.get("EXAMPLE_TICKET", "Please help me find my invoice.")

    try:
        with run(user_id="example-user", run_id="run_ticket_example"):
            label = classify_ticket(ticket)
            print({"context": current_runtime_context(), "label": label})
    finally:
        PromptRail.shutdown()


if __name__ == "__main__":
    main()
