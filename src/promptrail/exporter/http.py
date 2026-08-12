from __future__ import annotations

import gzip
import http.client
import ssl
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ExportResponse:
    status: int
    retryable: bool


class ExportError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class HTTPSender:
    """Persistent stdlib HTTP(S) sender with TLS validation and keep-alive."""

    RETRYABLE_STATUSES: ClassVar[set[int]] = {408, 425, 429, 500, 502, 503, 504}

    def __init__(self, config: object, path: str = "/v1/runtime/events") -> None:
        endpoint = getattr(config, "endpoint", None) or getattr(
            config, "runtime_events_endpoint", None
        )
        if not endpoint:
            raise ValueError("runtime exporter endpoint is required")
        self._url = urlsplit(endpoint)
        if self._url.scheme not in {"https", "http"} or not self._url.netloc:
            raise ValueError("runtime exporter endpoint must be an http(s) URL")
        self._path = self._url.path or path
        if self._url.query:
            self._path += "?" + self._url.query
        self._timeout = float(getattr(config, "export_timeout", 10.0))
        self._api_key = getattr(config, "api_key", None)
        self._gzip = bool(getattr(config, "export_gzip", False))
        self._sdk_version = getattr(config, "sdk_version", "unknown")
        self._conn: http.client.HTTPConnection | None = None

    def _connect(self) -> http.client.HTTPConnection:
        if self._conn is not None:
            return self._conn
        if self._url.scheme == "https":
            context = ssl.create_default_context()
            self._conn = http.client.HTTPSConnection(
                self._url.hostname, self._url.port, timeout=self._timeout, context=context
            )
        else:
            self._conn = http.client.HTTPConnection(
                self._url.hostname, self._url.port, timeout=self._timeout
            )
        return self._conn

    def send(self, body: bytes, headers: Mapping[str, str] | None = None) -> ExportResponse:
        out = body
        req_headers = {
            "Content-Type": "application/json",
            "User-Agent": f"promptrail-python/{self._sdk_version}",
            "Connection": "keep-alive",
        }
        if self._api_key:
            req_headers["Authorization"] = f"Bearer {self._api_key}"
        if headers:
            req_headers.update(headers)
        if self._gzip and len(body) >= 512:
            out = gzip.compress(body)
            req_headers["Content-Encoding"] = "gzip"
        req_headers["Content-Length"] = str(len(out))
        try:
            conn = self._connect()
            conn.request("POST", self._path, body=out, headers=req_headers)
            response = conn.getresponse()
            response.read()
            retryable = response.status in self.RETRYABLE_STATUSES
            if not 200 <= response.status < 300:
                if response.status >= 500 or retryable:
                    raise ExportError(
                        f"runtime export failed with HTTP {response.status}", retryable=retryable
                    )
                raise ExportError(
                    f"runtime export rejected with HTTP {response.status}", retryable=False
                )
            return ExportResponse(response.status, retryable=False)
        except ExportError:
            raise
        except Exception as exc:
            self.close()
            raise ExportError(
                f"runtime export transport error: {type(exc).__name__}", retryable=True
            ) from None

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with suppress(Exception):
                conn.close()


__all__ = ["ExportError", "ExportResponse", "HTTPSender"]
