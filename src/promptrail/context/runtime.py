"""Context-local runtime identity and run scopes."""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Callable, Generator
from concurrent.futures import Executor, Future
from dataclasses import dataclass, replace
from types import TracebackType
from typing import Any

from promptrail.config import RuntimeConfig
from promptrail.utils.ids import secure_id
from promptrail.utils.logging import debug


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    user_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeLifecycleCallbacks:
    on_run_start: Callable[[RuntimeContext], None] | None = None
    on_run_end: Callable[[RuntimeContext, BaseException | None], None] | None = None


_runtime_context: contextvars.ContextVar[RuntimeContext | None] = contextvars.ContextVar(
    "promptrail_runtime_context", default=None
)
_contextual_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "promptrail_contextual_user_id", default=None
)
_config: RuntimeConfig | None = None
_lifecycle_callbacks: RuntimeLifecycleCallbacks = RuntimeLifecycleCallbacks()


def set_runtime_config(config: RuntimeConfig | None) -> None:
    global _config
    _config = config


def current_runtime_config() -> RuntimeConfig | None:
    return _config


def set_lifecycle_callbacks(
    callbacks: RuntimeLifecycleCallbacks | None = None, **kwargs: Any
) -> None:
    """Install lazy lifecycle callbacks without importing the SDK facade."""
    global _lifecycle_callbacks
    _lifecycle_callbacks = callbacks or RuntimeLifecycleCallbacks(**kwargs)


def current_runtime_context() -> RuntimeContext:
    existing = _runtime_context.get()
    if existing is not None:
        return existing
    return RuntimeContext(user_id=current_user_id())


def current_run_id() -> str | None:
    return current_runtime_context().run_id


def current_user_id() -> str | None:
    existing = _runtime_context.get()
    if existing and existing.user_id:
        return existing.user_id
    contextual = _contextual_user_id.get()
    if contextual:
        return contextual
    config = _config
    if config is None:
        return None
    try:
        return config.resolve_user_id()
    except Exception as exc:
        debug(f"user resolver failed: {type(exc).__name__}", enabled=config.debug)
        return None


def current_trace_id() -> str | None:
    return current_runtime_context().trace_id


def current_span_id() -> str | None:
    return current_runtime_context().span_id


def current_parent_span_id() -> str | None:
    return current_runtime_context().parent_span_id


@contextlib.contextmanager
def contextual_user(user_id: str | None) -> Generator[None, None, None]:
    token = _contextual_user_id.set(user_id)
    try:
        yield
    finally:
        _contextual_user_id.reset(token)


class RunContext:
    def __init__(
        self,
        *,
        user_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> None:
        parent = current_runtime_context()
        self.context = RuntimeContext(
            user_id=user_id or parent.user_id or current_user_id(),
            run_id=run_id or secure_id("run"),
            trace_id=trace_id or parent.trace_id,
            span_id=span_id or parent.span_id,
            parent_span_id=parent_span_id or parent.parent_span_id,
        )
        self._token: contextvars.Token[RuntimeContext | None] | None = None
        self._exc: BaseException | None = None

    def __enter__(self) -> RuntimeContext:
        self._token = _runtime_context.set(self.context)
        self._safe_start()
        return self.context

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self._exc = exc
        self._safe_end(exc)
        if self._token is not None:
            _runtime_context.reset(self._token)
        return False

    async def __aenter__(self) -> RuntimeContext:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return self.__exit__(exc_type, exc, tb)

    def _safe_start(self) -> None:
        try:
            callback = _lifecycle_callbacks.on_run_start
            if callback:
                callback(self.context)
        except Exception as exc:
            config = _config
            debug(
                f"run start callback failed: {type(exc).__name__}",
                enabled=bool(config and config.debug),
            )

    def _safe_end(self, exc: BaseException | None) -> None:
        try:
            callback = _lifecycle_callbacks.on_run_end
            if callback:
                callback(self.context, exc)
        except Exception as callback_exc:
            config = _config
            debug(
                f"run end callback failed: {type(callback_exc).__name__}",
                enabled=bool(config and config.debug),
            )


def run(**kwargs: Any) -> RunContext:
    return RunContext(**kwargs)


def bind_runtime_context(context: RuntimeContext) -> contextvars.Token[RuntimeContext | None]:
    return _runtime_context.set(context)


def reset_runtime_context(token: contextvars.Token[RuntimeContext | None]) -> None:
    _runtime_context.reset(token)


def clear_runtime_context() -> None:
    _runtime_context.set(None)


def ensure_implicit_run() -> RuntimeContext:
    current = current_runtime_context()
    if current.run_id:
        return current
    implicit = replace(current, run_id=secure_id("run"))
    _runtime_context.set(implicit)
    return implicit


def copy_context() -> contextvars.Context:
    return contextvars.copy_context()


def submit_with_context(
    executor: Executor, fn: Callable[..., Any], /, *args: Any, **kwargs: Any
) -> Future[Any]:
    ctx = copy_context()
    return executor.submit(ctx.run, fn, *args, **kwargs)
