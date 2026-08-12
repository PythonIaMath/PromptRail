"""Correlate asynchronous agent steps with a PromptRail runtime run."""

from __future__ import annotations

import asyncio
import os

from promptrail import PromptRail, current_runtime_context, event, run


async def agent_step(name: str, delay: float = 0.01) -> str:
    event("agent.step.started", {"step": name})
    await asyncio.sleep(delay)
    event("agent.step.completed", {"step": name})
    return f"{name}:ok"


async def main() -> None:
    rail = PromptRail(project=os.environ.get("PROMPTRAIL_PROJECT", "examples"))

    with run("async-agent", runtime=rail, metadata={"example": "async_agent"}):
        ctx = current_runtime_context()
        plan = await agent_step("plan")
        answer = await agent_step("answer")
        print({"run_id": getattr(ctx, "run_id", None), "steps": [plan, answer]})


if __name__ == "__main__":
    asyncio.run(main())
