"""End-to-end Runtime SDK acceptance app executed in an isolated process."""

from __future__ import annotations

import asyncio
import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from openai import AsyncOpenAI, OpenAI
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

from promptrail import PromptRail, current_run_id, event, run, wrap_openai

_GATEWAY_REQUESTS: list[dict[str, Any]] = []
_DIRECT_REQUESTS: list[dict[str, Any]] = []
_EVENTS: list[dict[str, Any]] = []
_LOCK = threading.Lock()
_EVENT_STATUS = 202


def _chat_response() -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-acceptance",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "accepted"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ).encode()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_kind = "gateway"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        global _EVENT_STATUS

        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        headers = {key.lower(): value for key, value in self.headers.items()}
        if self.path == "/v1/runtime/events":
            if headers.get("content-encoding") == "gzip":
                body = gzip.decompress(body)
            with _LOCK:
                _EVENTS.extend(json.loads(body)["events"])
            self.send_response(_EVENT_STATUS)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        payload = json.loads(body)
        record = {"headers": headers, "body": payload}
        target = _GATEWAY_REQUESTS if self.server_kind == "gateway" else _DIRECT_REQUESTS
        with _LOCK:
            target.append(record)

        if payload.get("stream"):
            chunks = [
                {
                    "id": "chatcmpl-stream",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {"index": 0, "delta": {"content": "ac"}, "finish_reason": None}
                    ],
                },
                {
                    "id": "chatcmpl-stream",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {"index": 0, "delta": {"content": "cepted"}, "finish_reason": "stop"}
                    ],
                },
            ]
            response = (
                "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
                + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            response = _chat_response()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)
        self.wfile.flush()


class _DirectHandler(_Handler):
    server_kind = "direct"


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


def _message(record: dict[str, Any]) -> str:
    return str(record["body"]["messages"][-1]["content"])


def _request_for(message: str) -> dict[str, Any]:
    return next(record for record in _GATEWAY_REQUESTS if _message(record) == message)


def _assert_wire_identity(record: dict[str, Any]) -> None:
    headers = record["headers"]
    assert headers["x-promptrail-run-id"].startswith("run_")
    assert headers["x-promptrail-application"] == "runtime-acceptance"
    assert headers["x-promptrail-environment"] == "test"
    assert headers["x-promptrail-schema-version"] == "1.0"
    assert len(headers["x-promptrail-trace-id"]) == 32
    assert len(headers["x-promptrail-span-id"]) == 16
    traceparent = headers["traceparent"].split("-")
    assert traceparent[1] == headers["x-promptrail-trace-id"]
    assert traceparent[2] == headers["x-promptrail-span-id"]


async def _concurrent_calls(client: Any, tracer: Any) -> list[tuple[str, str]]:
    async def one(index: int) -> tuple[str, str]:
        run_id = f"run_acceptance_{index:03d}"
        user_id = f"tenant:user-{index:03d}"
        async with run(user_id=user_id, run_id=run_id):
            with tracer.start_as_current_span(
                f"parallel-agent-{index}",
                attributes={"promptrail.span.kind": "agent"},
            ):
                response = await client.chat.completions.create(
                    model="test-model",
                    messages=[{"role": "user", "content": f"parallel-{index}"}],
                )
                assert response.choices[0].message.content == "accepted"
        return run_id, user_id

    try:
        return list(await asyncio.gather(*(one(index) for index in range(100))))
    finally:
        await client.close()


def main() -> None:
    global _EVENT_STATUS

    gateway_server, gateway_url = _serve(_Handler)
    direct_server, direct_url = _serve(_DirectHandler)
    provider = TracerProvider()
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("promptrail.acceptance")

    PromptRail.init(
        api_key="pr_test_acceptance",
        application="runtime-acceptance",
        environment="test",
        gateway_url=gateway_url,
        runtime_events_endpoint=gateway_url + "/runtime/events",
        batch_size=4,
        flush_interval=0.02,
        request_timeout=1.0,
        max_retries=1,
        compression=True,
    )
    raw_client = OpenAI(base_url=gateway_url, api_key="pr_test_acceptance", max_retries=0)
    client = wrap_openai(raw_client)

    try:
        with (
            run(user_id="tenant:explicit", run_id="run_acceptance_explicit"),
            tracer.start_as_current_span(
                "planner", attributes={"promptrail.span.kind": "agent"}
            ),
        ):
            with tracer.start_as_current_span(
                "search_repository",
                attributes={
                    "promptrail.span.kind": "tool",
                    "tool.name": "search_repository",
                    "input_size_bytes": 310,
                    "api_key": "SECRET_ATTRIBUTE",
                    "prompt": "RAW_PROPRIETARY_PROMPT",
                },
            ):
                pass
            response = client.chat.completions.create(
                model="test-model",
                messages=[{"role": "user", "content": "explicit"}],
            )
            assert response.choices[0].message.content == "accepted"
            emitted = event("branch.start", name="verification")
            assert emitted is not None and emitted.run_id == "run_acceptance_explicit"

        with tracer.start_as_current_span(
            "native-request", attributes={"promptrail.span.kind": "agent"}
        ):
            for message in ("native-one", "native-two"):
                client.chat.completions.create(
                    model="test-model",
                    messages=[{"role": "user", "content": message}],
                )

        for message in ("implicit-one", "implicit-two"):
            client.chat.completions.create(
                model="test-model",
                messages=[{"role": "user", "content": message}],
            )

        stream = client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "stream"}],
            stream=True,
        )
        stream_run = current_run_id()
        assert "".join(chunk.choices[0].delta.content or "" for chunk in stream) == "accepted"
        assert stream_run is not None and current_run_id() is None

        async_client = wrap_openai(
            AsyncOpenAI(base_url=gateway_url, api_key="pr_test_acceptance", max_retries=0)
        )
        pairs = asyncio.run(_concurrent_calls(async_client, tracer))

        client.base_url = direct_url
        client.chat.completions.create(
            model="test-model",
            messages=[{"role": "user", "content": "direct"}],
        )
        assert _DIRECT_REQUESTS
        assert not any(
            key.startswith("x-promptrail-") for key in _DIRECT_REQUESTS[-1]["headers"]
        )
        client.base_url = gateway_url

        _EVENT_STATUS = 503
        with run(user_id="tenant:offline", run_id="run_acceptance_offline"):
            offline = client.chat.completions.create(
                model="test-model",
                messages=[{"role": "user", "content": "offline-events"}],
            )
        assert offline.choices[0].message.content == "accepted"
        time.sleep(0.15)
        _EVENT_STATUS = 202
    finally:
        PromptRail.shutdown(timeout=3.0)
        raw_client.close()
        provider.shutdown()
        gateway_server.shutdown()
        direct_server.shutdown()

    explicit = _request_for("explicit")
    _assert_wire_identity(explicit)
    assert explicit["headers"]["x-promptrail-run-id"] == "run_acceptance_explicit"
    assert explicit["headers"]["x-promptrail-user-id"] == "tenant:explicit"
    assert explicit["headers"].get("x-promptrail-parent-span-id")

    native_one = _request_for("native-one")
    native_two = _request_for("native-two")
    assert native_one["headers"]["x-promptrail-run-id"] == native_two["headers"][
        "x-promptrail-run-id"
    ]
    assert native_one["headers"]["x-promptrail-trace-id"] == native_two["headers"][
        "x-promptrail-trace-id"
    ]
    assert native_one["headers"]["x-promptrail-span-id"] != native_two["headers"][
        "x-promptrail-span-id"
    ]
    assert _request_for("implicit-one")["headers"]["x-promptrail-run-id"] != _request_for(
        "implicit-two"
    )["headers"]["x-promptrail-run-id"]
    assert _request_for("stream")["headers"]["x-promptrail-run-id"] == stream_run

    for index, (run_id, user_id) in enumerate(pairs):
        request = _request_for(f"parallel-{index}")
        _assert_wire_identity(request)
        assert request["headers"]["x-promptrail-run-id"] == run_id
        assert request["headers"]["x-promptrail-user-id"] == user_id

    llm_events = [
        item
        for item in _EVENTS
        if item["run_id"] == "run_acceptance_explicit"
        and item["span_id"] == explicit["headers"]["x-promptrail-span-id"]
        and item["type"] in {"llm.start", "llm.end"}
    ]
    assert {item["type"] for item in llm_events} == {"llm.start", "llm.end"}
    assert all(
        item["parent_span_id"] == explicit["headers"]["x-promptrail-parent-span-id"]
        for item in llm_events
    )

    for run_id in [
        "run_acceptance_explicit",
        "run_acceptance_offline",
        *(run_id for run_id, _ in pairs),
    ]:
        event_types = [item["type"] for item in _EVENTS if item["run_id"] == run_id]
        assert event_types.count("run.start") == 1
        assert event_types.count("run.end") == 1

    serialized = json.dumps(_EVENTS)
    assert all(item["schema_version"] == "1.0" for item in _EVENTS)
    assert "SECRET_ATTRIBUTE" not in serialized
    assert "RAW_PROPRIETARY_PROMPT" not in serialized
    assert "pr_test_acceptance" not in serialized

    print(
        json.dumps(
            {
                "concurrent_users": len(pairs),
                "events": len(_EVENTS),
                "gateway_requests": len(_GATEWAY_REQUESTS),
                "status": "accepted",
            }
        )
    )


if __name__ == "__main__":
    main()
