"""Optional client integrations for runtime correlation."""

from .http import async_httpx_request_hook, httpx_request_hook
from .openai import wrap_openai

__all__ = ["async_httpx_request_hook", "httpx_request_hook", "wrap_openai"]
