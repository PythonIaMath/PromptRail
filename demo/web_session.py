"""Thread-safe browser adapter for the real two-lane Codex demo."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from .run import (
        ANSWER_DIVIDER,
        build_model_catalog,
        codex_command,
        launch_codex,
        prepare_workspace,
        render_codex_line,
        render_ledger_error,
        render_ledger_event,
        resolve_model_command,
        savings_percentage,
        terminal_text,
    )
except ImportError:  # Direct script execution from the repository root.
    from run import (
        ANSWER_DIVIDER,
        build_model_catalog,
        codex_command,
        launch_codex,
        prepare_workspace,
        render_codex_line,
        render_ledger_error,
        render_ledger_event,
        resolve_model_command,
        savings_percentage,
        terminal_text,
    )


class ComparisonSession:
    """Own one persistent baseline/managed conversation and its isolated workspaces."""

    def __init__(
        self,
        *,
        demo_root: Path,
        workspace: Path,
        events: Path,
        baseline_port: int,
        managed_port: int,
        baseline_model: str,
        comparison_models: tuple[dict[str, Any], ...],
        savings_display_seconds: float,
    ) -> None:
        self.demo_root = demo_root.resolve()
        self.events = events.resolve()
        self.baseline_port = baseline_port
        self.managed_port = managed_port
        self.default_model = baseline_model
        self.comparison_models = comparison_models
        self.savings_display_seconds = savings_display_seconds
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-web-" + uuid4().hex[:6]
        self.run_root = self.demo_root / "runs" / run_id
        self.baseline_workspace = self.run_root / "baseline"
        self.managed_workspace = self.run_root / "managed"
        self.model_catalog = self.run_root / "codex-models.json"
        self.run_root.mkdir(parents=True, exist_ok=True)
        build_model_catalog(
            self.model_catalog,
            tuple(str(item["model"]) for item in comparison_models),
        )
        prepare_workspace(workspace.resolve(), self.baseline_workspace)
        prepare_workspace(workspace.resolve(), self.managed_workspace)

        self.env = os.environ.copy()
        self.env["PROMPTRAIL_DEMO_PROXY_TOKEN"] = "loopback-only-demo"
        self.logs: dict[str, deque[str]] = {
            "baseline": deque(maxlen=5_000),
            "managed": deque(maxlen=5_000),
        }
        self.totals: dict[str, dict[str, Any]] = {
            "baseline": {
                "cost": 0.0,
                "tokens": 0,
                "cached": 0,
                "calls": 0,
                "model": baseline_model,
            },
            "managed": {
                "cost": 0.0,
                "tokens": 0,
                "cached": 0,
                "calls": 0,
                "model": "selecting",
            },
        }
        self.raw_totals = {
            lane: {"cost": 0.0, "tokens": 0, "cached": 0, "calls": 0}
            for lane in ("baseline", "managed")
        }
        self.comparison_offsets = {
            lane: {"cost": 0.0, "tokens": 0, "cached": 0, "calls": 0}
            for lane in ("baseline", "managed")
        }
        self.decisions: deque[str] = deque(maxlen=20)
        self.thread_ids: dict[str, str] = {}
        self.baseline_model = baseline_model
        self.event_offset = 0
        self.turn_number = 1
        self.phase = "ready"
        self.status = "Ready for turn 1"
        self.comparison_error: str | None = None
        self.savings_deadline: float | None = None
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._closed = False
        self._lock = threading.RLock()

    def submit(self, prompt: str) -> dict[str, Any]:
        normalized = prompt.strip()
        if not normalized:
            raise ValueError("prompt cannot be empty")
        with self._lock:
            self._advance_savings_if_due()
            if self.phase != "ready":
                raise RuntimeError("both Codex lanes must finish before the next prompt")
            handled, selected_model, model_lines = resolve_model_command(
                normalized,
                self.comparison_models,
                self.baseline_model,
            )
            if handled:
                self.logs["baseline"].extend(model_lines)
                if selected_model != self.baseline_model:
                    self.baseline_model = selected_model
                    for lane in ("baseline", "managed"):
                        for field in ("cost", "tokens", "cached", "calls"):
                            self.comparison_offsets[lane][field] = self.raw_totals[lane][field]
                            self.totals[lane][field] = 0.0 if field == "cost" else 0
                    self.totals["baseline"]["model"] = selected_model
                    self.totals["managed"]["model"] = "selecting"
                    self.decisions.append(f"NEW COMPARISON · {selected_model} vs PromptRail")
                    self.logs["managed"].extend(
                        ("", f"COMPARE PromptRail vs {selected_model}", "")
                    )
                return self.public()
            for lane in self.logs:
                self.logs[lane].extend(("", f"USER    {normalized}", ""))
            self.phase = "running"
            self.comparison_error = None
            self.status = f"Turn {self.turn_number} · launching both Codex lanes"
            threading.Thread(
                target=self._run_turn,
                args=(normalized,),
                daemon=True,
            ).start()
            return self.public()

    def public(self) -> dict[str, Any]:
        with self._lock:
            self._advance_savings_if_due()
            baseline_cost = float(self.totals["baseline"]["cost"])
            managed_cost = float(self.totals["managed"]["cost"])
            percentage = savings_percentage(baseline_cost, managed_cost)
            remaining_ms = 0
            if self.phase == "savings" and self.savings_deadline is not None:
                remaining_ms = max(0, round((self.savings_deadline - time.monotonic()) * 1_000))
            return {
                "phase": self.phase,
                "status": self.status,
                "turn": self.turn_number,
                "logs": {lane: list(lines) for lane, lines in self.logs.items()},
                "totals": {lane: dict(values) for lane, values in self.totals.items()},
                "decision": self.decisions[-1] if self.decisions else None,
                "comparison_error": self.comparison_error,
                "savings": {
                    "percentage": percentage,
                    "amount_usd": baseline_cost - managed_cost,
                    "remaining_ms": remaining_ms,
                },
                "models": [
                    {
                        "model": str(item["model"]),
                        "label": str(item["label"]),
                        "alias": str((item.get("aliases") or [item["model"]])[0]),
                    }
                    for item in self.comparison_models
                ],
                "selected_baseline_model": self.baseline_model,
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True
            processes = tuple(self._processes.values())
        for process in processes:
            if process.poll() is None:
                process.terminate()

    def _advance_savings_if_due(self) -> None:
        if (
            self.phase == "savings"
            and self.savings_deadline is not None
            and time.monotonic() >= self.savings_deadline
        ):
            self.turn_number += 1
            self.phase = "ready"
            self.status = f"Ready for turn {self.turn_number}"
            self.savings_deadline = None

    def _run_turn(self, prompt: str) -> None:
        pending_agent_messages: dict[str, str] = {}
        selector = selectors.DefaultSelector()
        processes: dict[str, subprocess.Popen[str]] = {}
        try:
            for lane, port, workspace, model in (
                ("baseline", self.baseline_port, self.baseline_workspace, self.baseline_model),
                ("managed", self.managed_port, self.managed_workspace, self.default_model),
            ):
                process = launch_codex(
                    codex_command(
                        lane,
                        port,
                        model,
                        workspace,
                        prompt,
                        self.model_catalog,
                        self.thread_ids.get(lane),
                    ),
                    workspace,
                    self.env,
                )
                processes[lane] = process
                assert process.stdout is not None
                selector.register(process.stdout, selectors.EVENT_READ, lane)
            with self._lock:
                self._processes = processes

            while any(process.poll() is None for process in processes.values()):
                for key, _ in selector.select(timeout=0.08):
                    line = key.fileobj.readline()
                    if line:
                        self._consume_codex_line(key.data, line, pending_agent_messages)
                self._consume_ledger()
                with self._lock:
                    states = ", ".join(
                        f"{lane}: {'running' if process.poll() is None else 'done'}"
                        for lane, process in processes.items()
                    )
                    self.status = f"Turn {self.turn_number} · {states}"

            for lane, process in processes.items():
                assert process.stdout is not None
                for line in process.stdout:
                    self._consume_codex_line(lane, line, pending_agent_messages)
            self._consume_ledger()
            return_codes = [process.wait() for process in processes.values()]
            if any(code != 0 for code in return_codes):
                self.comparison_error = (
                    self.comparison_error or "One or both Codex lanes failed this turn"
                )
        except Exception as exc:
            with self._lock:
                self.comparison_error = f"{type(exc).__name__}: {exc}"
                self.logs["managed"].append(f"ERROR   web session | {self.comparison_error}")
            for process in processes.values():
                if process.poll() is None:
                    process.terminate()
        finally:
            selector.close()
            with self._lock:
                self._processes = {}
                self.phase = "savings"
                self.status = (
                    "Comparison stopped"
                    if self.comparison_error
                    else f"Turn {self.turn_number} complete"
                )
                self.savings_deadline = time.monotonic() + self.savings_display_seconds

    def _consume_codex_line(
        self,
        lane: str,
        line: str,
        pending: dict[str, str],
    ) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = None
        with self._lock:
            if isinstance(payload, dict):
                item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
                if item.get("type") == "agent_message":
                    previous = pending.get(lane)
                    if previous:
                        self.logs[lane].extend(("", "UPDATE", *previous.splitlines(), ""))
                    text = terminal_text(item.get("text") or item.get("content"))
                    if text:
                        pending[lane] = text
                    if payload.get("type") == "thread.started" and payload.get("thread_id"):
                        self.thread_ids[lane] = str(payload["thread_id"])
                    return
                previous = pending.pop(lane, None)
                if previous:
                    if payload.get("type") == "turn.completed":
                        self.logs[lane].extend(
                            ("", ANSWER_DIVIDER, "ANSWER", *previous.splitlines(), "")
                        )
                    else:
                        self.logs[lane].extend(("", "UPDATE", *previous.splitlines(), ""))
            rendered, thread_id = render_codex_line(line)
            if thread_id is not None:
                self.thread_ids[lane] = thread_id
            self.logs[lane].extend(item for item in rendered if item)

    def _consume_ledger(self) -> None:
        if not self.events.exists():
            return
        with self._lock, self.events.open("r", encoding="utf-8") as stream:
            stream.seek(self.event_offset)
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                lane = event.get("lane")
                if event.get("totals") and lane in self.raw_totals:
                    self.raw_totals[lane].update(event["totals"])
                    for field in ("cost", "tokens", "cached", "calls"):
                        self.totals[lane][field] = (
                            self.raw_totals[lane][field]
                            - self.comparison_offsets[lane][field]
                        )
                event_type = event.get("event")
                if event_type == "catalog_loaded":
                    self.decisions.append(
                        f"PromptRail catalog: {event['candidate_count']} MongoDB tool models"
                    )
                elif event_type == "ranker_shortlist":
                    self.decisions.append(
                        f"PromptRail ranking {event['candidate_count']} of "
                        f"{event['catalog_count']} eligible models"
                    )
                elif event_type == "decision":
                    self.totals["managed"]["model"] = event["model"]
                    self.decisions.append(
                        f"ROUTE {event['model']} | compacted "
                        f"{event['compacted_tokens']:,} tok | cache "
                        f"{event['cached_tokens']:,} tok"
                    )
                elif event_type in {"rate_limit_reroute", "provider_error_reroute"}:
                    self.decisions.append(
                        f"FAILOVER HTTP {event.get('status', 429)} | "
                        f"{event['previous_model']} -> {event['model']}"
                    )
                if (
                    event_type == "usage"
                    and event.get("purpose") == "agent"
                    and lane in self.totals
                ):
                    self.totals[lane]["model"] = event.get("model", "unknown")
                ledger_line = render_ledger_event(event)
                if ledger_line and lane in self.logs:
                    self.logs[lane].append(ledger_line)
                error = render_ledger_error(event)
                if error:
                    self.comparison_error = self.comparison_error or error
            self.event_offset = stream.tell()
