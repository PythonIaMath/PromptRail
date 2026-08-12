from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest

from promptrail import (
    PromptRail,
    RuntimeConfig,
    RuntimeContext,
    current_runtime_context,
    httpx_request_hook,
    run,
    submit_with_context,
    wrap_openai,
)
from promptrail.context import contextual_user, current_user_id, set_runtime_config
from promptrail.exporter import ExportWorker
from promptrail.exporter.http import ExportError
from promptrail.exporter.queue import EventQueue
from promptrail.sdk import _dispatch_otel_event, get_runtime_client
from promptrail.tracing import EventType

GATEWAY = "https://gateway.promptrail.test/v1"


@pytest.fixture(autouse=True)
def clean_runtime() -> None:
    PromptRail.shutdown(timeout=0)
    yield
    PromptRail.shutdown(timeout=0)
    set_runtime_config(None)


def test_init_shutdown_lifecycle_and_fail_open_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[Any] = []
    stopped: list[float | None] = []

    class FakeWorker:
        def __init__(self, config: RuntimeConfig) -> None:
            self.config = config

        def start(self) -> None:
            started.append(self.config)

        def shutdown(self, timeout: float | None = None) -> None:
            stopped.append(timeout)

        def enqueue(self, event: Any) -> bool:
            return True

    monkeypatch.setattr("promptrail.sdk.ExportWorker", FakeWorker)
    PromptRail.init(api_key="key", application="app", environment="ci", gateway_url=GATEWAY)
    client = get_runtime_client()
    assert client is not None
    assert started and started[0].application == "app"
    PromptRail.shutdown(timeout=0.01)
    assert stopped == [0.01]
    assert get_runtime_client() is None
    PromptRail.shutdown(timeout=0.01)

    class BrokenWorker(FakeWorker):
        def start(self) -> None:
            raise RuntimeError("offline")

    monkeypatch.setattr("promptrail.sdk.ExportWorker", BrokenWorker)
    PromptRail.init(api_key="key", gateway_url=GATEWAY)
    assert get_runtime_client() is not None
    assert PromptRail.event(EventType.OTHER_START, attributes={"ok": True}) is not None


def test_run_end_preserves_user_when_otel_enriches_lifecycle_context() -> None:
    captured: list[Any] = []

    class CapturingWorker:
        def enqueue(self, event: Any) -> bool:
            captured.append(event)
            return True

        def shutdown(self, timeout: float | None = None) -> None:
            pass

    PromptRail.init(export_enabled=False, enable_opentelemetry=False)
    client = get_runtime_client()
    assert client is not None
    client._worker = CapturingWorker()  # type: ignore[assignment]

    client.observe_run_start(RuntimeContext(run_id="run_enriched", user_id="user_123"))
    client.observe_run_start(RuntimeContext(run_id="run_enriched", trace_id="1" * 32))
    client.observe_run_end(RuntimeContext(run_id="run_enriched", trace_id="1" * 32))

    ended = next(event for event in captured if event.type == EventType.RUN_END)
    assert ended.user_id == "user_123"
    assert ended.trace_id == "1" * 32


def test_reusing_explicit_run_id_emits_balanced_lifecycles() -> None:
    captured: list[Any] = []

    class CapturingWorker:
        def enqueue(self, event: Any) -> bool:
            captured.append(event)
            return True

        def shutdown(self, timeout: float | None = None) -> None:
            pass

    PromptRail.init(export_enabled=False, enable_opentelemetry=False)
    client = get_runtime_client()
    assert client is not None
    client._worker = CapturingWorker()  # type: ignore[assignment]

    with run(run_id="run_reused"):
        pass
    with run(run_id="run_reused"):
        pass

    types = [event.type for event in captured if event.run_id == "run_reused"]
    assert types == [EventType.RUN_START, EventType.RUN_END] * 2


def test_reinitializing_with_otel_disabled_ignores_existing_processor_callbacks() -> None:
    captured: list[Any] = []

    class CapturingWorker:
        def enqueue(self, event: Any) -> bool:
            captured.append(event)
            return True

        def shutdown(self, timeout: float | None = None) -> None:
            pass

    PromptRail.init(export_enabled=False, enable_opentelemetry=False)
    client = get_runtime_client()
    assert client is not None
    client._worker = CapturingWorker()  # type: ignore[assignment]
    _dispatch_otel_event(
        {
            "phase": "start",
            "kind": "tool",
            "run_id": "run_disabled",
            "trace_id": "1" * 32,
            "span_id": "2" * 16,
        }
    )
    assert captured == []


def test_explicit_sync_async_run_contexts_and_user_precedence_fail_open() -> None:
    calls = 0

    def configured() -> str:
        nonlocal calls
        calls += 1
        return "configured"

    set_runtime_config(
        RuntimeConfig(user_id=configured, export_enabled=False, enable_opentelemetry=False)
    )
    assert current_user_id() == "configured"
    with contextual_user("contextual"):
        assert current_user_id() == "contextual"
        with run(user_id="explicit", run_id="run_sync") as ctx:
            assert ctx.user_id == "explicit"
            assert PromptRail.current_run_id() == "run_sync"
        assert current_user_id() == "contextual"

    async def check() -> None:
        async with run(run_id="run_async") as ctx:
            await asyncio.sleep(0)
            assert ctx.user_id == "configured"
            assert current_runtime_context().run_id == "run_async"

    asyncio.run(check())
    assert calls >= 2

    def bad() -> str:
        raise RuntimeError("resolver exploded")

    set_runtime_config(RuntimeConfig(user_id=bad, debug=True))
    assert current_user_id() is None


def test_header_injection_origin_bound_and_stale_private_replacement() -> None:
    PromptRail.init(
        application="app",
        environment="test",
        user_id="configured",
        gateway_url=GATEWAY,
        export_enabled=False,
        enable_opentelemetry=False,
    )
    with run(user_id="u1", run_id="run_current", trace_id="1" * 32, span_id="2" * 16):
        direct = PromptRail.inject_headers(
            {"x-promptrail-run-id": "stale", "authorization": "keep"},
            url="https://api.openai.com/v1/chat/completions",
        )
        assert direct == {"authorization": "keep"}
        injected = PromptRail.inject_headers(
            {
                "X-PromptRail-Run-Id": "stale",
                "x-promptrail-user-id": "old",
                "authorization": "keep",
                "traceparent": f"00-{'a' * 32}-{'b' * 16}-00",
                "tracestate": "vendor=stale",
            },
            url=f"{GATEWAY}/chat/completions",
        )
    assert injected["x-promptrail-run-id"] == "run_current"
    assert injected["x-promptrail-user-id"] == "u1"
    assert injected["x-promptrail-trace-id"] == "1" * 32
    assert injected["x-promptrail-span-id"] == "2" * 16
    assert injected["x-promptrail-application"] == "app"
    assert injected["x-promptrail-environment"] == "test"
    assert injected["traceparent"] == f"00-{'1' * 32}-{'2' * 16}-01"
    assert "tracestate" not in injected
    assert injected["authorization"] == "keep"
    assert "X-PromptRail-Run-Id" not in injected


def test_http_hook_injects_gateway_and_strips_private_provider_headers() -> None:
    PromptRail.init(
        gateway_url=GATEWAY,
        user_id="hook-user",
        export_enabled=False,
        enable_opentelemetry=False,
    )
    with run(run_id="run_hook"):
        gateway_request = SimpleNamespace(url=GATEWAY, headers={"authorization": "keep"})
        httpx_request_hook(gateway_request)
    assert gateway_request.headers["x-promptrail-run-id"] == "run_hook"

    provider_request = SimpleNamespace(
        url="https://api.openai.com/v1/chat/completions",
        headers={"x-promptrail-run-id": "stale", "authorization": "provider"},
    )
    httpx_request_hook(provider_request)
    assert provider_request.headers == {"authorization": "provider"}


def test_direct_header_fallback_run_is_request_local() -> None:
    PromptRail.init(
        gateway_url=GATEWAY,
        user_id="direct-user",
        export_enabled=False,
        enable_opentelemetry=False,
    )
    first = PromptRail.inject_headers(url=GATEWAY)
    second = PromptRail.inject_headers(url=GATEWAY)

    assert first["x-promptrail-run-id"] != second["x-promptrail-run-id"]
    assert first["x-promptrail-user-id"] == second["x-promptrail-user-id"] == "direct-user"
    assert current_runtime_context().run_id is None


class _SyncCreate:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return kwargs


class _AsyncCreate:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return kwargs


class _Client:
    def __init__(self, base_url: str, create: Any) -> None:
        self.base_url = base_url
        self.chat = type("Chat", (), {"completions": create})()


def test_openai_fake_sync_async_clients_receive_current_headers_and_provider_safety() -> None:
    PromptRail.init(
        gateway_url=GATEWAY, user_id="u", export_enabled=False, enable_opentelemetry=False
    )
    sync_create = _SyncCreate()
    client = wrap_openai(_Client(GATEWAY, sync_create))
    with run(run_id="run_sync"):
        result = client.chat.completions.create(
            model="gpt", extra_headers={"x-promptrail-run-id": "old"}
        )
    assert result["extra_headers"]["x-promptrail-run-id"] == "run_sync"
    assert result["extra_headers"]["x-promptrail-user-id"] == "u"

    provider = _Client("https://api.openai.com/v1", _SyncCreate())
    assert wrap_openai(provider) is provider

    async def check_async() -> None:
        async_create = _AsyncCreate()
        async_client = wrap_openai(_Client(GATEWAY, async_create))
        async with run(user_id="async-user", run_id="run_async"):
            got = await async_client.chat.completions.create(model="gpt-async")
        assert got["extra_headers"]["x-promptrail-run-id"] == "run_async"
        assert got["extra_headers"]["x-promptrail-user-id"] == "async-user"

    asyncio.run(check_async())


def test_openai_wrapper_rechecks_mutated_base_url_and_wraps_response_modifiers() -> None:
    PromptRail.init(
        gateway_url=GATEWAY, user_id="u", export_enabled=False, enable_opentelemetry=False
    )
    create = _SyncCreate()
    raw_create = _SyncCreate()
    streaming_create = _SyncCreate()
    create.with_raw_response = raw_create  # type: ignore[attr-defined]
    create.with_streaming_response = streaming_create  # type: ignore[attr-defined]
    original = _Client(GATEWAY, create)
    wrapped = wrap_openai(original)

    with run(run_id="run_modifiers"):
        wrapped.chat.completions.with_raw_response.create(model="gpt")
        wrapped.chat.completions.with_streaming_response.create(model="gpt")
    assert raw_create.calls[0]["extra_headers"]["x-promptrail-run-id"] == "run_modifiers"
    assert streaming_create.calls[0]["extra_headers"]["x-promptrail-run-id"] == "run_modifiers"

    wrapped.base_url = "https://api.openai.com/v1"
    with run(run_id="run_provider"):
        provider_result = wrapped.chat.completions.create(model="gpt")
    assert "extra_headers" not in provider_result


def test_openai_stream_keeps_implicit_run_open_and_records_late_error() -> None:
    captured: list[Any] = []

    class CapturingWorker:
        def enqueue(self, event: Any) -> bool:
            captured.append(event)
            return True

        def shutdown(self, timeout: float | None = None) -> None:
            pass

    class StreamingCreate:
        def create(self, **kwargs: Any):
            def chunks():
                yield current_runtime_context().run_id
                raise RuntimeError("stream failed")

            return chunks()

    PromptRail.init(gateway_url=GATEWAY, export_enabled=False, enable_opentelemetry=False)
    client = get_runtime_client()
    assert client is not None
    client._worker = CapturingWorker()  # type: ignore[assignment]
    wrapped = wrap_openai(_Client(GATEWAY, StreamingCreate()))

    stream = wrapped.chat.completions.create(model="gpt", stream=True)
    run_id = next(stream)
    assert run_id is not None
    assert current_runtime_context().run_id == run_id
    assert not any(event.type == EventType.RUN_END for event in captured)
    with pytest.raises(RuntimeError, match="stream failed"):
        next(stream)

    ended = next(event for event in captured if event.type == EventType.RUN_END)
    assert ended.run_id == run_id
    assert ended.status == "error"
    assert current_runtime_context().run_id is None


def test_openai_streaming_response_context_manager_keeps_run_open() -> None:
    class Manager:
        def __enter__(self) -> str | None:
            return current_runtime_context().run_id

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            pass

    class StreamingResponseCreate:
        def create(self, **kwargs: Any) -> Manager:
            return Manager()

    PromptRail.init(gateway_url=GATEWAY, export_enabled=False, enable_opentelemetry=False)
    create = _SyncCreate()
    create.with_streaming_response = StreamingResponseCreate()  # type: ignore[attr-defined]
    wrapped = wrap_openai(_Client(GATEWAY, create))

    with wrapped.chat.completions.with_streaming_response.create(model="gpt") as run_id:
        assert run_id is not None
        assert current_runtime_context().run_id == run_id
    assert current_runtime_context().run_id is None


def test_implicit_per_call_fallback_lifecycle_for_wrapped_openai() -> None:
    events: list[tuple[str, str]] = []

    class FakeWorker:
        def __init__(self, config: RuntimeConfig) -> None:
            pass

        def start(self) -> None:
            pass

        def shutdown(self, timeout: float | None = None) -> None:
            pass

        def enqueue(self, event: Any) -> bool:
            events.append((str(event.type), event.run_id))
            return True

    # Use manual client replacement to avoid patching source globally across tests.
    PromptRail.init(
        api_key="key", gateway_url=GATEWAY, export_enabled=False, enable_opentelemetry=False
    )
    client = get_runtime_client()
    assert client is not None
    client._worker = FakeWorker(client.config)  # type: ignore[assignment]
    create = _SyncCreate()
    wrapped = wrap_openai(_Client(GATEWAY, create))
    wrapped.chat.completions.create(model="gpt")
    run_ids = {run_id for _, run_id in events}
    assert len(run_ids) == 1
    assert {kind for kind, _ in events} >= {"run.start", "run.end"}
    assert create.calls[0]["extra_headers"]["x-promptrail-run-id"] in run_ids


def test_100_concurrent_asyncio_users_no_id_leakage() -> None:
    async def one(i: int) -> tuple[str | None, str | None]:
        async with run(user_id=f"u{i}", run_id=f"run_{i}"):
            await asyncio.sleep(0)
            return current_runtime_context().user_id, current_runtime_context().run_id

    async def main() -> list[tuple[str | None, str | None]]:
        return await asyncio.gather(*(one(i) for i in range(100)))

    results = asyncio.run(main())
    assert results == [(f"u{i}", f"run_{i}") for i in range(100)]
    assert current_runtime_context().run_id is None


def test_thread_pool_submit_with_context_propagates_and_plain_submit_does_not() -> None:
    with (
        ThreadPoolExecutor(max_workers=2) as executor,
        run(user_id="thread-u", run_id="run_thread"),
    ):
        copied = submit_with_context(executor, current_runtime_context).result(timeout=5)
        plain = executor.submit(current_runtime_context).result(timeout=5)
    assert copied.run_id == "run_thread"
    assert copied.user_id == "thread-u"
    assert plain.run_id is None


def test_malformed_attributes_privacy_and_exporter_fail_open() -> None:
    PromptRail.init(export_enabled=False, enable_opentelemetry=False, capture_content=False)
    with run(run_id="run_privacy"):
        event = PromptRail.event(
            EventType.LLM_START,
            attributes={
                "messages": ["private"],
                "prompt": "secret",
                "model": "gpt",
                "bad": object(),
                "nested": {"safe": True, "content": "hidden"},
            },
        )
    assert event is not None
    assert event.attributes == {"model": "gpt", "nested": {"safe": True}}

    class BrokenSender:
        def send(self, body: bytes, headers: dict[str, str]) -> None:
            raise ExportError("offline", retryable=False)

        def close(self) -> None:
            raise RuntimeError("close failed")

    worker = ExportWorker(
        RuntimeConfig(export_batch_size=1, max_export_retries=1), sender=BrokenSender()
    )
    assert worker.enqueue(event)
    worker.shutdown(timeout=0.01)
    assert worker.failed_exports >= 1

    queue = EventQueue(maxsize=1)
    assert queue.put_nowait("one") is True
    assert queue.put_nowait("two") is False
    assert queue.dropped_full == 1
