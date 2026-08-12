"""Standalone local server for the SDK-connected trace source interface."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from .trace_connections import TraceConnectionService
except ImportError:
    from trace_connections import TraceConnectionService

STATIC_ROOT = Path(__file__).resolve().parent / "web"


class TraceConnectionServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        self.trace_connections = TraceConnectionService()
        super().__init__(address, TraceConnectionHandler)


class TraceConnectionHandler(BaseHTTPRequestHandler):
    server: TraceConnectionServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/trace-sources":
            self._json(self.server.trace_connections.capabilities())
            return
        assets = {
            "/": ("connect.html", "text/html; charset=utf-8"),
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
        self.send_error(HTTPStatus.NOT_FOUND)

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

    def _json(self, value: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the PromptRail trace connection interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("the trace connection preview is local-only")
    server = TraceConnectionServer((args.host, args.port))
    print(
        f"PromptRail trace connections: http://{args.host}:{server.server_address[1]}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
