from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError

import pytest

from promptrail.config import RuntimeConfig
from promptrail.context import (
    RuntimeContext,
    RuntimeLifecycleCallbacks,
    contextual_user,
    current_run_id,
    current_runtime_context,
    current_user_id,
    run,
    set_lifecycle_callbacks,
    set_runtime_config,
    submit_with_context,
)
from promptrail.privacy import PrivacyPolicy
from promptrail.tracing import EventType, PromptRailEvent, event, inject_headers
from promptrail.utils import sanitize_message, secure_id


def test_runtime_context_is_frozen_slotted() -> None:
    context = RuntimeContext(user_id="u", run_id="r")
    assert not hasattr(context, "__dict__")
    with pytest.raises(FrozenInstanceError):
        context.run_id = "other"  # type: ignore[misc]


def test_run_context_precedence_and_lifecycle_callbacks() -> None:
    seen: list[tuple[str, str, str | None]] = []
    set_runtime_config(RuntimeConfig(user_id=lambda: "configured"))
    set_lifecycle_callbacks(
        RuntimeLifecycleCallbacks(
            on_run_start=lambda ctx: seen.append(("start", ctx.run_id or "", ctx.user_id)),
            on_run_end=lambda ctx, exc: seen.append(("end", ctx.run_id or "", ctx.user_id)),
        )
    )
    with contextual_user("contextual"):
        assert current_user_id() == "contextual"
        with run(user_id="explicit", run_id="run_test") as ctx:
            assert ctx.user_id == "explicit"
            assert current_run_id() == "run_test"
    assert seen == [("start", "run_test", "explicit"), ("end", "run_test", "explicit")]
    set_lifecycle_callbacks(RuntimeLifecycleCallbacks())
    set_runtime_config(None)


def test_async_run_context_manager() -> None:
    async def check() -> None:
        async with run(user_id="async-user", run_id="run_async"):
            await asyncio.sleep(0)
            assert current_runtime_context().user_id == "async-user"
            assert current_runtime_context().run_id == "run_async"

    asyncio.run(check())
    assert current_run_id() is None


def test_nested_run_scope_inherits_outer_run_by_default() -> None:
    seen: list[str] = []
    set_lifecycle_callbacks(
        RuntimeLifecycleCallbacks(
            on_run_start=lambda context: seen.append("start"),
            on_run_end=lambda context, error: seen.append("end"),
        )
    )
    try:
        with run(user_id="outer-user", run_id="run_outer"), run() as nested:
            assert nested.run_id == "run_outer"
            assert nested.user_id == "outer-user"
        assert seen == ["start", "end"]
    finally:
        set_lifecycle_callbacks()


def test_context_copy_to_thread_pool() -> None:
    with (
        ThreadPoolExecutor(max_workers=1) as executor,
        run(user_id="thread-user", run_id="run_thread"),
    ):
        future = submit_with_context(executor, current_runtime_context)
        assert future.result(timeout=5).run_id == "run_thread"


def test_prompt_rail_event_schema_and_event_type_strings() -> None:
    runtime_context = RuntimeContext(
        user_id="u",
        run_id="run_abc",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id="b7ad6b7169203331",
    )
    runtime_event = event(
        EventType.TOOL_END,
        context=runtime_context,
        name="tool",
        status="success",
        attributes={"duration_ms": 7, "prompt": "secret"},
    ).to_json_dict()
    assert runtime_event["schema_version"] == "1.0"
    assert runtime_event["event_id"].startswith("evt_")
    assert runtime_event["type"] == "tool.end"
    assert runtime_event["attributes"] == {"duration_ms": 7}
    manual_event = PromptRailEvent(type="workflow.stage", run_id="run_abc")
    assert manual_event.to_json_dict()["type"] == "workflow.stage"


def test_privacy_policy_bounds_and_no_arbitrary_object_serialization() -> None:
    class Sensitive:
        def __repr__(self) -> str:
            return "SHOULD_NOT_APPEAR"

    policy = PrivacyPolicy(mode="metadata_only", max_depth=3, max_items=2, max_string_length=4)
    sanitized = policy.sanitize(
        {
            "model": "abcdef",
            "messages": ["secret"],
            "api_key": "pr_live_SECRET",
            "authorization": "Bearer SECRET",
            "db.statement": "select * from proprietary_data",
            "request.url": "https://example.test/private?token=secret",
            "summary": "raw proprietary prompt text",
            "nested": {"ok": True, "object": Sensitive(), "more": 3},
            "extra": "dropped-by-item-limit",
        }
    )
    assert sanitized == {"model": "abcd", "nested": {"ok": True, "more": 3}}
    assert "SHOULD_NOT_APPEAR" not in str(sanitized)


def test_content_policy_allows_content_with_limits() -> None:
    policy = PrivacyPolicy(mode="content", max_string_length=5)
    assert policy.sanitize({"prompt": "123456", "items": ["abcdef"]}) == {
        "prompt": "12345",
        "items": ["abcde"],
    }


def test_secure_ids_ms_clocks_headers_and_secret_safe_logging() -> None:
    run_id = secure_id("run")
    assert run_id.startswith("run_")
    assert len({secure_id("evt") for _ in range(10)}) == 10
    headers = inject_headers(
        {"x-promptrail-run-id": "stale", "authorization": "Bearer keep"},
        context=RuntimeContext(run_id=run_id, user_id="u", trace_id="0" * 32, span_id="1" * 16),
        application="app",
        environment="prod",
        sdk_version="0.1.0",
    )
    assert headers["x-promptrail-run-id"] == run_id
    assert headers["traceparent"] == f"00-{'0' * 32}-{'1' * 16}-01"
    assert headers["authorization"] == "Bearer keep"
    assert "pr_live_secret" not in sanitize_message("api_key=pr_live_secret sk-secret")


def test_resolver_fail_open() -> None:
    def broken() -> str:
        raise RuntimeError("boom")

    set_runtime_config(RuntimeConfig(user_id=broken, debug=True))
    assert current_user_id() is None
    set_runtime_config(None)
