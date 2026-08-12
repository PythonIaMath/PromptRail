"""Show contextvars propagation through an asynchronous agent workflow."""

from __future__ import annotations

import asyncio
import os

from promptrail import PromptRail, current_runtime_context, event, run


async def agent_step(name: str) -> str:
    event("agent.start", name=name)
    await asyncio.sleep(0.01)
    event("agent.end", name=name, status="success")
    return f"{name}:ok"


async def main() -> None:
    PromptRail.init(
        api_key=os.environ.get("PROMPTRAIL_API_KEY"),
        application="async-agent-example",
        export_enabled=bool(os.environ.get("PROMPTRAIL_API_KEY")),
    )
    try:
        async with run(user_id="async-example-user"):
            steps = [await agent_step("plan"), await agent_step("answer")]
            print({"context": current_runtime_context(), "steps": steps})
    finally:
        PromptRail.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
