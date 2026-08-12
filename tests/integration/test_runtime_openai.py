from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from openai import AsyncOpenAI, OpenAI

from promptrail import PromptRail, run, wrap_openai

GATEWAY = "https://gateway.promptrail.test/v1"


def _response(request: httpx.Request, captured: list[httpx.Request]) -> httpx.Response:
    captured.append(request)
    payload: dict[str, Any] = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return httpx.Response(
        200, content=json.dumps(payload), headers={"content-type": "application/json"}
    )


def _assert_correlation(request: httpx.Request, run_id: str, user_id: str) -> None:
    headers = request.headers
    assert headers["x-promptrail-run-id"] == run_id
    assert headers["x-promptrail-user-id"] == user_id
    assert headers["x-promptrail-application"] == "official-openai-test"
    assert headers["x-promptrail-schema-version"] == "1.0"
    if "traceparent" in headers:
        _, trace_id, span_id, _ = headers["traceparent"].split("-")
        assert headers["x-promptrail-trace-id"] == trace_id
        assert headers["x-promptrail-span-id"] == span_id


def test_official_openai_client_carries_current_runtime_headers() -> None:
    captured: list[httpx.Request] = []
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: _response(request, captured))
    )
    PromptRail.init(
        application="official-openai-test",
        gateway_url=GATEWAY,
        export_enabled=False,
        user_id="official-user",
    )
    client = wrap_openai(OpenAI(base_url=GATEWAY, api_key="pr_test", http_client=http_client))
    try:
        with run(run_id="run_official_sync"):
            response = client.chat.completions.create(
                model="test-model", messages=[{"role": "user", "content": "hello"}]
            )
        assert response.choices[0].message.content == "ok"
        _assert_correlation(captured[0], "run_official_sync", "official-user")
    finally:
        PromptRail.shutdown(timeout=0)
        http_client.close()


def test_official_async_openai_client_carries_current_runtime_headers() -> None:
    async def check() -> None:
        captured: list[httpx.Request] = []
        transport = httpx.MockTransport(lambda request: _response(request, captured))
        http_client = httpx.AsyncClient(transport=transport)
        PromptRail.init(
            application="official-openai-test",
            gateway_url=GATEWAY,
            export_enabled=False,
            user_id="configured-user",
        )
        client = wrap_openai(
            AsyncOpenAI(base_url=GATEWAY, api_key="pr_test", http_client=http_client)
        )
        try:
            async with run(user_id="async-user", run_id="run_official_async"):
                response = await client.chat.completions.create(
                    model="test-model", messages=[{"role": "user", "content": "hello"}]
                )
            assert response.choices[0].message.content == "ok"
            _assert_correlation(captured[0], "run_official_async", "async-user")
        finally:
            PromptRail.shutdown(timeout=0)
            await http_client.aclose()

    asyncio.run(check())
