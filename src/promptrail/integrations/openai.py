"""Stable, optional wrapper for official OpenAI-compatible Python clients."""

from __future__ import annotations

import contextlib
import functools
import inspect
from collections.abc import AsyncIterator, Callable, Generator, Iterator, Mapping
from typing import Any

from ..context import run
from ..propagation.headers import inject_headers, is_promptrail_gateway
from ..sdk import current_runtime_context, get_runtime_client
from ..tracing.opentelemetry import start_as_current_span
from ..utils.logging import debug

_TARGET_METHODS = {
    "chat.completions.create": "chat",
    "chat.completions.with_raw_response.create": "chat",
    "chat.completions.with_streaming_response.create": "chat",
    "completions.create": "completion",
    "completions.with_raw_response.create": "completion",
    "completions.with_streaming_response.create": "completion",
    "responses.create": "chat",
    "responses.stream": "chat",
    "responses.with_raw_response.create": "chat",
    "responses.with_streaming_response.create": "chat",
    "embeddings.create": "embeddings",
    "embeddings.with_raw_response.create": "embeddings",
}


class _OpenAIProxy:
    __slots__ = ("_client", "_path", "_target")

    def __init__(self, target: Any, path: str, client: Any) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_client", client)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self._target, name)
        path = f"{self._path}.{name}" if self._path else name
        operation = _TARGET_METHODS.get(path)
        if operation is not None and callable(target):
            return _wrap_create(target, path=path, operation=operation, client=self._client)
        if any(candidate.startswith(path + ".") for candidate in _TARGET_METHODS):
            return _OpenAIProxy(target, path, self._client)
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
    return _OpenAIProxy(client, "", client)


def _wrap_create(
    function: Callable[..., Any],
    *,
    path: str,
    operation: str,
    client: Any,
) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(function):

        @functools.wraps(function)
        async def async_call(*args: Any, **kwargs: Any) -> Any:
            base_url = _current_base_url(client)
            if not is_promptrail_gateway(base_url):
                return await function(*args, **kwargs)
            stack = contextlib.ExitStack()
            stack.enter_context(_request_run_scope())
            stack.enter_context(_safe_llm_span(path, operation, kwargs))
            try:
                _apply_headers(kwargs, base_url)
                result = await function(*args, **kwargs)
            except BaseException as app_error:
                _exit_stack(stack, app_error)
                raise
            if _is_streaming_path(path) and hasattr(result, "__aenter__"):
                return _AsyncContextProxy(result, stack)
            if _is_async_stream(result, kwargs):
                return _AsyncStreamProxy(result, stack)
            _close_stack(stack)
            return result

        return async_call

    @functools.wraps(function)
    def sync_call(*args: Any, **kwargs: Any) -> Any:
        base_url = _current_base_url(client)
        if not is_promptrail_gateway(base_url):
            return function(*args, **kwargs)
        stack = contextlib.ExitStack()
        stack.enter_context(_request_run_scope())
        stack.enter_context(_safe_llm_span(path, operation, kwargs))
        try:
            _apply_headers(kwargs, base_url)
            result = function(*args, **kwargs)
        except BaseException as app_error:
            _exit_stack(stack, app_error)
            raise
        if _is_streaming_path(path) and hasattr(result, "__enter__"):
            return _SyncContextProxy(result, stack)
        if _is_sync_stream(result, kwargs):
            return _SyncStreamProxy(result, stack)
        _close_stack(stack)
        return result

    return sync_call


def _current_base_url(client: Any) -> object | None:
    try:
        return client.base_url
    except Exception:
        return None


def _is_sync_stream(value: Any, kwargs: Mapping[str, Any]) -> bool:
    return bool(kwargs.get("stream")) and hasattr(value, "__iter__") and hasattr(value, "__next__")


def _is_async_stream(value: Any, kwargs: Mapping[str, Any]) -> bool:
    return (
        bool(kwargs.get("stream")) and hasattr(value, "__aiter__") and hasattr(value, "__anext__")
    )


def _is_streaming_path(path: str) -> bool:
    return "with_streaming_response" in path or path.endswith(".stream")


def _close_stack(stack: contextlib.ExitStack) -> None:
    with contextlib.suppress(Exception):
        stack.close()


def _exit_stack(stack: contextlib.ExitStack, error: BaseException) -> None:
    with contextlib.suppress(Exception):
        stack.__exit__(type(error), error, error.__traceback__)


class _SyncContextProxy:
    def __init__(self, manager: Any, stack: contextlib.ExitStack) -> None:
        self._manager = manager
        self._stack = stack

    def __enter__(self) -> Any:
        try:
            return self._manager.__enter__()
        except BaseException as app_error:
            _exit_stack(self._stack, app_error)
            raise

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        try:
            return self._manager.__exit__(exc_type, exc, tb)
        finally:
            if exc is None:
                _close_stack(self._stack)
            else:
                _exit_stack(self._stack, exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)


class _AsyncContextProxy:
    def __init__(self, manager: Any, stack: contextlib.ExitStack) -> None:
        self._manager = manager
        self._stack = stack

    async def __aenter__(self) -> Any:
        try:
            return await self._manager.__aenter__()
        except BaseException as app_error:
            _exit_stack(self._stack, app_error)
            raise

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        try:
            return await self._manager.__aexit__(exc_type, exc, tb)
        finally:
            if exc is None:
                _close_stack(self._stack)
            else:
                _exit_stack(self._stack, exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)


class _SyncStreamProxy(Iterator[Any]):
    def __init__(self, stream: Any, stack: contextlib.ExitStack) -> None:
        self._stream = stream
        self._stack = stack
        self._closed = False

    def __iter__(self) -> _SyncStreamProxy:
        return self

    def __next__(self) -> Any:
        try:
            return next(self._stream)
        except StopIteration:
            self.close()
            raise
        except BaseException as app_error:
            self._exit_with_error(app_error)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            closer = getattr(self._stream, "close", None)
            if callable(closer):
                closer()
        finally:
            _close_stack(self._stack)

    def _exit_with_error(self, error: BaseException) -> None:
        if self._closed:
            return
        self._closed = True
        _exit_stack(self._stack, error)


class _AsyncStreamProxy(AsyncIterator[Any]):
    def __init__(self, stream: Any, stack: contextlib.ExitStack) -> None:
        self._stream = stream
        self._stack = stack
        self._closed = False

    def __aiter__(self) -> _AsyncStreamProxy:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self._stream.__anext__()
        except StopAsyncIteration:
            await self.aclose()
            raise
        except BaseException as app_error:
            self._exit_with_error(app_error)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            closer = getattr(self._stream, "close", None)
            if callable(closer):
                result = closer()
                if inspect.isawaitable(result):
                    await result
        finally:
            _close_stack(self._stack)

    def _exit_with_error(self, error: BaseException) -> None:
        if self._closed:
            return
        self._closed = True
        _exit_stack(self._stack, error)


@contextlib.contextmanager
def _request_run_scope() -> Generator[None, None, None]:
    try:
        current = current_runtime_context()
    except Exception:
        current = None
    if current is not None and current.run_id is not None:
        yield
        return
    if current is not None and current.trace_id is not None:
        try:
            ensured = current_runtime_context(ensure_run=True)
        except Exception:
            ensured = None
        if ensured is not None and ensured.run_id is not None:
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
