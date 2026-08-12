"""Run parallel async branches under one run with isolated child contexts."""

from __future__ import annotations

import asyncio
import os

from promptrail import PromptRail, current_runtime_context, event, run


async def worker(name: str) -> dict[str, str | None]:
    event("branch.start", name=name)
    await asyncio.sleep(0.01)
    context = current_runtime_context()
    event("branch.end", name=name, status="success")
    return {"worker": name, "run_id": context.run_id, "user_id": context.user_id}


async def main() -> None:
    PromptRail.init(
        api_key=os.environ.get("PROMPTRAIL_API_KEY"),
        application="parallel-agents-example",
        export_enabled=bool(os.environ.get("PROMPTRAIL_API_KEY")),
    )
    try:
        async with run(user_id="parallel-example-user"):
            results = await asyncio.gather(worker("research"), worker("review"), worker("write"))
            print(results)
    finally:
        PromptRail.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
