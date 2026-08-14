"""Interactive side-by-side terminal runner for two real Codex executions."""

from __future__ import annotations

import argparse
import copy
import curses
import json
import math
import os
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from collections import deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
ANSWER_DIVIDER = "\x1ePROMPTRAIL_ANSWER_DIVIDER"


def codex_command(
    lane: str,
    port: int,
    model: str,
    workspace: Path,
    prompt: str,
    model_catalog: Path | None = None,
    thread_id: str | None = None,
) -> list[str]:
    provider = f"demo-{lane}"
    provider_config = (
        '{ name = "PromptRail Demo", '
        f'base_url = "http://127.0.0.1:{port}", '
        'env_key = "PROMPTRAIL_DEMO_PROXY_TOKEN", requires_openai_auth = false, '
        'wire_api = "responses", supports_websockets = false }'
    )
    provider_overrides = [
        "-c",
        f'model_provider="{provider}"',
        "-c",
        f"model_providers.{provider}={provider_config}",
    ]
    initial_options = [
        "--json",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--model",
        model,
        *provider_overrides,
    ]
    if thread_id:
        # Keep resumed output structured so final answers can be formatted exactly
        # like the first turn. Workspace and model settings stay on the compatible
        # config boundary while the exact thread ID preserves conversation state.
        command = [
            "codex",
            "exec",
            "resume",
            "--json",
            "-c",
            "mcp_servers={}",
            "-c",
            f'model="{model}"',
            *provider_overrides,
        ]
    else:
        command = [
            "codex",
            "exec",
            *initial_options,
            "--sandbox",
            "workspace-write",
            "--cd",
            str(workspace),
        ]
    if model_catalog is not None:
        command.extend(["-c", f'model_catalog_json="{model_catalog}"'])
    if thread_id:
        command.append(thread_id)
    command.append(prompt)
    return command


def launch_codex(
    command: list[str],
    workspace: Path,
    env: dict[str, str],
) -> subprocess.Popen[str]:
    """Launch every initial or resumed turn inside its isolated lane workspace."""

    return subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        cwd=workspace,
    )


def build_model_catalog(path: Path, model: str | tuple[str, ...]) -> None:
    result = subprocess.run(
        ["codex", "debug", "models", "--bundled"],
        check=True,
        capture_output=True,
        text=True,
    )
    bundled = json.loads(result.stdout)
    models = bundled.get("models") or []
    if not models:
        raise RuntimeError("Codex bundled model catalog is empty")
    requested_models = (model,) if isinstance(model, str) else model
    entries = []
    for model_id in requested_models:
        preferred = next(
            (item for item in models if item.get("slug") == model_id.removeprefix("openai/")),
            models[0],
        )
        entries.append(portable_codex_model(preferred, model_id))
    path.write_text(json.dumps({"models": entries}, indent=2) + "\n", encoding="utf-8")


def portable_codex_model(template: dict[str, Any], model: str) -> dict[str, Any]:
    """Make Codex expose standard direct tools to every routed model."""

    entry = copy.deepcopy(template)
    entry["slug"] = model
    entry["display_name"] = f"PromptRail · {model}"
    entry["description"] = "Provider-neutral Codex harness used by the PromptRail live demo"
    entry["tool_mode"] = "direct"
    entry["use_responses_lite"] = False
    entry["shell_type"] = "unified_exec"
    entry["apply_patch_tool_type"] = None
    entry["multi_agent_version"] = "disabled"
    entry["supports_search_tool"] = False
    return entry


def resolve_model_command(
    prompt: str,
    models: tuple[dict[str, Any], ...],
    current_model: str,
) -> tuple[bool, str, list[str]]:
    command, _, raw_query = prompt.strip().partition(" ")
    if command.casefold() != "/model":
        return False, current_model, []
    query = raw_query.strip().casefold()
    if not query:
        lines = ["", "MODEL SELECTOR", f"CURRENT  {current_model}"]
        for option in models:
            alias = str((option.get("aliases") or [option["model"]])[0])
            lines.append(f"  /model {alias:<8} {option['label']}  [{option['model']}]")
        lines.extend(("", "Type one of the commands above. Comparison totals reset on change.", ""))
        return True, current_model, lines

    for option in models:
        candidates = {
            str(option["model"]).casefold(),
            str(option["label"]).casefold(),
            *(str(alias).casefold() for alias in option.get("aliases") or ()),
        }
        if query in candidates:
            selected = str(option["model"])
            if selected == current_model:
                return True, current_model, [f"MODEL   already using {option['label']}"]
            return (
                True,
                selected,
                [
                    f"MODEL   baseline -> {option['label']}  [{selected}]",
                    "RESET   cost, token, cache, and call comparison window",
                ],
            )
    aliases = ", ".join(str((item.get("aliases") or [item["model"]])[0]) for item in models)
    return (
        True,
        current_model,
        [
            f"ERROR   unknown model {raw_query.strip()!r}",
            f"MODELS  {aliases}",
        ],
    )


def prepare_workspace(source: Path, target: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        relative = Path(directory).resolve().relative_to(source.resolve())
        result = set()
        for name in names:
            candidate = str(relative / name) if str(relative) != "." else name
            if (
                name
                in {
                    ".git",
                    ".venv",
                    ".pytest_cache",
                    ".ruff_cache",
                    "dist",
                    "__pycache__",
                }
                or candidate == "demo/runs"
            ):
                result.add(name)
        return result

    shutil.copytree(source, target, ignore=ignore)


def terminal_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", text)
    return text.replace("**", "").strip()


def compact_command(value: Any, limit: int = 132) -> str:
    command = " ".join(str(value or "").split())
    match = re.fullmatch(r"/bin/(?:zsh|bash|sh) -lc (.+)", command)
    if match:
        try:
            command = shlex.split(match.group(1))[0]
        except (ValueError, IndexError):
            command = match.group(1).strip("'\"")
    command = re.sub(
        r"/[^ '\"]*/demo/runs/\d{8}-\d{6}/(?:baseline|managed)/",
        "./",
        command,
    )
    return textwrap.shorten(command, width=limit, placeholder=" ...")


def render_codex_event(payload: dict[str, Any]) -> list[str]:
    event_type = str(payload.get("type") or "")
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    item_type = str(item.get("type") or "")
    if item_type == "agent_message":
        text = terminal_text(item.get("text") or item.get("content"))
        return ["", "UPDATE", *text.splitlines(), ""] if text else []
    if item_type == "command_execution":
        status = str(item.get("status") or "")
        prefix = "DONE" if status == "completed" else "RUN"
        suffix = f" | exit {item['exit_code']}" if item.get("exit_code") is not None else ""
        return [f"{prefix:<8}{compact_command(item.get('command'))}{suffix}"]
    if item_type == "mcp_tool_call":
        status = str(item.get("status") or "")
        prefix = "DONE" if status == "completed" else "TOOL"
        return [f"{prefix:<8}{terminal_text(item.get('name') or 'MCP tool')}"]
    if item_type == "file_change":
        return [f"EDIT    {terminal_text(item.get('path') or 'workspace files')}"]
    if event_type == "turn.completed":
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        tokens = int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
        return [f"COMPLETE  turn finished | {tokens:,} tokens"]
    if event_type == "error":
        return [f"ERROR   {terminal_text(payload.get('message') or payload)}"]
    return []


def render_codex_line(line: str) -> tuple[list[str], str | None]:
    """Decode one Codex output line without assuming every JSON value is an event."""

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        text = line.strip()
        return ([text] if text else []), None
    if not isinstance(payload, dict):
        if isinstance(payload, str):
            text = payload.strip()
        else:
            text = json.dumps(payload, ensure_ascii=False)
        return ([text] if text else []), None
    thread_id = None
    if payload.get("type") == "thread.started" and payload.get("thread_id"):
        thread_id = str(payload["thread_id"])
    return render_codex_event(payload), thread_id


def render_ledger_error(event: dict[str, Any]) -> str | None:
    if event.get("event") == "error":
        return f"ERROR   proxy | {terminal_text(event['message'])}"
    return None


def render_ledger_event(event: dict[str, Any]) -> str | None:
    event_type = event.get("event")
    if event_type == "allocation":
        repaired = " | floor repaired" if event.get("repaired") else ""
        return (
            f"BUDGET  ${float(event.get('effective_cost_usd', 0)):.6f} | "
            f"{int(event.get('effective_latency_ms', 0)):,} ms{repaired}"
        )
    if event_type == "decision":
        return (
            f"ROUTE   {event.get('model', 'unknown')} | "
            f"compact {int(event.get('compacted_tokens', 0)):,} | "
            f"cache {int(event.get('cached_tokens', 0)):,} | "
            f"context {int(event.get('required_context_tokens', 0)):,} | "
            f"output {int(event.get('predicted_output_tokens', 0)):,} tok"
        )
    if event_type == "control_plane":
        return (
            f"CONTROL {int(event.get('total_ms', 0)):,} ms | "
            f"predict {int(event.get('output_prediction_ms', 0)):,} | "
            f"Gemma {int(event.get('gemma_allocation_ms', 0)):,} | "
            f"rank {int(event.get('semantic_ranking_ms', 0)):,} | "
            f"compact {int(event.get('compaction_ms', 0)):,} ms"
        )
    if event_type in {"rate_limit_reroute", "provider_error_reroute"}:
        return (
            f"FAILOVER  HTTP {event.get('status', 429)} | "
            f"{event.get('previous_model', 'unknown')} -> {event.get('model', 'unknown')}"
        )
    if event_type == "provider_error_terminal":
        return (
            f"ERROR   HTTP {event.get('status', '?')} | "
            f"{event.get('model', 'unknown')} | account-wide"
        )
    if event_type == "usage" and event.get("purpose") == "agent":
        return (
            f"USAGE   {event.get('model', 'unknown')} | "
            f"in {int(event.get('input_tokens', 0)):,} | "
            f"out {int(event.get('output_tokens', 0)):,} | "
            f"cache {int(event.get('cached_tokens', 0)):,} | "
            f"${float(event.get('cost', 0)):.6f} | "
            f"TTFT {int(event.get('end_to_end_ttft_ms') or 0):,} ms"
        )
    return render_ledger_error(event)


def savings_percentage(baseline_cost: float, managed_cost: float) -> float | None:
    if baseline_cost <= 0:
        return None
    return (baseline_cost - managed_cost) / baseline_cost * 100


def managed_call_limit_reached(agent_calls: int, limit: int) -> bool:
    return limit > 0 and agent_calls >= limit


def comparison_exit_code(return_codes: list[int], comparison_error: str | None) -> int:
    if comparison_error or any(code != 0 for code in return_codes):
        return 1
    return 0


def big_text(value: str) -> list[str]:
    try:
        from pyfiglet import figlet_format
    except ModuleNotFoundError:
        executable = ROOT / ".venv" / "bin" / "pyfiglet"
        if executable.exists():
            command = [str(executable)]
        elif uv := shutil.which("uv"):
            command = [uv, "run", "--project", str(ROOT), "pyfiglet"]
        else:
            raise RuntimeError(
                "FIGlet is unavailable; install uv and run `uv sync --project demo`"
            ) from None
        rendered = subprocess.run(
            [*command, "-f", "big", "-w", "200", value],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    else:
        rendered = figlet_format(value, font="big", width=200)
    return rendered.rstrip("\n").splitlines()


def final_percentage_label(percentage: float) -> str:
    if percentage >= 0:
        return f"{percentage:.1f}% SAVED"
    return f"{abs(percentage):.1f}% ADDITIONAL COST"


def savings_seconds_remaining(deadline: float, now: float) -> int:
    return max(0, math.ceil(deadline - now))


def add_centered(screen: Any, row: int, text: str, width: int, attribute: int = 0) -> None:
    column = max(0, (width - len(text)) // 2)
    screen.addnstr(row, column, text, max(1, width - column - 1), attribute)


def log_window(
    lines: deque[str],
    width: int,
    height: int,
    scroll_offset: int,
) -> tuple[list[str], int]:
    """Return a wrapped viewport and its clamped offset from the newest line."""

    wrapped: list[str] = []
    for line in lines:
        if line == ANSWER_DIVIDER:
            wrapped.append("—" * max(1, width))
        else:
            wrapped.extend(textwrap.wrap(line, max(10, width)) or [""])
    visible = max(0, height)
    maximum_offset = max(0, len(wrapped) - visible)
    offset = min(max(0, scroll_offset), maximum_offset)
    end = max(0, len(wrapped) - offset)
    start = max(0, end - visible)
    return wrapped[start:end], offset


def navigate_logs(
    key: int | str,
    scroll_offsets: dict[str, int],
    active_lane: str,
    page_size: int,
) -> tuple[bool, str]:
    """Apply a keyboard navigation event to the focused log pane."""

    if key in ("\t", 9):
        return True, "managed" if active_lane == "baseline" else "baseline"
    if key in (curses.KEY_UP, "KEY_UP"):
        scroll_offsets[active_lane] += 1
    elif key in (curses.KEY_DOWN, "KEY_DOWN"):
        scroll_offsets[active_lane] = max(0, scroll_offsets[active_lane] - 1)
    elif key in (curses.KEY_PPAGE, "KEY_PPAGE"):
        scroll_offsets[active_lane] += max(1, page_size)
    elif key in (curses.KEY_NPAGE, "KEY_NPAGE"):
        scroll_offsets[active_lane] = max(0, scroll_offsets[active_lane] - max(1, page_size))
    elif key in (curses.KEY_HOME, "KEY_HOME"):
        scroll_offsets[active_lane] = sys.maxsize
    elif key in (curses.KEY_END, "KEY_END"):
        scroll_offsets[active_lane] = 0
    else:
        return False, active_lane
    return True, active_lane


def navigate_mouse(
    width: int,
    x: int,
    button_state: int,
    scroll_offsets: dict[str, int],
    active_lane: str,
) -> tuple[bool, str]:
    """Scroll the pane under the mouse wheel when the terminal reports it."""

    lane = "baseline" if x < width // 2 else "managed"
    scroll_up = getattr(curses, "BUTTON4_PRESSED", 0)
    scroll_down = getattr(curses, "BUTTON5_PRESSED", 0)
    if scroll_up and button_state & scroll_up:
        scroll_offsets[lane] += 3
    elif scroll_down and button_state & scroll_down:
        scroll_offsets[lane] = max(0, scroll_offsets[lane] - 3)
    else:
        return False, active_lane
    return True, lane


def draw_final(
    screen: Any,
    totals: dict[str, dict[str, Any]],
    comparison_error: str | None = None,
    footer: str = "Returning to the conversation",
) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    baseline_cost = float(totals["baseline"]["cost"])
    managed_cost = float(totals["managed"]["cost"])
    percentage = savings_percentage(baseline_cost, managed_cost)
    heading = "COMPARISON STOPPED" if comparison_error else "PROMPTRAIL SAVINGS"
    add_centered(screen, 1, heading, width, curses.A_BOLD)

    if comparison_error:
        banner = big_text("STOP")
    elif percentage is None:
        banner = ["Savings unavailable: baseline reported no cost"]
    else:
        banner = big_text(f"{percentage:.1f}%")
    start_row = max(3, (height - len(banner)) // 2 - 2)
    accent = curses.A_BOLD
    if curses.has_colors() and percentage is not None:
        accent |= curses.color_pair(1 if percentage >= 0 else 2)
    for offset, line in enumerate(banner):
        if start_row + offset < height - 4:
            add_centered(screen, start_row + offset, line, width, accent)

    exact_row = min(height - 5, start_row + len(banner) + 1)
    if comparison_error and exact_row > start_row:
        add_centered(screen, exact_row, comparison_error, width, curses.A_BOLD | curses.A_REVERSE)
    elif percentage is not None and exact_row > start_row:
        add_centered(
            screen,
            exact_row,
            f"  {final_percentage_label(percentage)}  ",
            width,
            accent | curses.A_REVERSE,
        )

    saved = baseline_cost - managed_cost
    if comparison_error:
        saved_detail = "No savings result: the managed lane did not complete"
    else:
        saved_detail = (
            f"${saved:.6f} saved with actual provider usage"
            if saved >= 0
            else f"${abs(saved):.6f} additional cost with actual provider usage"
        )
    detail_row = min(height - 5, exact_row + 2)
    summary_rows = (
        (
            f"BASELINE    {totals['baseline'].get('model', 'gpt-5.6-sol')} | "
            f"${baseline_cost:.6f} | {int(totals['baseline']['tokens']):,} tok | "
            f"{int(totals['baseline']['cached']):,} cached | "
            f"{int(totals['baseline']['calls']):,} metered calls"
        ),
        (
            f"PROMPTRAIL  {totals['managed'].get('model', 'selecting')} | "
            f"${managed_cost:.6f} | {int(totals['managed']['tokens']):,} tok | "
            f"{int(totals['managed']['cached']):,} cached | "
            f"{int(totals['managed']['calls']):,} metered calls"
        ),
    )
    for offset, line in enumerate(summary_rows):
        if detail_row + offset < height - 2:
            add_centered(screen, detail_row + offset, line, width)
    if detail_row + 2 < height:
        add_centered(screen, detail_row + 2, saved_detail, width, curses.A_DIM)
    add_centered(screen, height - 1, footer, width)
    screen.refresh()


def draw(
    screen: Any,
    logs: dict[str, deque[str]],
    totals: dict[str, dict[str, Any]],
    decisions: deque[str],
    status: str,
    footer: str = "Press q to quit after both agents finish",
    scroll_offsets: dict[str, int] | None = None,
    active_lane: str = "managed",
) -> None:
    screen.erase()
    height, width = screen.getmaxyx()
    mid = width // 2
    baseline = totals["baseline"]
    managed = totals["managed"]
    savings = baseline["cost"] - managed["cost"]
    title = (
        f" BASELINE  ${baseline['cost']:.6f}  {baseline['tokens']:,} tok "
        f"| PROMPTRAIL  ${managed['cost']:.6f}  {managed['tokens']:,} tok "
        f"| SAVED  ${savings:.6f} "
    )
    screen.addnstr(0, 0, title.ljust(width), width - 1, curses.A_REVERSE)
    baseline_heading = f"CODEX · BASELINE · {baseline.get('model', 'selecting model')}"
    baseline_style = curses.A_BOLD | (curses.A_REVERSE if active_lane == "baseline" else 0)
    screen.addnstr(1, 1, baseline_heading, mid - 2, baseline_style)
    managed_heading = f"CODEX · PROMPTRAIL · {managed.get('model', 'selecting model')}"
    managed_style = curses.A_BOLD | (curses.A_REVERSE if active_lane == "managed" else 0)
    screen.addnstr(1, mid + 1, managed_heading, width - mid - 2, managed_style)
    content_bottom = max(3, height - 5)
    for row in range(1, content_bottom):
        if mid < width:
            screen.addch(row, mid, curses.ACS_VLINE)
    visible = content_bottom - 3
    for lane, left, pane_width in (
        ("baseline", 1, mid - 2),
        ("managed", mid + 1, width - mid - 2),
    ):
        offset = scroll_offsets[lane] if scroll_offsets is not None else 0
        window, clamped_offset = log_window(logs[lane], pane_width, visible, offset)
        if scroll_offsets is not None:
            scroll_offsets[lane] = clamped_offset
        for row, line in enumerate(window, start=2):
            screen.addnstr(row, left, line, pane_width)
    decision = decisions[-1] if decisions else "Waiting for PromptRail decision"
    screen.addnstr(content_bottom, 0, f" {decision}".ljust(width), width - 1, curses.A_DIM)
    screen.addnstr(content_bottom + 1, 0, f" {status}".ljust(width), width - 1)
    screen.addnstr(
        content_bottom + 3,
        0,
        f" {footer}".ljust(width),
        width - 1,
        curses.A_DIM,
    )
    screen.refresh()


def read_prompt(
    screen: Any,
    logs: dict[str, deque[str]],
    totals: dict[str, dict[str, Any]],
    decisions: deque[str],
    turn_number: int,
    scroll_offsets: dict[str, int],
    active_lane: str,
) -> tuple[str, str]:
    screen.nodelay(False)
    curses.curs_set(1)
    curses.noecho()
    characters: list[str] = []
    cursor = 0
    try:
        while True:
            height, width = screen.getmaxyx()
            page_size = max(1, height - 8)
            draw(
                screen,
                logs,
                totals,
                decisions,
                f"Ready for turn {turn_number}",
                "TAB pane · arrows/PgUp/PgDn scroll · Home/End · /model · q quits",
                scroll_offsets,
                active_lane,
            )
            prompt = "".join(characters)
            available = max(1, width - 3)
            start = max(0, cursor - available + 1)
            shown = prompt[start : start + available]
            screen.move(height - 1, 0)
            screen.clrtoeol()
            screen.addnstr(height - 1, 0, "> ", max(1, width - 1), curses.A_BOLD)
            screen.addnstr(height - 1, 2, shown, available)
            screen.move(height - 1, min(width - 1, 2 + cursor - start))
            screen.refresh()
            key = screen.get_wch()
            handled, new_active_lane = navigate_logs(key, scroll_offsets, active_lane, page_size)
            if handled:
                active_lane = new_active_lane
                continue
            if key == curses.KEY_MOUSE:
                try:
                    _, x, _, _, button_state = curses.getmouse()
                except curses.error:
                    continue
                _, active_lane = navigate_mouse(width, x, button_state, scroll_offsets, active_lane)
                continue
            if key in ("\n", "\r", curses.KEY_ENTER):
                return prompt.strip(), active_lane
            if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                if cursor:
                    del characters[cursor - 1]
                    cursor -= 1
            elif key == curses.KEY_DC:
                if cursor < len(characters):
                    del characters[cursor]
            elif key == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
            elif key == curses.KEY_RIGHT:
                cursor = min(len(characters), cursor + 1)
            elif isinstance(key, str) and key.isprintable():
                characters.insert(cursor, key)
                cursor += 1
    finally:
        curses.curs_set(0)
        screen.nodelay(True)


def show_savings(
    screen: Any,
    totals: dict[str, dict[str, Any]],
    comparison_error: str | None,
    duration_seconds: float,
) -> bool:
    deadline = time.monotonic() + duration_seconds
    screen.nodelay(True)
    while True:
        now = time.monotonic()
        remaining = savings_seconds_remaining(deadline, now)
        if remaining <= 0:
            return False
        draw_final(
            screen,
            totals,
            comparison_error,
            f"Conversation resumes in {remaining}s · press q to quit",
        )
        try:
            if screen.getch() == ord("q"):
                return True
        except curses.error:
            pass
        time.sleep(0.05)


def interactive(
    screen: Any,
    args: argparse.Namespace,
    backend: subprocess.Popen[str],
    events: Path,
) -> int:
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
    try:
        curses.mousemask(curses.ALL_MOUSE_EVENTS)
        curses.mouseinterval(0)
    except curses.error:
        pass
    run_root = ROOT / "runs" / time.strftime("%Y%m%d-%H%M%S")
    source = Path(args.workspace).resolve()
    baseline_workspace = run_root / "baseline"
    managed_workspace = run_root / "managed"
    model_catalog = run_root / "codex-models.json"
    run_root.mkdir(parents=True, exist_ok=True)
    comparison_models = tuple(args.comparison_models)
    comparison_model_ids = tuple(str(item["model"]) for item in comparison_models)
    build_model_catalog(model_catalog, comparison_model_ids)
    prepare_workspace(source, baseline_workspace)
    prepare_workspace(source, managed_workspace)
    env = os.environ.copy()
    env["PROMPTRAIL_DEMO_PROXY_TOKEN"] = "loopback-only-demo"
    logs = {"baseline": deque(), "managed": deque()}
    scroll_offsets = {"baseline": 0, "managed": 0}
    active_lane = "managed"
    totals = {
        "baseline": {
            "cost": 0.0,
            "tokens": 0,
            "cached": 0,
            "calls": 0,
            "model": "gpt-5.6-sol",
        },
        "managed": {
            "cost": 0.0,
            "tokens": 0,
            "cached": 0,
            "calls": 0,
            "model": "selecting",
        },
    }
    raw_totals = {
        lane: {"cost": 0.0, "tokens": 0, "cached": 0, "calls": 0}
        for lane in ("baseline", "managed")
    }
    comparison_offsets = {
        lane: {"cost": 0.0, "tokens": 0, "cached": 0, "calls": 0}
        for lane in ("baseline", "managed")
    }
    decisions: deque[str] = deque(maxlen=20)
    thread_ids: dict[str, str] = {}
    baseline_model = args.baseline_model
    event_offset = 0
    turn_number = 1
    overall_failed = False

    while True:
        prompt, active_lane = read_prompt(
            screen,
            logs,
            totals,
            decisions,
            turn_number,
            scroll_offsets,
            active_lane,
        )
        if prompt.casefold() == "q":
            return 1 if overall_failed else 0
        if not prompt:
            continue
        scroll_offsets.update(baseline=0, managed=0)
        handled, selected_model, model_lines = resolve_model_command(
            prompt,
            comparison_models,
            baseline_model,
        )
        if handled:
            logs["baseline"].extend(model_lines)
            if selected_model != baseline_model:
                baseline_model = selected_model
                for lane in ("baseline", "managed"):
                    for field in ("cost", "tokens", "cached", "calls"):
                        comparison_offsets[lane][field] = raw_totals[lane][field]
                        totals[lane][field] = 0.0 if field == "cost" else 0
                totals["baseline"]["model"] = baseline_model
                totals["managed"]["model"] = "selecting"
                decisions.append(f"NEW COMPARISON · {baseline_model} vs PromptRail")
                logs["managed"].extend(("", f"COMPARE PromptRail vs {baseline_model}", ""))
            continue
        for lane in logs:
            logs[lane].extend(("", f"USER    {prompt}", ""))

        processes = {}
        for lane, port, workspace, model in (
            ("baseline", args.baseline_port, baseline_workspace, baseline_model),
            ("managed", args.managed_port, managed_workspace, args.baseline_model),
        ):
            processes[lane] = launch_codex(
                codex_command(
                    lane,
                    port,
                    model,
                    workspace,
                    prompt,
                    model_catalog,
                    thread_ids.get(lane),
                ),
                workspace,
                env=env,
            )

        selector = selectors.DefaultSelector()
        for lane, process in processes.items():
            assert process.stdout is not None
            selector.register(process.stdout, selectors.EVENT_READ, lane)

        managed_agent_calls = 0
        comparison_error = None
        quit_after_turn = False
        screen.nodelay(True)
        pending_agent_messages: dict[str, str] = {}

        def consume_codex_line(
            lane: str,
            line: str,
            _pending: dict[str, str] = pending_agent_messages,
        ) -> None:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
                if item.get("type") == "agent_message":
                    previous = _pending.get(lane)
                    if previous:
                        logs[lane].extend(("", "UPDATE", *previous.splitlines(), ""))
                    text = terminal_text(item.get("text") or item.get("content"))
                    if text:
                        _pending[lane] = text
                    if payload.get("type") == "thread.started" and payload.get("thread_id"):
                        thread_ids[lane] = str(payload["thread_id"])
                    return
                pending = _pending.pop(lane, None)
                if pending:
                    if payload.get("type") == "turn.completed":
                        logs[lane].extend(("", ANSWER_DIVIDER, "ANSWER", *pending.splitlines(), ""))
                    else:
                        logs[lane].extend(("", "UPDATE", *pending.splitlines(), ""))
            rendered, started_thread_id = render_codex_line(line)
            if started_thread_id is not None:
                thread_ids[lane] = started_thread_id
            logs[lane].extend(item for item in rendered if item)

        def consume_ledger(turn_processes: dict[str, subprocess.Popen[str]]) -> None:
            nonlocal event_offset, managed_agent_calls, comparison_error
            if not events.exists():
                return
            with events.open("r", encoding="utf-8") as stream:
                stream.seek(event_offset)
                for line in stream:
                    event = json.loads(line)
                    if event.get("totals"):
                        lane = event["lane"]
                        raw_totals[lane].update(event["totals"])
                        for field in ("cost", "tokens", "cached", "calls"):
                            totals[lane][field] = (
                                raw_totals[lane][field] - comparison_offsets[lane][field]
                            )
                    if event.get("event") == "catalog_loaded":
                        decisions.append(
                            f"PromptRail catalog: {event['candidate_count']} MongoDB tool models"
                        )
                    elif event.get("event") == "ranker_shortlist":
                        decisions.append(
                            f"PromptRail ranking {event['candidate_count']} of "
                            f"{event['catalog_count']} eligible models"
                        )
                    elif event.get("event") == "decision":
                        totals["managed"]["model"] = event["model"]
                        decisions.append(
                            f"ROUTE {event['model']} | compacted "
                            f"{event['compacted_tokens']:,} tok | "
                            f"cache {event['cached_tokens']:,} tok"
                        )
                    elif event.get("event") in {
                        "rate_limit_reroute",
                        "provider_error_reroute",
                    }:
                        decisions.append(
                            f"FAILOVER HTTP {event.get('status', 429)} | "
                            f"{event['previous_model']} -> {event['model']}"
                        )
                    if (
                        event.get("lane") == "managed"
                        and event.get("event") == "usage"
                        and event.get("purpose") == "agent"
                    ):
                        managed_agent_calls += 1
                    if event.get("event") == "usage" and event.get("purpose") == "agent":
                        totals[event["lane"]]["model"] = event.get("model", "unknown")
                    if (
                        comparison_error is None
                        and managed_call_limit_reached(
                            managed_agent_calls,
                            args.max_managed_agent_calls,
                        )
                        and turn_processes["managed"].poll() is None
                    ):
                        comparison_error = (
                            "Managed lane stopped after "
                            f"{args.max_managed_agent_calls} calls in this turn"
                        )
                        logs["managed"].append(f"[stopped] {comparison_error}")
                        turn_processes["managed"].terminate()
                    ledger_line = render_ledger_event(event)
                    if ledger_line:
                        logs[event["lane"]].append(ledger_line)
                    error_message = render_ledger_error(event)
                    if error_message:
                        comparison_error = comparison_error or error_message
                event_offset = stream.tell()

        while any(process.poll() is None for process in processes.values()):
            for key, _ in selector.select(timeout=0.08):
                line = key.fileobj.readline()
                if line:
                    consume_codex_line(key.data, line)
            consume_ledger(processes)
            statuses = ", ".join(
                f"{lane}: {'running' if process.poll() is None else 'done'}"
                for lane, process in processes.items()
            )
            draw(
                screen,
                logs,
                totals,
                decisions,
                f"Turn {turn_number} · {statuses}",
                "TAB pane · arrows/PgUp/PgDn scroll · Home/End · q after turn",
                scroll_offsets,
                active_lane,
            )
            try:
                key = screen.getch()
                handled, active_lane = navigate_logs(
                    key, scroll_offsets, active_lane, max(1, screen.getmaxyx()[0] - 8)
                )
                if key == curses.KEY_MOUSE:
                    try:
                        _, x, _, _, button_state = curses.getmouse()
                    except curses.error:
                        pass
                    else:
                        _, active_lane = navigate_mouse(
                            screen.getmaxyx()[1],
                            x,
                            button_state,
                            scroll_offsets,
                            active_lane,
                        )
                elif not handled and key == ord("q"):
                    quit_after_turn = True
            except curses.error:
                pass

        for lane, process in processes.items():
            assert process.stdout is not None
            for line in process.stdout:
                consume_codex_line(lane, line)
        selector.close()
        consume_ledger(processes)
        return_codes = [process.wait() for process in processes.values()]
        if any(code != 0 for code in return_codes):
            comparison_error = comparison_error or "One or both Codex lanes failed this turn"
        overall_failed = overall_failed or comparison_error is not None

        quit_from_savings = show_savings(
            screen,
            totals,
            comparison_error,
            args.savings_display_seconds,
        )
        if quit_after_turn or quit_from_savings:
            return 1 if overall_failed else 0
        turn_number += 1


def main() -> int:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description="Run the real PromptRail Codex comparison")
    parser.add_argument("--workspace", default=str(PROJECT))
    parser.add_argument("--env-file")
    parser.add_argument("--baseline-port", type=int, default=8765)
    parser.add_argument("--managed-port", type=int, default=8766)
    parser.add_argument(
        "--max-managed-agent-calls",
        type=int,
        default=int(config["max_managed_agent_calls"]),
    )
    parser.add_argument(
        "--savings-display-seconds",
        type=float,
        default=float(config["savings_display_seconds"]),
    )
    parser.add_argument(
        "--baseline-model",
        default=os.getenv("PROMPTRAIL_DEMO_BASELINE_MODEL", config["baseline_model"]),
    )
    args = parser.parse_args()
    comparison_models = [dict(item) for item in config["comparison_models"]]
    if args.baseline_model not in {str(item["model"]) for item in comparison_models}:
        comparison_models.insert(
            0,
            {
                "model": args.baseline_model,
                "label": args.baseline_model,
                "aliases": ["default"],
            },
        )
    args.comparison_models = tuple(comparison_models)
    if shutil.which("codex") is None:
        raise SystemExit("codex is not installed")
    ROOT.joinpath("runs").mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="promptrail-demo-",
        suffix=".jsonl",
        delete=False,
    ) as event_handle:
        events = Path(event_handle.name)
    demo_python = ROOT / ".venv" / "bin" / "python"
    command = [
        str(demo_python) if demo_python.exists() else sys.executable,
        str(ROOT / "backend.py"),
        "--events",
        str(events),
        "--baseline-port",
        str(args.baseline_port),
        "--managed-port",
        str(args.managed_port),
    ]
    if args.env_file:
        command.extend(["--env-file", args.env_file])
    backend = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=PROJECT,
    )
    assert backend.stdout is not None
    ready = backend.stdout.readline().strip()
    if backend.poll() is not None or '"status": "ready"' not in ready:
        remainder = backend.stdout.read()
        raise SystemExit(f"Demo backend failed to start:\n{ready}\n{remainder}")
    try:
        return curses.wrapper(interactive, args, backend, events)
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend.kill()
        events.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
