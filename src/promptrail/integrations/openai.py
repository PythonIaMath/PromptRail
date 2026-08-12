"""Stable, optional wrapper for official OpenAI-compatible Python clients."""

from __future__ import annotations

import contextlib
import functools
import inspect
from collections.abc import Callable, Generator, Mapping
from typing import Any

from ..context import run
from ..propagation.headers import inject_headers, is_promptrail_gateway
from ..sdk import current_runtime_context, get_runtime_client
from ..tracing.opentelemetry import start_as_current_span
from ..utils.logging import debug

_TARGET_METHODS = {
    "chat.completions.create": "chat",
    "completions.create": "completion",
    "responses.create": "chat",
    "embeddings.create": "embeddings",
}


class _OpenAIProxy:
    __slots__ = ("_base_url", "_path", "_target")

    def __init__(self, target: Any, path: str, base_url: object) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_base_url", base_url)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._target, name)
        path = f"{self._path}.{name}" if self._path else name
        operation = _TARGET_METHODS.get(path)
        if operation is not None and callable(target):
            return _wrap_create(target, path=path, operation=operation, base_url=self._base_url)
        if any(candidate.startswith(path + ".") for candidate in _TARGET_METHODS):
            return _OpenAIProxy(target, path, self._base_url)
        return target

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._target, name, value)

    def __repr__(self) -> str:
        return repr(self._target)

    def __enter__(self) -> _OpenAIProxy:
        self._target.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        return self._target.__exit__(exc_type, exc, tb)

    async def __aenter__(self) -> _OpenAIProxy:
        await self._target.__aenter__()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        return await self._target.__aexit__(exc_type, exc, tb)


def wrap_openai(client: Any) -> Any:
    """Wrap an OpenAI or AsyncOpenAI client with current per-call metadata.

    Direct model-provider clients are returned unchanged. PromptRail does not
    monkey-patch OpenAI internals, and the wrapper delegates all non-target
    resources and methods to the original client.
    """

    if isinstance(client, _OpenAIProxy):
        return client
    try:
        base_url = client.base_url
    except Exception:
        base_url = None
    if not is_promptrail_gateway(base_url):
        return client
    return _OpenAIProxy(client, "", base_url)


def _wrap_create(
    function: Callable[..., Any],
    *,
    path: str,
    operation: str,
    base_url: object,
) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(function):

        @functools.wraps(function)
        async def async_call(*args: Any, **kwargs: Any) -> Any:
            with _request_run_scope(), _safe_llm_span(path, operation, kwargs):
                _apply_headers(kwargs, base_url)
                return await function(*args, **kwargs)

        return async_call

    @functools.wraps(function)
    def sync_call(*args: Any, **kwargs: Any) -> Any:
        with _request_run_scope(), _safe_llm_span(path, operation, kwargs):
            _apply_headers(kwargs, base_url)
            return function(*args, **kwargs)

    return sync_call


@contextlib.contextmanager
def _request_run_scope() -> Generator[None, None, None]:
    try:
        has_run = current_runtime_context().run_id is not None
    except Exception:
        has_run = False
    if has_run:
        yield
        return
    with run():
        yield


def _llm_span(path: str, operation: str, kwargs: Mapping[str, Any]) -> Any:
    attributes: dict[str, str] = {
        "gen_ai.operation.name": operation,
        "gen_ai.system": "openai",
        "promptrail.span.kind": "llm",
    }
    model = kwargs.get("model")
    if isinstance(model, str):
        attributes["gen_ai.request.model"] = model[:256]
    options: dict[str, Any] = {
        "attributes": attributes,
        "record_exception": True,
        "set_status_on_exception": True,
    }
    try:
        from opentelemetry.trace import SpanKind

        options["kind"] = SpanKind.CLIENT
    except Exception:
        pass
    return start_as_current_span(f"openai.{path}", **options)


@contextlib.contextmanager
def _safe_llm_span(
    path: str, operation: str, kwargs: Mapping[str, Any]
) -> Generator[None, None, None]:
    try:
        manager = _llm_span(path, operation, kwargs)
        manager.__enter__()
    except Exception:
        yield
        return
    try:
        yield
    except BaseException as app_error:
        with contextlib.suppress(Exception):
            manager.__exit__(type(app_error), app_error, app_error.__traceback__)
        raise
    else:
        with contextlib.suppress(Exception):
            manager.__exit__(None, None, None)


def _apply_headers(kwargs: dict[str, Any], base_url: object) -> None:
    original = kwargs.get("extra_headers")
    existing = original if isinstance(original, Mapping) else None
    try:
        kwargs["extra_headers"] = inject_headers(existing, url=base_url, ensure_run=True)
    except Exception:
        return
    client = get_runtime_client()
    debug("gateway metadata injected", enabled=bool(client and client.config.debug))


__all__ = ["wrap_openai"]
