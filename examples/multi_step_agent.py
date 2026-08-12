"""Correlate a multi-step agent workflow with PromptRail runtime events."""

from __future__ import annotations

import os

from promptrail import PromptRail, current_runtime_context, event, run


def retrieve(question: str) -> list[str]:
    event("agent.retrieve", {"question_chars": len(question)})
    return ["Runtime events share a run context.", "Correlation helps observability."]


def reason(question: str, facts: list[str]) -> str:
    event("agent.reason", {"facts": len(facts)})
    return f"Question: {question}\nAnswer: {facts[0]}"


def respond(answer: str) -> str:
    event("agent.respond", {"answer_chars": len(answer)})
    return answer


def main() -> None:
    rail = PromptRail(project=os.environ.get("PROMPTRAIL_PROJECT", "examples"))
    question = os.environ.get("EXAMPLE_QUESTION", "How does runtime correlation help?")

    with run("multi-step-agent", runtime=rail, metadata={"example": "multi_step_agent"}):
        ctx = current_runtime_context()
        facts = retrieve(question)
        answer = respond(reason(question, facts))
        print({"run_id": getattr(ctx, "run_id", None), "answer": answer})


if __name__ == "__main__":
    main()
