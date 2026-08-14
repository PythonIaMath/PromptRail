from __future__ import annotations

import http.client
import json
import time
from threading import Thread
from typing import Any

from demo.web import DemoWebServer
from demo.web_session import ComparisonSession


class FakeComparisonSession:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.closed = False

    def public(self) -> dict[str, Any]:
        return {
            "phase": "ready",
            "status": "Ready for turn 1",
            "turn": 1,
            "logs": {"baseline": [], "managed": []},
            "totals": {
                "baseline": {
                    "cost": 0.0,
                    "tokens": 0,
                    "cached": 0,
                    "calls": 0,
                    "model": "openai/gpt-5.6-sol",
                },
                "managed": {
                    "cost": 0.0,
                    "tokens": 0,
                    "cached": 0,
                    "calls": 0,
                    "model": "selecting",
                },
            },
            "decision": None,
            "comparison_error": None,
            "savings": {"percentage": None, "amount_usd": 0.0, "remaining_ms": 0},
            "models": [
                {"model": "openai/gpt-5.6-terra", "label": "GPT Terra", "alias": "terra"}
            ],
            "selected_baseline_model": "openai/gpt-5.6-sol",
        }

    def submit(self, prompt: str) -> dict[str, Any]:
        self.prompts.append(prompt)
        state = self.public()
        state["phase"] = "running"
        state["status"] = "Turn 1 · baseline: running, managed: running"
        return state

    def close(self) -> None:
        self.closed = True


def request(
    server: DemoWebServer,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=2)
    encoded = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if encoded is not None else {}
    connection.request(method, path, body=encoded, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, payload


def test_web_interface_serves_split_codex_terminal_and_submits_turn() -> None:
    session = FakeComparisonSession()
    server = DemoWebServer(("127.0.0.1", 0), session)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, page = request(server, "GET", "/")
        assert status == 200
        assert b"CODEX \xc2\xb7 BASELINE" in page
        assert b"CODEX \xc2\xb7 PROMPTRAIL" in page
        assert b"PROMPTRAIL SAVINGS" in page

        status, payload = request(
            server,
            "POST",
            "/api/turn",
            {"prompt": "what is this codebase?"},
        )
        assert status == 202
        state = json.loads(payload)
        assert state["phase"] == "running"
        assert session.prompts == ["what is this codebase?"]

        status, payload = request(server, "GET", "/api/state")
        assert status == 200
        assert json.loads(payload)["selected_baseline_model"] == "openai/gpt-5.6-sol"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_web_interface_passes_model_command_to_same_session() -> None:
    session = FakeComparisonSession()
    server = DemoWebServer(("127.0.0.1", 0), session)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _ = request(server, "POST", "/api/turn", {"prompt": "/model terra"})
        assert status == 202
        assert session.prompts == ["/model terra"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_comparison_session_returns_to_prompt_after_savings_deadline() -> None:
    session = object.__new__(ComparisonSession)
    session.phase = "savings"
    session.savings_deadline = time.monotonic() - 1
    session.turn_number = 3
    session.status = "Turn 3 complete"

    session._advance_savings_if_due()

    assert session.phase == "ready"
    assert session.turn_number == 4
    assert session.status == "Ready for turn 4"
    assert session.savings_deadline is None
