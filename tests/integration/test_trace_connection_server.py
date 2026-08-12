from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("demo").resolve()))

from connect_server import TraceConnectionServer


@pytest.fixture
def trace_server():
    server = TraceConnectionServer(("127.0.0.1", 0))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _post(base: str, path: str, payload: bytes) -> tuple[int, dict]:
    request = urllib.request.Request(
        base + path,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-PromptRail-Privacy-Mode": "metadata_only",
        },
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return response.status, json.load(response)


def test_interface_assets_and_sdk_capabilities_are_served(trace_server: str) -> None:
    for path, content_type in (
        ("/connect", "text/html"),
        ("/connect.css", "text/css"),
        ("/connect.js", "text/javascript"),
    ):
        with urllib.request.urlopen(trace_server + path) as response:
            assert response.status == 200
            assert content_type in response.headers["Content-Type"]
    with urllib.request.urlopen(trace_server + "/api/trace-sources") as response:
        payload = json.load(response)
    assert payload["sdk"]["trace_processor"] == "PromptRailSpanProcessor"


def test_source_configuration_and_trace_import_use_sdk_contract(trace_server: str) -> None:
    status, configured = _post(
        trace_server,
        "/api/trace-sources/connect",
        json.dumps(
            {
                "source": "langfuse",
                "credential": "read-only-secret",
                "project": "production",
                "metadata_only": True,
            }
        ).encode(),
    )
    assert status == 202
    assert configured["status"] == "configured"
    assert "credential" not in json.dumps(configured)

    status, imported = _post(
        trace_server,
        "/api/traces/import",
        json.dumps(
            {
                "spans": [
                    {
                        "trace_id": "abc",
                        "span_id": "def",
                        "name": "openai chat",
                        "attributes": {"gen_ai.system": "openai", "prompt": "private"},
                    }
                ]
            }
        ).encode(),
    )
    assert status == 202
    assert imported["import"]["accepted_events"] == 1
    assert imported["import"]["llm_call_count"] == 1


@pytest.mark.parametrize(
    ("path", "payload"),
    [("/api/trace-sources/connect", b"{}"), ("/api/traces/import", b"not-json")],
)
def test_invalid_requests_return_actionable_400(
    trace_server: str, path: str, payload: bytes
) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        _post(trace_server, path, payload)
    assert error.value.code == 400
    assert json.loads(error.value.read())["error"]
