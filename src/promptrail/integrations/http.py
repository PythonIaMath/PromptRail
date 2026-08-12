"""Generic HTTP request hooks for PromptRail gateway correlation."""

from __future__ import annotations

from typing import Any

from ..propagation.headers import inject_headers


def httpx_request_hook(request: Any) -> None:
    """Inject current metadata into an httpx request event hook, fail-open."""

    try:
        headers = inject_headers(dict(request.headers), url=request.url)
        request.headers.clear()
        request.headers.update(headers)
    except Exception:
        return


async def async_httpx_request_hook(request: Any) -> None:
    """Async httpx event-hook form of :func:`httpx_request_hook`."""

    httpx_request_hook(request)


__all__ = ["async_httpx_request_hook", "httpx_request_hook"]
