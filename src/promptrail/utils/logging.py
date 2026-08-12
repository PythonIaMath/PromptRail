"""Secret-safe debug logging."""

from __future__ import annotations

import logging
import re
from typing import Any

_LOGGER = logging.getLogger("promptrail.runtime")
_SECRET_PATTERNS = [
    re.compile(r"(pr_(?:live|test)_[A-Za-z0-9_\-]+)"),
    re.compile(r"(sk-[A-Za-z0-9_\-]+)"),
    re.compile(r"(?i)(api[_-]?key|authorization|token|secret|password)=([^\s,;]+)"),
]


def sanitize_message(message: object) -> str:
    text = str(message)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: f"{m.group(1)}=<redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)
    return text[:1000]


def debug(message: object, *args: Any, enabled: bool = False, **kwargs: Any) -> None:
    """Log a sanitized debug message only when explicitly enabled."""
    if not enabled:
        return
    try:
        safe_args = tuple(sanitize_message(arg) for arg in args)
        _LOGGER.debug(sanitize_message(message), *safe_args, **kwargs)
    except Exception:
        return
