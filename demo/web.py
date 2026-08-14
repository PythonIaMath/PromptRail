"""Local browser interface for the real side-by-side PromptRail Codex demo."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

try:
    from .trace_connections import TraceConnectionService
    from .web_session import ComparisonSession
except ImportError:  # Direct script execution from the repository root.
    from trace_connections import TraceConnectionService
    from web_session import ComparisonSession

DEMO_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = DEMO_ROOT.parent
STATIC_ROOT = DEMO_ROOT / "web"


class SessionProtocol(Protocol):
    def public(self) -> dict[str, Any]: ...

    def submit(self, prompt: str) -> dict[str, Any]: ...

    def close(self) -> None: ...


class DemoWebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], session: SessionProtocol) -> None:
        self.session = session
        self.trace_connections = TraceConnectionService()
        super().__init__(address, DemoWebHandler)


class DemoWebHandler(BaseHTTPRequestHandler):
    server: DemoWebServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self._json(self.server.session.public())
            return
        if path == "/api/trace-sources":
            self._json(self.server.trace_connections.capabilities())
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/connect": ("connect.html", "text/html; charset=utf-8"),
            "/connect.html": ("connect.html", "text/html; charset=utf-8"),
            "/connect.css": ("connect.css", "text/css; charset=utf-8"),
            "/connect.js": ("connect.js", "text/javascript; charset=utf-8"),
        }
        asset = assets.get(path)
        if asset is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        filename, content_type = asset
        body = (STATIC_ROOT / filename).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/trace-sources/connect":
            self._connect_trace_source()
            return
        if path == "/api/traces/import":
            self._import_traces()
            return
        if path != "/api/turn":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 65_536:
                raise ValueError("prompt is too large")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict) or not isinstance(payload.get("prompt"), str):
                raise ValueError("request must contain a prompt string")
            state = self.server.session.submit(payload["prompt"])
        except RuntimeError as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.CONFLICT)
            return
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._json(state, status=HTTPStatus.ACCEPTED)

    def _connect_trace_source(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65_536:
                raise ValueError("invalid connection request size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("connection request must be a JSON object")
            result = self.server.trace_connections.connect(payload)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._json(result, status=HTTPStatus.ACCEPTED)

    def _import_traces(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("trace import is empty")
            if length > self.server.trace_connections.max_import_bytes:
                raise ValueError("trace import exceeds 50 MB")
            metadata_only = self.headers.get("X-PromptRail-Privacy-Mode") != "content"
            result = self.server.trace_connections.import_json(
                self.rfile.read(length), metadata_only=metadata_only
            )
        except (UnicodeDecodeError, ValueError) as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._json(result, status=HTTPStatus.ACCEPTED)

    def _json(self, value: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def start_backend(args: argparse.Namespace, events: Path) -> subprocess.Popen[str]:
    demo_python = DEMO_ROOT / ".venv" / "bin" / "python"
    command = [
        str(demo_python) if demo_python.exists() else sys.executable,
        str(DEMO_ROOT / "backend.py"),
        "--events",
        str(events),
        "--baseline-port",
        str(args.baseline_port),
        "--managed-port",
        str(args.managed_port),
    ]
    if args.env_file:
        command.extend(("--env-file", args.env_file))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=PROJECT_ROOT,
    )
    assert process.stdout is not None
    ready = process.stdout.readline().strip()
    if process.poll() is not None or '"status": "ready"' not in ready:
        remainder = process.stdout.read()
        raise RuntimeError(f"Demo backend failed to start:\n{ready}\n{remainder}")
    return process


def stop_process(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> int:
    config = json.loads((DEMO_ROOT / "config.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description="Run PromptRail's local browser demo")
    parser.add_argument("--workspace", default=str(PROJECT_ROOT))
    parser.add_argument("--env-file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--baseline-port", type=int, default=8765)
    parser.add_argument("--managed-port", type=int, default=8766)
    parser.add_argument(
        "--baseline-model",
        default=os.getenv("PROMPTRAIL_DEMO_BASELINE_MODEL", config["baseline_model"]),
    )
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("the Codex comparison is local-only; use 127.0.0.1 or localhost")
    if shutil.which("codex") is None:
        raise SystemExit("codex is not installed")
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
    DEMO_ROOT.joinpath("runs").mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="promptrail-web-",
        suffix=".jsonl",
        delete=False,
    ) as event_handle:
        events = Path(event_handle.name)
    backend: subprocess.Popen[str] | None = None
    session: ComparisonSession | None = None
    try:
        backend = start_backend(args, events)
        session = ComparisonSession(
            demo_root=DEMO_ROOT,
            workspace=Path(args.workspace),
            events=events,
            baseline_port=args.baseline_port,
            managed_port=args.managed_port,
            baseline_model=args.baseline_model,
            comparison_models=tuple(comparison_models),
            savings_display_seconds=float(config["savings_display_seconds"]),
        )
        server = DemoWebServer((args.host, args.port), session)
        link = f"http://{args.host}:{server.server_address[1]}"
        print(f"PromptRail Codex comparison: {link}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        if session is not None:
            session.close()
        if backend is not None:
            stop_process(backend)
        events.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
