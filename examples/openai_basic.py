"""Send one OpenAI-compatible request through the PromptRail gateway."""

from __future__ import annotations

import os

from promptrail import PromptRail, current_runtime_context, run, wrap_openai


def main() -> None:
    api_key = os.environ.get("PROMPTRAIL_API_KEY")
    if not api_key:
        raise SystemExit("Set PROMPTRAIL_API_KEY to run this example.")

    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - optional example dependency
        raise SystemExit(
            'Install the optional dependency: pip install "promptrail[openai]"'
        ) from exc

    PromptRail.init(
        api_key=api_key,
        application="openai-basic-example",
        environment=os.environ.get("PROMPTRAIL_ENVIRONMENT", "development"),
        user_id=lambda: os.environ.get("EXAMPLE_USER_ID", "example-user"),
    )
    client = wrap_openai(OpenAI(base_url="https://api.promptrail.ai/v1", api_key=api_key))

    try:
        with run():
            response = client.chat.completions.create(
                model=os.environ.get("PROMPTRAIL_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": "Say hello in one short sentence."}],
            )
            print(current_runtime_context())
            print(response.choices[0].message.content)
    finally:
        PromptRail.shutdown()


if __name__ == "__main__":
    main()
