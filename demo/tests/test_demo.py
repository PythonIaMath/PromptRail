from __future__ import annotations

import base64
import curses
import json
import os
import threading
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from demo import credentials
from demo.backend import (
    DemoService,
    EventLedger,
    OpenRouterAllocator,
    OpenRouterRanker,
    _priced_usage_cost,
    _stream_json_number,
    apply_compaction,
    build_candidates,
    extract_completed_response,
    normalize_reasoning,
    openrouter_compatible_body,
    require_paid_controller_model,
    responses_to_messages,
    shortlist_candidates,
    should_reroute_provider_error,
    should_reroute_rate_limit,
    task_from_messages,
)
from demo.benchmark_control_plane import (
    MULTITURN_MARKER,
    assistant_text,
    multiturn_body,
)
from demo.run import (
    ANSWER_DIVIDER,
    big_text,
    build_model_catalog,
    codex_command,
    compact_command,
    comparison_exit_code,
    final_percentage_label,
    launch_codex,
    log_window,
    managed_call_limit_reached,
    navigate_logs,
    portable_codex_model,
    read_prompt,
    render_codex_event,
    render_codex_line,
    render_ledger_error,
    render_ledger_event,
    resolve_model_command,
    savings_percentage,
    savings_seconds_remaining,
)

from promptrail import BudgetError, CallIntent, RunStatus


def test_multiturn_benchmark_carries_real_history_and_extracts_response_text() -> None:
    history = [
        {"role": "user", "content": f"Remember {MULTITURN_MARKER}"},
        {"role": "assistant", "content": "Stored."},
    ]
    body = multiturn_body(history, "What was the marker?")

    assert body["input"][:-1] == history
    assert body["input"][-1] == {"role": "user", "content": "What was the marker?"}
    assert (
        assistant_text(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": MULTITURN_MARKER}],
                    }
                ]
            }
        )
        == MULTITURN_MARKER
    )


def test_modal_credential_bridge_keeps_token_in_memory(monkeypatch) -> None:
    monkeypatch.delenv("LEROUTER_INTERNAL_SERVICE_TOKEN", raising=False)
    token = "test-internal-token"
    encoded = base64.b64encode(token.encode()).decode()
    monkeypatch.setattr(credentials.shutil, "which", lambda _: "/usr/local/bin/modal")
    monkeypatch.setattr(
        credentials.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=f"PROMPTRAIL_MODAL_TOKEN_B64={encoded}\n",
        ),
    )

    credentials.ensure_internal_service_token()

    assert os.environ["LEROUTER_INTERNAL_SERVICE_TOKEN"] == token


def test_controller_model_must_be_a_paid_openrouter_slug() -> None:
    assert require_paid_controller_model("google/gemma-3-12b-it") == ("google/gemma-3-12b-it")
    try:
        require_paid_controller_model("google/gemma-3-12b-it:free")
    except RuntimeError as error:
        assert "paid OpenRouter model slug" in str(error)
    else:
        raise AssertionError("free controller slug was accepted")


def test_extracts_openrouter_completed_usage() -> None:
    raw = (
        b'data: {"type":"response.completed","response":{"model":"m","usage":'
        b'{"input_tokens":10,"output_tokens":2,"cost":0.01}}}\n\n'
    )
    response = extract_completed_response(raw, "text/event-stream")
    assert response["usage"]["cost"] == 0.01


def test_ledger_accumulates_only_real_usage(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    ledger.record(
        "baseline",
        "usage",
        input_tokens=10,
        output_tokens=2,
        cached_tokens=4,
        cost=0.02,
    )
    event = json.loads((tmp_path / "events.jsonl").read_text())
    assert event["totals"] == {"cost": 0.02, "tokens": 12, "cached": 4, "calls": 1}


def test_compaction_is_applied_to_tool_output_without_rebuilding_protocol() -> None:
    body = {
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "go"}]},
            {"type": "function_call_output", "call_id": "c1", "output": "large result"},
        ]
    }
    messages, indices = responses_to_messages(body)
    compacted = (messages[0], {**messages[1], "content": "short result"})
    result = apply_compaction(body, messages, compacted, indices)
    assert result["input"][1]["output"] == "short result"
    assert result["input"][0] == body["input"][0]


def test_codex_command_uses_real_responses_provider(tmp_path: Path) -> None:
    catalog = tmp_path / "models.json"
    command = codex_command(
        "baseline",
        8765,
        "openai/model",
        tmp_path,
        "fix it",
        catalog,
    )
    joined = " ".join(command)
    assert "codex exec" in joined
    assert 'wire_api = "responses"' in joined
    assert "http://127.0.0.1:8765" in joined
    assert str(catalog) in joined
    assert "--ephemeral" not in command

    resumed = codex_command(
        "baseline",
        8765,
        "openai/model",
        tmp_path,
        "continue",
        catalog,
        "thread-123",
    )
    assert resumed[:3] == ["codex", "exec", "resume"]
    assert "thread-123" in resumed
    assert "--cd" not in resumed
    assert "--json" in resumed
    assert "--ignore-user-config" not in resumed
    assert 'model="openai/model"' in resumed
    assert "mcp_servers={}" in resumed


def test_codex_process_is_bound_to_its_lane_workspace(tmp_path: Path, monkeypatch: object) -> None:
    captured = {}

    def fake_popen(command: list[str], **kwargs: object) -> object:
        captured["command"] = command
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("demo.run.subprocess.Popen", fake_popen)
    launch_codex(["codex", "exec"], tmp_path, {"DEMO": "1"})
    assert captured["cwd"] == tmp_path


def test_model_command_lists_and_selects_comparison_baselines() -> None:
    models = (
        {"model": "openai/gpt-5.6-terra", "label": "GPT-5.6 Terra", "aliases": ["terra"]},
        {"model": "openai/gpt-5.6-luna", "label": "GPT-5.6 Luna", "aliases": ["luna"]},
        {"model": "moonshotai/kimi-k3", "label": "Kimi K3", "aliases": ["kimi", "k3"]},
    )
    handled, selected, lines = resolve_model_command("/model", models, "openai/gpt-5.6-terra")
    assert handled is True
    assert selected == "openai/gpt-5.6-terra"
    assert any("/model kimi" in line for line in lines)

    handled, selected, lines = resolve_model_command("/model kimi", models, "openai/gpt-5.6-terra")
    assert handled is True
    assert selected == "moonshotai/kimi-k3"
    assert lines[-1].startswith("RESET")

    handled, selected, lines = resolve_model_command(
        "what is this codebase", models, "openai/gpt-5.6-terra"
    )
    assert handled is False
    assert selected == "openai/gpt-5.6-terra"
    assert lines == []


def test_model_catalog_contains_every_comparison_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = {
        "models": [
            {"slug": "gpt-5.6-sol", "display_name": "Sol"},
            {"slug": "gpt-5.6-terra", "display_name": "Terra"},
            {"slug": "gpt-5.6-luna", "display_name": "Luna"},
        ]
    }

    def fake_run(*args: object, **kwargs: object) -> object:
        del args, kwargs
        return SimpleNamespace(stdout=json.dumps(bundled))

    monkeypatch.setattr("demo.run.subprocess.run", fake_run)
    catalog = tmp_path / "models.json"
    build_model_catalog(
        catalog,
        (
            "openai/gpt-5.6-terra",
            "openai/gpt-5.6-luna",
            "moonshotai/kimi-k3",
        ),
    )
    entries = json.loads(catalog.read_text())["models"]
    assert [entry["slug"] for entry in entries] == [
        "openai/gpt-5.6-terra",
        "openai/gpt-5.6-luna",
        "moonshotai/kimi-k3",
    ]


def test_terminal_events_are_short_and_scannable() -> None:
    assert compact_command("/bin/zsh -lc 'rg --files src tests'") == "rg --files src tests"
    assert render_codex_event(
        {
            "type": "item.started",
            "item": {
                "type": "command_execution",
                "command": "/bin/zsh -lc 'rg --files src tests'",
                "status": "in_progress",
            },
        }
    ) == ["RUN     rg --files src tests"]
    assert render_codex_event(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/bin/zsh -lc 'rg --files src tests'",
                "status": "completed",
                "exit_code": 0,
            },
        }
    ) == ["DONE    rg --files src tests | exit 0"]
    answer = render_codex_event(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "**PromptRail** uses [routing](https://example.test).",
            },
        }
    )
    assert answer == ["", "UPDATE", "PromptRail uses routing.", ""]


def test_codex_line_parser_treats_non_object_json_as_text() -> None:
    assert render_codex_line('"plain Codex output"\n') == (
        ["plain Codex output"],
        None,
    )
    assert render_codex_line('["one", "two"]\n') == (
        ['["one", "two"]'],
        None,
    )


def test_log_window_scrolls_wrapped_output_and_clamps_to_oldest_content() -> None:
    lines = deque(("zero", "one two three", "four"))
    assert log_window(lines, width=7, height=2, scroll_offset=0) == (
        ["three", "four"],
        0,
    )
    assert log_window(lines, width=7, height=2, scroll_offset=2) == (
        ["zero", "one two"],
        2,
    )
    assert log_window(lines, width=7, height=2, scroll_offset=999) == (
        ["zero", "one two"],
        2,
    )


def test_answer_divider_fills_the_current_pane_width() -> None:
    assert log_window(
        deque((ANSWER_DIVIDER, "ANSWER")),
        width=24,
        height=2,
        scroll_offset=0,
    ) == (["—" * 24, "ANSWER"], 0)


def test_log_navigation_tracks_each_pane_independently() -> None:
    offsets = {"baseline": 0, "managed": 0}
    handled, lane = navigate_logs(curses.KEY_PPAGE, offsets, "managed", 12)
    assert handled is True
    assert lane == "managed"
    assert offsets == {"baseline": 0, "managed": 12}

    handled, lane = navigate_logs("\t", offsets, lane, 12)
    assert handled is True
    assert lane == "baseline"
    handled, lane = navigate_logs(curses.KEY_UP, offsets, lane, 12)
    assert offsets == {"baseline": 1, "managed": 12}
    handled, lane = navigate_logs(curses.KEY_END, offsets, lane, 12)
    assert offsets["baseline"] == 0


def test_prompt_editor_keeps_scrollback_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeScreen:
        def __init__(self) -> None:
            self.keys = iter((curses.KEY_PPAGE, "\t", "h", "i", "\n"))

        def nodelay(self, _: bool) -> None:
            pass

        def getmaxyx(self) -> tuple[int, int]:
            return 24, 100

        def move(self, _: int, __: int) -> None:
            pass

        def clrtoeol(self) -> None:
            pass

        def addnstr(self, *args: object) -> None:
            del args

        def refresh(self) -> None:
            pass

        def get_wch(self) -> int | str:
            return next(self.keys)

    monkeypatch.setattr("demo.run.draw", lambda *args, **kwargs: None)
    monkeypatch.setattr("demo.run.curses.curs_set", lambda *_: None)
    monkeypatch.setattr("demo.run.curses.noecho", lambda: None)
    offsets = {"baseline": 0, "managed": 0}

    prompt, lane = read_prompt(
        FakeScreen(),
        {"baseline": deque(), "managed": deque()},
        {},
        deque(),
        2,
        offsets,
        "managed",
    )

    assert prompt == "hi"
    assert lane == "baseline"
    assert offsets == {"baseline": 0, "managed": 16}


def test_ledger_events_explain_route_failover_and_real_usage() -> None:
    assert render_ledger_event(
        {
            "event": "decision",
            "model": "moonshotai/kimi-k2.7-code",
            "compacted_tokens": 1200,
            "cached_tokens": 8000,
        }
    ) == (
        "ROUTE   moonshotai/kimi-k2.7-code | compact 1,200 | cache 8,000 | context 0 | output 0 tok"
    )
    assert (
        render_ledger_event(
            {
                "event": "provider_error_reroute",
                "status": 429,
                "previous_model": "model/a",
                "model": "model/b",
            }
        )
        == "FAILOVER  HTTP 429 | model/a -> model/b"
    )
    assert (
        render_ledger_event(
            {
                "event": "usage",
                "purpose": "agent",
                "model": "model/b",
                "input_tokens": 1200,
                "output_tokens": 80,
                "cached_tokens": 900,
                "cost": 0.000321,
            }
        )
        == "USAGE   model/b | in 1,200 | out 80 | cache 900 | $0.000321 | TTFT 0 ms"
    )


def test_filters_only_openai_hosted_tools() -> None:
    body = {
        "tools": [
            {"type": "function", "name": "shell"},
            {"type": "function", "name": "view_image"},
            {"type": "namespace", "name": "apps"},
            {"type": "web_search"},
        ]
    }
    result = openrouter_compatible_body(body)
    assert result["tools"] == [{"type": "function", "name": "shell"}]
    assert "never call a tool named final" in result["instructions"]
    assert len(body["tools"]) == 4


def test_non_openai_agent_models_use_deterministic_sampling() -> None:
    body = {"reasoning": {"effort": "medium"}}
    normalize_reasoning(body, "nex-agi/nex-n2-mini")
    assert "reasoning" not in body
    assert body["temperature"] == 0


def test_task_stays_on_latest_user_goal_across_tool_continuations() -> None:
    messages = (
        {"role": "user", "content": "what is this codebase"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "content": "thousands of lines of shell output"},
    )
    assert task_from_messages(messages) == "what is this codebase"


def test_baseline_uses_openai_and_managed_uses_openrouter_without_output_ceilings() -> None:
    service = DemoService.__new__(DemoService)
    service.openai_key = "openai-key"
    service.openrouter_key = "openrouter-key"
    service.baseline_model = "openai/gpt-5.6-sol"

    host, path, key, payload, provider = service.upstream(
        "baseline", {"model": "openai/gpt-5.6-terra", "stream": True}
    )
    assert (host, path, key, provider) == (
        "api.openai.com",
        "/v1/responses",
        "openai-key",
        "openai",
    )
    assert payload["model"] == "gpt-5.6-terra"
    assert "max_output_tokens" not in payload

    host, path, key, payload, provider = service.upstream(
        "baseline", {"model": "moonshotai/kimi-k3", "stream": True}
    )
    assert (host, path, key, provider) == (
        "openrouter.ai",
        "/api/v1/responses",
        "openrouter-key",
        "openrouter",
    )
    assert payload["model"] == "moonshotai/kimi-k3"
    assert payload["provider"] == {"sort": "price", "allow_fallbacks": True}

    host, path, key, explicit_payload, provider = service.upstream(
        "managed",
        {
            "model": "openai/gpt-5.6-sol",
            "max_output_tokens": 128_000,
            "max_tokens": 128_000,
        },
    )
    assert (host, path, key, provider) == (
        "openrouter.ai",
        "/api/v1/responses",
        "openrouter-key",
        "openrouter",
    )
    assert "max_output_tokens" not in explicit_payload
    assert "max_tokens" not in explicit_payload


def test_baseline_prepare_uses_requested_comparison_model(tmp_path: Path) -> None:
    service = DemoService.__new__(DemoService)
    service.ledger = EventLedger(tmp_path / "events.jsonl")
    service.baseline_model = "openai/gpt-5.6-sol"
    service.baseline_models = (
        "openai/gpt-5.6-sol",
        "openai/gpt-5.6-terra",
        "openai/gpt-5.6-luna",
        "moonshotai/kimi-k3",
    )
    prepared, reservation = service.prepare(
        "baseline",
        {
            "model": "moonshotai/kimi-k3",
            "input": "hello",
            "reasoning": {"effort": "medium"},
        },
    )
    assert reservation is None
    assert prepared["model"] == "moonshotai/kimi-k3"
    assert prepared["temperature"] == 0
    assert "reasoning" not in prepared

    with pytest.raises(ValueError, match="not allowed"):
        service.prepare("baseline", {"model": "unknown/model", "input": "hello"})


def test_failed_managed_run_is_rejected_before_ranker_prefetch(tmp_path: Path) -> None:
    service = DemoService.__new__(DemoService)
    service.ledger = EventLedger(tmp_path / "events.jsonl")
    calls: list[str] = []
    service.ranker = SimpleNamespace(prefetch=lambda **_: calls.append("prefetch"))
    controller = SimpleNamespace(snapshot=lambda _: SimpleNamespace(status=RunStatus.FAILED))
    service.managed = SimpleNamespace(
        gateway=SimpleNamespace(controller=controller),
        run_id="run_failed",
        session_id="session",
        candidates=(),
        predicted_output_tokens=2_048,
    )

    with pytest.raises(BudgetError, match="agent run is failed"):
        service.prepare("managed", {"input": "continue", "tools": []})

    assert calls == []


def test_baseline_cost_uses_provider_tokens_and_catalog_prices() -> None:
    route = build_candidates(
        (
            {
                "model": "openai/gpt-5.6-sol",
                "model_cost": {
                    "input_usd_per_million": 0.2,
                    "output_usd_per_million": 0.5,
                    "input_cache_read_usd_per_million": 0.1,
                },
            },
        )
    )[0].routes[0]
    assert _priced_usage_cost(100, 10, 20, route) == pytest.approx(0.000023)


def test_normalizes_reasoning_for_openrouter_models() -> None:
    openai_body = {"reasoning": {"effort": "none", "summary": "auto", "context": "all_turns"}}
    normalize_reasoning(openai_body, "openai/gpt-5.6-sol")
    assert openai_body["reasoning"]["effort"] == "medium"
    assert openai_body["reasoning"]["context"] == "auto"
    assert openai_body["text"]["verbosity"] == "medium"
    gemini_body = {"reasoning": {"effort": "low"}}
    normalize_reasoning(gemini_body, "google/gemini-3-flash-preview")
    assert "reasoning" not in gemini_body


def test_settlement_diagnostics_are_not_rendered_in_agent_panes() -> None:
    assert (
        render_ledger_error(
            {
                "event": "settlement_error",
                "message": "end-to-end call latency exceeded allocation",
            }
        )
        is None
    )
    assert render_ledger_error({"event": "error", "message": "provider failed"}) == (
        "ERROR   proxy | provider failed"
    )


def test_savings_percentage_uses_actual_lane_totals() -> None:
    assert round(savings_percentage(0.10, 0.025), 6) == 75.0
    assert round(savings_percentage(0.10, 0.12), 6) == -20.0
    assert savings_percentage(0.0, 0.0) is None


def test_big_percentage_text_uses_figlet_big_font() -> None:
    banner = big_text("75.0%")
    assert len(banner) == 8
    assert all(line for line in banner)


def test_final_percentage_always_has_an_exact_plain_text_label() -> None:
    assert final_percentage_label(38.3) == "38.3% SAVED"
    assert final_percentage_label(-12.5) == "12.5% ADDITIONAL COST"


def test_savings_countdown_returns_after_five_seconds() -> None:
    assert savings_seconds_remaining(15.0, 10.0) == 5
    assert savings_seconds_remaining(15.0, 14.1) == 1
    assert savings_seconds_remaining(15.0, 15.0) == 0


def test_managed_loop_guard_stops_at_configured_agent_call_limit() -> None:
    assert not managed_call_limit_reached(1_000_000, 0)
    assert not managed_call_limit_reached(31, 32)
    assert managed_call_limit_reached(32, 32)
    assert managed_call_limit_reached(100, 32)


def test_aborted_or_failed_comparison_returns_failure() -> None:
    assert comparison_exit_code([0, 0], None) == 0
    assert comparison_exit_code([0, -15], None) == 1
    assert comparison_exit_code([0, 0], "loop guard triggered") == 1


def test_demo_catalog_uses_the_mongodb_tool_universe() -> None:
    config = json.loads((Path(__file__).parents[1] / "config.json").read_text())
    assert config["max_managed_agent_calls"] == 0
    assert config["savings_display_seconds"] == 5
    assert config["mongodb_database"] == "lerouter"
    assert config["mongodb_collection"] == "model_profiles"
    assert "models" not in config


def test_mongodb_profiles_become_openrouter_candidates() -> None:
    profiles = (
        {
            "model": "open/model-a",
            "model_cost": {
                "input_usd_per_million": 0.2,
                "output_usd_per_million": 1.0,
            },
            "model_latency": 800,
            "model_context_window": 128_000,
            "forces": ["Coding", "Tool Use"],
            "quality_calibration": {
                "routes": {
                    "coding_debugging": {
                        "measured": True,
                        "mean_quality_score": 0.82,
                    }
                }
            },
        },
        {
            "model": "open/model-b",
            "model_cost": {
                "input_usd_per_million": 0.1,
                "output_usd_per_million": 0.4,
            },
            "model_latency": "OpenRouter latency unavailable",
            "model_context_window": 64_000,
            "forces": ["Writing", "Tool Use"],
        },
    )
    candidates = build_candidates(profiles)
    assert len(candidates) == 2
    assert {candidate.routes[0].provider for candidate in candidates} == {"openrouter"}
    assert candidates[0].quality == 0.82
    assert candidates[1].quality == 0.0
    assert candidates[1].routes[0].p95_total_latency_ms == 5_000
    assert shortlist_candidates("debug this code", candidates, 1)[0].model_id == ("open/model-a")


def test_free_models_are_disabled_even_when_a_paid_sibling_exists() -> None:
    profiles = (
        {
            "model": "google/gemma-4-31b-it:free",
            "model_cost": {
                "input_usd_per_million": 0,
                "output_usd_per_million": 0,
            },
            "model_latency": 900,
            "model_context_window": 32_000,
            "forces": ["Coding"],
        },
        {
            "model": "google/gemma-4-31b-it",
            "model_cost": {
                "input_usd_per_million": 0.14,
                "output_usd_per_million": 0.4,
            },
            "model_latency": 700,
            "model_context_window": 128_000,
            "forces": ["Coding", "Tool Use"],
        },
        {
            "model": "free-only/model:free",
            "model_cost": {
                "input_usd_per_million": 0,
                "output_usd_per_million": 0,
            },
            "forces": ["Coding"],
        },
        {
            "model": "openrouter/free",
            "model_cost": {
                "input_usd_per_million": 0,
                "output_usd_per_million": 0,
            },
            "forces": ["Coding"],
        },
    )
    candidates = build_candidates(profiles)
    assert len(candidates) == 1
    paid = candidates[0]
    assert paid.model_id == "google/gemma-4-31b-it"
    route = paid.routes[0]
    assert route.native_model_id == paid.model_id
    assert route.input_price_per_million == 0.14
    assert route.output_price_per_million == 0.4
    assert paid.context_window_tokens == 128_000
    assert all(not item.model_id.endswith(":free") for item in candidates)


def test_rate_limited_models_enter_and_leave_cooldown(tmp_path: Path) -> None:
    ranker = OpenRouterRanker(
        "key",
        "google/gemma-3-12b-it",
        EventLedger(tmp_path / "events.jsonl"),
        24,
        1,
        15,
        2,
    )
    ranker.mark_rate_limited("paid/model", retry_after_seconds=60)
    assert ranker.rate_limited_models() == frozenset({"paid/model"})
    ranker._rate_limited_until["paid/model"] = 0
    assert ranker.rate_limited_models() == frozenset()
    ranker.close()


def test_ranker_retries_transport_timeout_without_repeating_allocator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    ranker = OpenRouterRanker(
        "key",
        "google/gemma-3-12b-it",
        ledger,
        24,
        1,
        15,
        2,
    )
    candidates = build_candidates(
        (
            {
                "model": "paid/model",
                "model_cost": {
                    "input_usd_per_million": 0.2,
                    "output_usd_per_million": 0.5,
                },
                "forces": ["Coding", "Tool Use"],
            },
        )
    )
    calls: list[tuple[dict[str, object], float]] = []

    def fake_stream_json_number(
        client: object,
        payload: dict[str, object],
        timeout: float,
    ) -> tuple[dict[str, object], float, float, str]:
        del client
        calls.append((payload, timeout))
        if len(calls) == 1:
            raise TimeoutError("read stalled")
        return (
            {
                "model": "google/gemma-3-12b-it",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.001},
                "choices": [{"message": {"content": "0.9"}}],
            },
            120.0,
            150.0,
            "HTTP/2",
        )

    monkeypatch.setattr("demo.backend._stream_json_number", fake_stream_json_number)
    scores = ranker.rank(
        intent=CallIntent(
            session_id="session",
            task="fix the code",
            messages=({"role": "user", "content": "fix the code"},),
            predicted_output_tokens=128,
        ),
        candidates=candidates,
        timeout_ms=500,
    )

    assert scores == {"paid/model": 0.9}
    assert len(calls) == 2
    assert all(timeout == 15 for _, timeout in calls)
    assert calls[0][0]["provider"] == {
        "sort": "latency",
        "allow_fallbacks": True,
    }
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "ranker_shortlist",
        "control_retry",
        "usage",
    ]
    assert events[-1]["ttft_ms"] == 120.0
    assert events[-1]["http_version"] == "HTTP/2"
    ranker.close()


def test_compact_allocator_uses_microdollars_and_latency_routing(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "google/gemma-3-12b-it",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
                "choices": [
                    {
                        "message": {
                            "content": '{"cost_microdollars":125,"latency_ms":123000,'
                            '"input_cost_fraction":0.5,"required_context_tokens":100,'
                            '"importance_overrides":[]}'
                        }
                    }
                ],
            },
        )

    option = SimpleNamespace(
        context_fits=True,
        required_capabilities_supported=True,
        exact_cache_reuse=False,
        quality=0.8,
        cheapest_predicted_cost_usd=0.0001,
        cheapest_input_cost_fraction=0.6,
        fastest_predicted_latency_ms=1000,
        model_id="paid/model",
        model_dump=lambda **_: {"model_id": "paid/model"},
    )
    request = SimpleNamespace(
        candidate_options=(option,),
        analytics_insight="save money",
        task="fix code",
        input_tokens=100,
        predicted_output_tokens=50,
        cacheable_tokens=0,
        compactable_tokens=0,
    )
    with httpx.Client(
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        allocator = OpenRouterAllocator(
            "key",
            "google/gemma-3-12b-it",
            EventLedger(tmp_path / "events.jsonl"),
            12,
            client,
        )
        decision = allocator.allocate(request)

    assert decision.cost_usd == 0.000125
    assert decision.latency_ms == 123_000
    assert decision.input_cost_fraction == 0.5
    assert captured["provider"] == {"sort": "latency", "allow_fallbacks": True}
    allocator_input = json.loads(captured["messages"][1]["content"])
    assert allocator_input["overrideable_block_ids"] == []
    assert allocator_input["candidate_fields"][0] == "model_id"


def test_allocator_repairs_invalid_input_fraction_from_catalog_economics(
    tmp_path: Path,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "google/gemma-3-12b-it",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
                "choices": [
                    {
                        "message": {
                            "content": '{"cost_microdollars":125,"latency_ms":123000,'
                            '"input_cost_fraction":0.0,"required_context_tokens":100,'
                            '"importance_overrides":[]}'
                        }
                    }
                ],
            },
        )

    option = SimpleNamespace(
        context_fits=True,
        required_capabilities_supported=True,
        exact_cache_reuse=False,
        quality=0.8,
        cheapest_predicted_cost_usd=0.0001,
        cheapest_input_cost_fraction=0.72,
        fastest_predicted_latency_ms=1000,
        model_id="paid/model",
        model_dump=lambda **_: {"model_id": "paid/model"},
    )
    request = SimpleNamespace(
        candidate_options=(option,),
        analytics_insight="save money",
        task="fix code",
        input_tokens=100,
        predicted_output_tokens=50,
        cacheable_tokens=0,
        compactable_tokens=0,
    )
    ledger_path = tmp_path / "events.jsonl"
    with httpx.Client(
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        allocator = OpenRouterAllocator(
            "key",
            "google/gemma-3-12b-it",
            EventLedger(ledger_path),
            12,
            client,
        )
        decision = allocator.allocate(request)

    assert decision.input_cost_fraction == 0.72
    assert "repaired" in decision.reason
    events = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    allocation = next(event for event in events if event["event"] == "allocation")
    assert allocation["raw_input_cost_fraction"] == 0.0
    assert allocation["effective_input_cost_fraction"] == 0.72
    assert allocation["repaired"] is True


def test_allocator_repairs_underbudget_and_cascades_to_cheapest_model(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "google/gemma-3-12b-it",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
                "choices": [
                    {
                        "message": {
                            "content": '{"cost_microdollars":1,"latency_ms":1,'
                            '"input_cost_fraction":0.5,"required_context_tokens":100,'
                            '"importance_overrides":[]}'
                        }
                    }
                ],
            },
        )

    def option(model_id: str, cost: float, latency: int, quality: float) -> object:
        values = {
            "model_id": model_id,
            "context_fits": True,
            "required_capabilities_supported": True,
            "exact_cache_reuse": False,
            "quality": quality,
            "cheapest_predicted_cost_usd": cost,
            "cheapest_input_cost_fraction": 0.5,
            "fastest_predicted_latency_ms": latency,
        }
        return SimpleNamespace(**values, model_dump=lambda **_: values)

    request = SimpleNamespace(
        candidate_options=(
            option("quality/model", 0.02, 121_000, 0.99),
            option("cheap/model", 0.00006197, 122_000, 0.4),
        ),
        analytics_insight="save money",
        task="fix code",
        input_tokens=100,
        predicted_output_tokens=50,
        cacheable_tokens=0,
        compactable_tokens=0,
    )
    ledger_path = tmp_path / "events.jsonl"
    with httpx.Client(
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        allocator = OpenRouterAllocator(
            "key",
            "google/gemma-3-12b-it",
            EventLedger(ledger_path),
            1,
            client,
        )
        decision = allocator.allocate(request)

    user_payload = json.loads(captured["messages"][1]["content"])
    model_index = user_payload["candidate_fields"].index("model_id")
    assert [item[model_index] for item in user_payload["candidates"]] == ["cheap/model"]
    assert user_payload["minimum_admissible"] == {
        "model_id": "cheap/model",
        "cost_microdollars": 62,
        "latency_ms": 122_000,
    }
    assert decision.cost_usd == pytest.approx(0.00006197)
    assert decision.latency_ms == 122_000
    assert "repaired" in decision.reason
    events = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    allocation = next(event for event in events if event["event"] == "allocation")
    assert allocation["repaired"] is True
    assert allocation["fallback_model"] == "cheap/model"


def test_allocator_excludes_rate_limited_model_before_budgeting(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "google/gemma-3-12b-it",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
                "choices": [
                    {
                        "message": {
                            "content": '{"cost_microdollars":200,"latency_ms":2000,'
                            '"input_cost_fraction":0.5,"required_context_tokens":100,'
                            '"importance_overrides":[]}'
                        }
                    }
                ],
            },
        )

    def option(model_id: str, cost: float, quality: float = 0.8) -> object:
        values = {
            "model_id": model_id,
            "context_fits": True,
            "required_capabilities_supported": True,
            "exact_cache_reuse": False,
            "quality": quality,
            "cheapest_predicted_cost_usd": cost,
            "cheapest_input_cost_fraction": 0.5,
            "fastest_predicted_latency_ms": 1000,
        }
        return SimpleNamespace(**values, model_dump=lambda **_: values)

    request = SimpleNamespace(
        candidate_options=(
            option("limited/model", 0.0001),
            option("fallback/model", 0.0002),
            option("cheap-but-unqualified/model", 0.00001, quality=0.0),
        ),
        analytics_insight="save money",
        task="fix code",
        task_rules=(
            SimpleNamespace(
                minimum_quality=0.1,
                match_terms=("code",),
            ),
        ),
        input_tokens=100,
        predicted_output_tokens=50,
        cacheable_tokens=0,
        compactable_tokens=0,
    )
    ledger_path = tmp_path / "events.jsonl"
    with httpx.Client(
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        allocator = OpenRouterAllocator(
            "key",
            "google/gemma-3-12b-it",
            EventLedger(ledger_path),
            1,
            client,
            lambda: frozenset({"limited/model"}),
        )
        decision = allocator.allocate(request)

    user_payload = json.loads(captured["messages"][1]["content"])
    model_index = user_payload["candidate_fields"].index("model_id")
    assert [item[model_index] for item in user_payload["candidates"]] == ["fallback/model"]
    assert user_payload["minimum_admissible"]["model_id"] == "fallback/model"
    assert decision.cost_usd == pytest.approx(0.0002)
    shortlist = json.loads(ledger_path.read_text().splitlines()[0])
    assert shortlist["rate_limited_models"] == ["limited/model"]
    assert shortlist["cheapest_fallback_model"] == "fallback/model"


def test_ranker_prefetch_starts_before_router_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()

    def fake_stream_json_number(
        client: object,
        payload: dict[str, object],
        timeout: float,
    ) -> tuple[dict[str, object], float, float, str]:
        del client, payload, timeout
        started.set()
        assert release.wait(timeout=1)
        return (
            {
                "model": "google/gemma-3-12b-it",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.001},
                "choices": [{"message": {"content": "0.7"}}],
            },
            100.0,
            120.0,
            "HTTP/2",
        )

    monkeypatch.setattr("demo.backend._stream_json_number", fake_stream_json_number)
    ranker = OpenRouterRanker(
        "key",
        "google/gemma-3-12b-it",
        EventLedger(tmp_path / "events.jsonl"),
        1,
        1,
        15,
        2,
    )
    candidates = build_candidates(
        (
            {
                "model": "paid/model",
                "model_cost": {
                    "input_usd_per_million": 0.2,
                    "output_usd_per_million": 0.5,
                },
            },
        )
    )
    intent = CallIntent(
        session_id="session",
        task="fix code",
        messages=({"role": "user", "content": "fix code"},),
    )
    ranker.prefetch(intent=intent, candidates=candidates)
    assert started.wait(timeout=1)
    release.set()
    assert ranker.rank(intent=intent, candidates=candidates, timeout_ms=500) == {"paid/model": 0.7}
    ranker.close()


def test_ranker_scores_twelve_single_model_requests_in_parallel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = EventLedger(tmp_path / "events.jsonl")
    ranker = OpenRouterRanker(
        "key",
        "google/gemma-3-12b-it",
        ledger,
        12,
        1,
        15,
        2,
    )
    candidates = build_candidates(
        tuple(
            {
                "model": f"paid/model-{index:02d}",
                "model_cost": {
                    "input_usd_per_million": 0.2,
                    "output_usd_per_million": 0.5,
                },
                "forces": ["Coding", "Tool Use"],
            }
            for index in range(12)
        )
    )
    barrier = threading.Barrier(12)
    calls: list[str] = []
    lock = threading.Lock()

    def fake_stream_json_number(
        client: object,
        payload: dict[str, object],
        timeout: float,
    ) -> tuple[dict[str, object], float, float, str]:
        del client, timeout
        schema = payload["response_format"]["json_schema"]["schema"]
        user_payload = json.loads(payload["messages"][1]["content"])
        model_id = user_payload["candidate"]["id"]
        with lock:
            calls.append(model_id)
        barrier.wait(timeout=1)
        assert schema == {"type": "number", "minimum": 0, "maximum": 1}
        return (
            {
                "model": "google/gemma-3-12b-it",
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.001},
                "choices": [{"message": {"content": "0.82"}}],
            },
            100.0,
            125.0,
            "HTTP/2",
        )

    monkeypatch.setattr("demo.backend._stream_json_number", fake_stream_json_number)
    intent = CallIntent(
        session_id="session",
        task="fix the code",
        messages=({"role": "user", "content": "fix the code"},),
        predicted_output_tokens=128,
    )
    scores = ranker.rank(
        intent=intent,
        candidates=candidates,
        timeout_ms=500,
    )
    cached_scores = ranker.rank(intent=intent, candidates=candidates, timeout_ms=500)
    ranker.rank(
        intent=intent.model_copy(update={"task": "review the code"}),
        candidates=candidates,
        timeout_ms=500,
    )

    assert len(calls) == 24
    assert len(set(calls)) == 12
    assert set(scores) == {candidate.model_id for candidate in candidates}
    assert all(0 <= score <= 1 for score in scores.values())
    assert cached_scores == scores
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert len([event for event in events if event.get("purpose") == "model_ranker"]) == 24
    assert len([event for event in events if event["event"] == "ranker_cache_hit"]) == 1
    ranker.close()


def test_streaming_ranker_captures_first_content_and_terminal_usage() -> None:
    captured_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        events = (
            'data: {"model":"google/gemma-3-12b-it","provider":"DeepInfra",'
            '"choices":[{"delta":{"content":"0."}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"82"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":10,'
            '"completion_tokens":2,"cost":0.001}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(
            200,
            text=events,
            extensions={"http_version": b"HTTP/2"},
        )

    with httpx.Client(
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
    ) as client:
        result, ttft_ms, total_ms, http_version = _stream_json_number(
            client,
            {"model": "google/gemma-3-12b-it", "messages": []},
            15,
        )

    assert result["choices"][0]["message"]["content"] == "0.82"
    assert result["usage"]["cost"] == 0.001
    assert captured_payload["stream"] is True
    assert captured_payload["stream_options"] == {"include_usage": True}
    assert 0 <= ttft_ms <= total_ms
    assert http_version == "HTTP/2"


def test_managed_provider_errors_are_internally_rerouted() -> None:
    prepared = object()
    for status in (400, 403, 404, 408, 413, 422, 429, 500, 502, 503):
        assert should_reroute_provider_error("managed", status, prepared, 1, 4)
    for status in (200, 401, 402):
        assert not should_reroute_provider_error("managed", status, prepared, 1, 4)
    assert not should_reroute_provider_error("managed", 429, prepared, 4, 4)
    assert not should_reroute_provider_error("baseline", 429, prepared, 1, 4)
    assert should_reroute_rate_limit("managed", 500, prepared, 1, 4)


def test_portable_catalog_uses_direct_standard_codex_tools() -> None:
    template = {
        "slug": "gpt-5.6-sol",
        "tool_mode": "code_mode_only",
        "use_responses_lite": True,
        "shell_type": "shell_command",
        "apply_patch_tool_type": "freeform",
        "multi_agent_version": "v2",
        "supports_search_tool": True,
    }
    model = portable_codex_model(template, "openai/gpt-5.6-sol")
    assert model["tool_mode"] == "direct"
    assert model["use_responses_lite"] is False
    assert model["shell_type"] == "unified_exec"
    assert model["apply_patch_tool_type"] is None
    assert model["multi_agent_version"] == "disabled"
    assert model["supports_search_tool"] is False
