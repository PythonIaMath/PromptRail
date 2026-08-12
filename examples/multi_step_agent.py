"""Multi-step agent with LLM, tool, parallel branch, and verification spans."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from promptrail import PromptRail, current_runtime_context, run, wrap_openai


async def main() -> None:
    api_key = os.environ.get("PROMPTRAIL_API_KEY")
    if not api_key:
        raise SystemExit("Set PROMPTRAIL_API_KEY to run this example.")

    try:
        from openai import AsyncOpenAI
        from opentelemetry import trace
    except ImportError as exc:  # pragma: no cover - optional example dependency
        raise SystemExit(
            'Install optional dependencies: pip install "promptrail[runtime]"'
        ) from exc

    PromptRail.init(
        api_key=api_key,
        application="multi-step-agent",
        environment="development",
        user_id=lambda: os.environ.get("EXAMPLE_USER_ID", "example-user"),
    )
    client = wrap_openai(AsyncOpenAI(base_url="https://api.promptrail.ai/v1", api_key=api_key))
    tracer = trace.get_tracer("promptrail.examples.multi-step")
    model = os.environ.get("PROMPTRAIL_MODEL", "gpt-4o-mini")

    async def llm(prompt: str) -> str:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    async def tool(name: str, delay: float = 0.01) -> dict[str, Any]:
        with tracer.start_as_current_span(name, attributes={"tool.name": name}):
            await asyncio.sleep(delay)
            return {"tool": name, "status": "success", "output_size_bytes": 64}

    try:
        async with run():
            with tracer.start_as_current_span("planner", attributes={"agent.name": "planner"}):
                plan = await llm("Plan how to answer: How does PromptRail correlate a run?")

            search = await tool("search_repository")
            synthesis = await llm(f"Synthesize this plan and tool metadata: {plan[:200]} {search}")
            parallel = await asyncio.gather(tool("inspect_tests"), tool("inspect_docs"))

            with tracer.start_as_current_span(
                "verification", attributes={"workflow.name": "verification"}
            ):
                verified = await llm(
                    f"Verify this draft using tool statuses only: {synthesis[:300]} {parallel}"
                )

            final = await llm(f"Give a concise final answer based on: {verified[:500]}")
            print({"context": current_runtime_context(), "answer": final})
    finally:
        PromptRail.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
