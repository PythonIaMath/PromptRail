"""Basic OpenAI call correlated with a PromptRail runtime run.

Set OPENAI_API_KEY in your environment before running. This example records
runtime correlation metadata only. It does not tune prompts, cache requests, or
change client-side behavior for optimization.
"""

from __future__ import annotations

import os

from promptrail import PromptRail, current_runtime_context, event, run, wrap_openai


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Set OPENAI_API_KEY to run this example.")

    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - optional example dependency
        raise SystemExit("Install the optional dependency: pip install openai") from exc

    rail = PromptRail(project=os.environ.get("PROMPTRAIL_PROJECT", "examples"))
    client = wrap_openai(OpenAI(), runtime=rail)

    with run("openai-basic", runtime=rail, metadata={"example": "openai_basic"}):
        event("example.started", {"client": "openai"})
        response = client.chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": "Say hello in one short sentence."}],
        )
        ctx = current_runtime_context()
        event("example.completed", {"run_id": getattr(ctx, "run_id", None)})
        print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
