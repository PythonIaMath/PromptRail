from __future__ import annotations

import fcntl
import hashlib
import http.client
import json
import logging
import math
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import certifi
from hermes_cli.middleware import MiddlewareFailClosedError

logger = logging.getLogger(__name__)

HERMES_HOME = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
STATE_PATH = HERMES_HOME / 'lerouter-user-managed' / 'state.json'
SESSION_ID = os.environ.get('HERMES_SESSION_ID') or f'hermes-{uuid.uuid4().hex[:12]}'
SESSION_LEDGER_SUFFIX = hashlib.sha256(SESSION_ID.encode('utf-8')).hexdigest()[:16]
EVENTS_PATH = STATE_PATH.parent / f'events-{SESSION_LEDGER_SUFFIX}.jsonl'
EVENTX_PATH = STATE_PATH.parent / f'eventx-{SESSION_LEDGER_SUFFIX}.json'
USAGE_PATH = STATE_PATH.parent / f'usage-{SESSION_LEDGER_SUFFIX}.jsonl'
_LOCK = threading.Lock()
_ROUTING_CALL_COUNTER = 0
_LAST_VISIBLE_ROUTING_KEY: tuple[str | None, str | None, str | None] | None = None
_HTTP_CONNECTIONS: dict[tuple[str, str, int], http.client.HTTPConnection] = {}
_HTTP_LOCK = threading.Lock()
ROUTER_SELECTION_SCORE_FIELDS = (
    'model_lab',
    'biencoder_source',
    'biencoder_rank',
    'biencoder_score',
    'biencoder_probability',
    'candidate_optimizer_score',
    'switch_cost_penalty',
    'prompt_cache_loss_usd',
    'continued_model_cache_savings_usd',
    'cache_pricing_available',
    'cache_stickiness_bonus',
    'cache_stickiness_bonus_multiplier',
    'cacheable_input_tokens',
    'cached_input_price_difference_per_million',
    'budget_malus',
    'request_budget_usd',
    'expected_price_usd',
    'request_weight',
    'final_score',
    'quality',
)
SIGNED_ACCOUNTING_PROFILE_FIELDS = (
    'input_price_per_million',
    'output_price_per_million',
    'input_cache_read_usd_per_million',
    'input_cache_write_usd_per_million',
)
ROUTER_EXECUTION_CAPABILITY_FIELDS = (
    'supports_tools',
    'supports_json',
    'supports_reasoning_effort',
)
SECRET_ENV_MARKERS = ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD')
OUTPUT_TOKEN_KEYS = ('max_tokens', 'max_completion_tokens', 'max_output_tokens')
OPENAI_PROMPT_CACHE_KEY_MAX_CHARS = 64


class CandidateCapabilityMismatchError(RuntimeError):
    pass


RETRYABLE_EXECUTION_MARKERS = (
    'provider/network',
    'network error',
    'connection reset',
    'timed out',
    'timeout',
    'rate limit',
    '429',
    '503',
    'unavailable model',
    'model unavailable',
    'authentication',
    'unauthorized',
    'forbidden',
    'credit balance',
    'insufficient credit',
    'account balance',
    'payment required',
    'quota exceeded',
    '402',
    'unsupported parameter',
    'unsupported tool',
    'tool_choice',
    'unsupported reasoning',
    'unsupported json',
    'activation failed',
    'empty response',
    'malformed response',
    'mid-tool-call stream drop',
    'stream drop',
    'incomplete stream',
    'truncated response',
    'invalid response',
    'invalid json data',
    'deserialize the json body',
)


def _canonical_request_payload(request: dict[str, Any]) -> dict[str, Any]:
    return {
        key: request.get(key)
        for key in (
            'messages', 'prompt', 'input', 'tools', 'tool_choice',
            'response_format', 'temperature', 'max_tokens', 'stream',
        )
        if key in request
    }


def _semantic_request_hash(request: dict[str, Any]) -> str:
    payload = json.dumps(
        _canonical_request_payload(request),
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _protocol_request_hash(request: dict[str, Any]) -> str:
    payload = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _is_retryable_execution_error(error: Any) -> bool:
    text = str(error or '').lower()
    return any(marker in text for marker in RETRYABLE_EXECUTION_MARKERS)


def _validate_direct_provider_response(response: Any, entry: dict[str, Any]) -> None:
    """Reject direct-provider responses that cannot be authoritatively billed.

    OpenRouter responses are allowed to omit inline usage because their
    generation ledger is queried separately in ``_usage_payload``. Direct
    providers have no equivalent recovery path, so a missing or incomplete
    usage object is a technical provider failure and must surface.
    """
    if str(entry.get('provider') or '').strip() == 'openrouter':
        return
    usage = _primitive_usage_dict(_response_field(response, 'usage'))
    if not _usage_has_token_counts(usage):
        raise RuntimeError(
            'malformed response: direct provider did not return authoritative token usage'
        )


def _is_retryable_success_accounting_error(error: Any, entry: dict[str, Any]) -> bool:
    """Classify direct-provider usage validation failures as technical errors."""
    if str(entry.get('provider') or '').strip() == 'openrouter':
        return False
    text = str(error or '').lower()
    return (
        'successful response cannot be accounted' in text
        and 'token count' in text
    )


def _repair_protocol_request(request: dict[str, Any], error: Any) -> tuple[dict[str, Any], list[str]]:
    """Repair transport-only incompatibilities without changing semantic content."""
    repaired = dict(request)
    repairs: list[str] = []
    text = str(error or '').lower()
    if 'reasoning' in text or 'reasoning_effort' in text:
        for key in ('reasoning', 'reasoning_effort', 'include_reasoning'):
            if key in repaired:
                repaired.pop(key, None)
                repairs.append(f'remove_{key}')
        extra = repaired.get('extra_body')
        if isinstance(extra, dict):
            extra = dict(extra)
            for key in ('reasoning', 'reasoning_effort', 'include_reasoning'):
                if key in extra:
                    extra.pop(key, None)
                    repairs.append(f'remove_extra_body_{key}')
            repaired['extra_body'] = extra
    if 'tool' in text and (
        'unsupported' in text
        or 'not support' in text
        or 'tool_choice' in text
        or 'invalid json' in text
        or 'deserialize' in text
    ):
        for key in ('tools', 'tool_choice'):
            if key in repaired:
                repaired.pop(key, None)
                repairs.append(f'remove_{key}')
    if 'json' in text and ('unsupported' in text or 'not support' in text):
        if 'response_format' in repaired:
            repaired.pop('response_format', None)
            repairs.append('remove_response_format')
    if 'max_tokens' in text and ('unsupported' in text or 'not support' in text):
        for key in OUTPUT_TOKEN_KEYS:
            if key in repaired:
                repaired.pop(key, None)
                repairs.append(f'remove_{key}')
    return repaired, repairs


def _next_routing_call_id() -> str:
    global _ROUTING_CALL_COUNTER
    with _LOCK:
        _ROUTING_CALL_COUNTER += 1
        return f'{SESSION_ID}:{_ROUTING_CALL_COUNTER}'


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get('text')
                if isinstance(text, str):
                    parts.append(text)
        return '\n'.join(parts).strip()
    return ''


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        raise RuntimeError(f'LeRouter state file is missing: {STATE_PATH}')
    try:
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'LeRouter state file could not be read: {STATE_PATH}: {exc}') from exc
    if not isinstance(state, dict):
        raise RuntimeError(f'LeRouter state file must contain a JSON object: {STATE_PATH}')
    return state


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _configured_secret_values() -> list[str]:
    return [
        value.strip()
        for name, value in os.environ.items()
        if any(marker in name.upper() for marker in SECRET_ENV_MARKERS)
        and isinstance(value, str)
        and len(value.strip()) >= 8
    ]


def _safe_error_message(error: Any, *secrets: Any) -> str | None:
    if not error:
        return None
    text = str(error)
    for secret in [*_configured_secret_values(), *secrets]:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, '<redacted>')
    text = re.sub(r'(?i)\bBearer\s+[^\s"\']+', 'Bearer <redacted>', text)
    text = re.sub(
        r'(?i)\b(?:sk-|lr_live_|tgp_)[A-Za-z0-9._~+/=-]+',
        '<redacted>',
        text,
    )
    return text[:500]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _required_nonnegative_number(
    sources: list[dict[str, Any]],
    keys: tuple[str, ...],
    label: str,
) -> float:
    for source in sources:
        for key in keys:
            if key not in source:
                continue
            value = source[key]
            if value is None or isinstance(value, bool):
                raise ValueError(f'{label} must be an explicit finite nonnegative number')
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f'{label} must be an explicit finite nonnegative number'
                ) from exc
            if not math.isfinite(number) or number < 0:
                raise ValueError(f'{label} must be an explicit finite nonnegative number')
            return number
    raise ValueError(f'{label} is missing')


def _estimate_final_request_spend_usd(entry: dict[str, Any] | None, usage: dict[str, Any]) -> float:
    if not isinstance(entry, dict):
        raise ValueError('selected model profile is missing')
    prompt_tokens = _required_nonnegative_number(
        [usage],
        ('prompt_tokens', 'input_tokens', 'promptTokens', 'inputTokens'),
        'input token count',
    )
    completion_tokens = _required_nonnegative_number(
        [usage],
        ('completion_tokens', 'output_tokens', 'completionTokens', 'outputTokens'),
        'output token count',
    )
    model_cost = entry.get('model_cost') if isinstance(entry.get('model_cost'), dict) else {}
    price_sources = [entry, model_cost]
    input_price = _required_nonnegative_number(
        price_sources,
        ('input_price_per_million', 'input_usd_per_million'),
        'input price per million tokens',
    )
    output_price = _required_nonnegative_number(
        price_sources,
        ('output_price_per_million', 'output_usd_per_million'),
        'output price per million tokens',
    )
    return round(
        (prompt_tokens / 1_000_000 * input_price)
        + (completion_tokens / 1_000_000 * output_price),
        12,
    )


def _append_eventx(event: dict[str, Any]) -> None:
    snapshot = {
        'version': 1,
        'updated_at': event['ts'],
        'events': [],
    }
    try:
        if EVENTX_PATH.exists():
            current = json.loads(EVENTX_PATH.read_text(encoding='utf-8'))
            if isinstance(current, dict):
                snapshot.update({
                    'version': current.get('version') or 1,
                    'events': list(current.get('events') or []),
                })
    except Exception:
        pass
    snapshot['updated_at'] = event['ts']
    snapshot['events'] = [*snapshot.get('events', []), event][-500:]
    EVENTX_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def _dashboard_url(state: dict[str, Any]) -> str:
    return str(
        state.get('dashboard_url')
        or os.environ.get('LEROUTER_DASHBOARD_URL')
        or os.environ.get('NEXT_PUBLIC_APP_URL')
        or ''
    ).rstrip('/')


def _post_dashboard_operation(state: dict[str, Any], event: dict[str, Any]) -> None:
    dashboard_url = _dashboard_url(state)
    if not dashboard_url:
        return
    payload = {
        'routeId': event.get('route_id') or state.get('route_id', 'default'),
        'routeName': event.get('route_name'),
        'provider': event.get('provider'),
        'modelId': event.get('model_id'),
        'success': bool(event.get('success')),
        'spendUsd': 0,
        'metadata': {
            'kind': 'routing_operation',
            'operation': event.get('event'),
            'session_id': event.get('session_id'),
            'routing_call_id': event.get('routing_call_id'),
            'latency_ms': event.get('latency_ms'),
            'error_type': event.get('error_type'),
            'error_message': event.get('error_message'),
            'source': 'hermes_lerouter_plugin',
        },
    }
    try:
        _post_json(f"{dashboard_url}/api/usage-log", payload, state['api_key'], timeout=8)
    except Exception as exc:
        logger.debug(
            'LeRouter dashboard event post failed: %s',
            _safe_error_message(exc, state.get('api_key')),
        )


def _log_event(
    state: dict[str, Any],
    event_name: str,
    *,
    routing_call_id: str | None = None,
    route_name: str | None = None,
    entry: dict[str, Any] | None = None,
    success: bool | None = None,
    latency_ms: int | None = None,
    error: Exception | str | None = None,
    **details: Any,
) -> None:
    event = {
        'ts': _iso_now(),
        'session_id': SESSION_ID,
        'event': event_name,
        'routing_call_id': routing_call_id,
        'route_id': state.get('route_id', 'default'),
        'route_name': route_name,
        'provider': (entry or {}).get('provider'),
        'model_id': (entry or {}).get('model_id'),
        'success': bool(success if success is not None else not event_name.endswith('_failed')),
        'latency_ms': latency_ms,
        'error_type': error.__class__.__name__ if isinstance(error, Exception) else None,
        'error_message': _safe_error_message(
            error,
            state.get('api_key'),
            (entry or {}).get('api_key'),
        ),
    }
    for key, value in details.items():
        if value is not None:
            event[key] = value
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_PATH.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + '\n')
        _append_eventx(event)
    except Exception as exc:
        logger.debug(
            'LeRouter local event write failed: %s',
            _safe_error_message(exc, state.get('api_key')),
        )
    _post_dashboard_operation(state, event)


def _json_http_request(
    method: str,
    url: str,
    api_key: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {'http', 'https'} or not parsed.hostname:
        raise RuntimeError(f'LeRouter HTTP URL is invalid: {url!r}')
    port = parsed.port or (443 if scheme == 'https' else 80)
    key = (scheme, parsed.hostname, port)
    target = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
    serialized = (
        json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if payload is not None
        else None
    )
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {api_key}',
        'User-Agent': 'LeRouter-Hermes/1.0',
    }
    if serialized is not None:
        headers['Content-Type'] = 'application/json'
        headers['Content-Length'] = str(len(serialized))

    with _HTTP_LOCK:
        connection = _HTTP_CONNECTIONS.get(key)
        reused = connection is not None and connection.sock is not None
        if connection is None:
            connection = (
                http.client.HTTPSConnection(
                    parsed.hostname,
                    port,
                    timeout=timeout,
                    context=ssl.create_default_context(cafile=certifi.where()),
                )
                if scheme == 'https'
                else http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
            )
            _HTTP_CONNECTIONS[key] = connection
        else:
            connection.timeout = timeout
            if connection.sock is not None:
                connection.sock.settimeout(timeout)
        try:
            connection.request(method, target, body=serialized, headers=headers)
            response = connection.getresponse()
            raw_bytes = response.read()
            status = int(response.status)
            reason = str(response.reason or '')
            if response.will_close:
                _HTTP_CONNECTIONS.pop(key, None)
                connection.close()
        except Exception as exc:
            _HTTP_CONNECTIONS.pop(key, None)
            connection.close()
            safe_error = _safe_error_message(exc, api_key) or exc.__class__.__name__
            raise RuntimeError(f'LeRouter HTTP transport failed: {safe_error}') from None

    raw = raw_bytes.decode('utf-8', errors='replace').strip()
    if status < 200 or status >= 300:
        safe_body = _safe_error_message(raw, api_key) or reason or 'request failed'
        raise RuntimeError(f'LeRouter HTTP {status}: {safe_body}')
    try:
        result = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'LeRouter HTTP response was not JSON: {exc}') from None
    if not isinstance(result, dict):
        raise RuntimeError('LeRouter HTTP response must be a JSON object')
    result['_lerouter_http_timings_ms'] = {
        'total': round((time.perf_counter() - started) * 1000, 2),
        'connection_reused': reused,
    }
    return result


def _post_json(url: str, payload: dict[str, Any], api_key: str, *, timeout: int = 120) -> dict[str, Any]:
    return _json_http_request('POST', url, api_key, payload=payload, timeout=timeout)


def _get_json(url: str, api_key: str, *, timeout: int = 30) -> dict[str, Any]:
    return _json_http_request('GET', url, api_key, timeout=timeout)


def _current_agent() -> Any:
    try:
        from hermes_cli.plugins import get_plugin_manager
        mgr = get_plugin_manager()
        cli_ref = getattr(mgr, '_cli_ref', None)
        agent = getattr(cli_ref, 'agent', None)
        if agent is not None:
            return agent
    except Exception:
        pass

    try:
        frame = sys._getframe()
        while frame is not None:
            candidate = frame.f_locals.get('agent')
            if (
                candidate is not None
                and hasattr(candidate, 'switch_model')
                and hasattr(candidate, '_build_api_kwargs')
            ):
                return candidate
            frame = frame.f_back
    except Exception:
        pass

    return None


def _current_api_messages() -> Any:
    try:
        frame = sys._getframe()
        while frame is not None:
            api_messages = frame.f_locals.get('api_messages')
            if isinstance(api_messages, list):
                return api_messages
            frame = frame.f_back
    except Exception:
        pass
    return None


def _positive_output_token_limit(value: Any, label: str) -> int:
    if value is None or isinstance(value, bool):
        raise RuntimeError(f'{label} must be a positive integer')
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f'{label} must be a positive integer') from exc
    if not math.isfinite(number) or number <= 0 or not number.is_integer():
        raise RuntimeError(f'{label} must be a positive integer')
    return int(number)


def _benchmark_output_token_cap(state: dict[str, Any]) -> int | None:
    benchmark = state.get('benchmark')
    if not isinstance(benchmark, dict):
        return None
    if benchmark.get('max_output_tokens_per_call') is None:
        return None
    return _positive_output_token_limit(
        benchmark['max_output_tokens_per_call'],
        'LeRouter benchmark.max_output_tokens_per_call',
    )


def _agent_output_token_limits(agent: Any) -> list[int]:
    limits: list[int] = []
    for attribute in ('_ephemeral_max_output_tokens', 'max_tokens'):
        value = getattr(agent, attribute, None)
        if value is not None:
            limits.append(
                _positive_output_token_limit(value, f'Hermes agent.{attribute}')
            )
    return limits


def _request_output_token_limits(request: dict[str, Any]) -> list[int]:
    return [
        _positive_output_token_limit(request[key], f'Hermes request.{key}')
        for key in OUTPUT_TOKEN_KEYS
        if request.get(key) is not None
    ]


def _clamp_agent_output_tokens(agent: Any, output_limit: int | None) -> None:
    if output_limit is None:
        return
    if hasattr(agent, 'max_tokens'):
        current = getattr(agent, 'max_tokens', None)
        agent.max_tokens = min(
            output_limit,
            _positive_output_token_limit(current, 'Hermes agent.max_tokens')
            if current is not None
            else output_limit,
        )
    if hasattr(agent, '_ephemeral_max_output_tokens'):
        current = getattr(agent, '_ephemeral_max_output_tokens', None)
        if current is not None:
            agent._ephemeral_max_output_tokens = min(
                output_limit,
                _positive_output_token_limit(
                    current,
                    'Hermes agent._ephemeral_max_output_tokens',
                ),
            )


def _prepare_benchmark_output_limit(
    state: dict[str, Any],
    request: dict[str, Any],
    agent: Any,
) -> tuple[dict[str, Any], int | None]:
    cap = _benchmark_output_token_cap(state)
    if cap is None:
        return dict(request), None
    available_limits = [
        cap,
        *_request_output_token_limits(request),
        *_agent_output_token_limits(agent),
    ]
    output_limit = min(available_limits)
    patched = dict(request)
    for key in OUTPUT_TOKEN_KEYS:
        patched.pop(key, None)
    patched['max_tokens'] = output_limit
    _clamp_agent_output_tokens(agent, output_limit)
    return patched, output_limit


def _provider_output_token_key(agent: Any, output_limit: int, request: dict[str, Any]) -> str:
    if getattr(agent, 'api_mode', None) == 'codex_responses':
        return 'max_output_tokens'
    formatter = getattr(agent, '_max_tokens_param', None)
    if callable(formatter):
        formatted = formatter(output_limit)
        if not isinstance(formatted, dict):
            raise RuntimeError('Hermes _max_tokens_param must return a request object')
        formatted_keys = [key for key in OUTPUT_TOKEN_KEYS if key in formatted]
        if len(formatted_keys) != 1:
            raise RuntimeError(
                'Hermes _max_tokens_param must return exactly one output-token field'
            )
        return formatted_keys[0]
    existing_keys = [key for key in OUTPUT_TOKEN_KEYS if request.get(key) is not None]
    return existing_keys[0] if len(existing_keys) == 1 else 'max_tokens'


def _enforce_rebuilt_output_limit(
    agent: Any,
    request: dict[str, Any],
    output_limit: int | None,
) -> dict[str, Any]:
    if output_limit is None:
        return request
    rebuilt_limits = _request_output_token_limits(request)
    provider_limit = min([output_limit, *rebuilt_limits])
    key = _provider_output_token_key(agent, provider_limit, request)
    patched = dict(request)
    for output_key in OUTPUT_TOKEN_KEYS:
        patched.pop(output_key, None)
    patched[key] = provider_limit
    return patched


def _bounded_openai_prompt_cache_key(value: Any) -> Any:
    if not isinstance(value, str) or len(value) <= OPENAI_PROMPT_CACHE_KEY_MAX_CHARS:
        return value
    digest = hashlib.sha256(value.encode('utf-8')).hexdigest()
    prefix = 'lerouter-'
    return prefix + digest[: OPENAI_PROMPT_CACHE_KEY_MAX_CHARS - len(prefix)]


def _bound_openai_prompt_cache_keys(
    request: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    if str(entry.get('provider') or '').strip() != 'openai':
        return request
    patched = dict(request)
    if 'prompt_cache_key' in patched:
        patched['prompt_cache_key'] = _bounded_openai_prompt_cache_key(
            patched['prompt_cache_key']
        )
    extra_body = patched.get('extra_body')
    if isinstance(extra_body, dict) and 'prompt_cache_key' in extra_body:
        patched_extra_body = dict(extra_body)
        patched_extra_body['prompt_cache_key'] = _bounded_openai_prompt_cache_key(
            patched_extra_body['prompt_cache_key']
        )
        patched['extra_body'] = patched_extra_body
    return patched


def _apply_reasoning_capability(
    request: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    if entry.get('supports_reasoning_effort') is not False:
        return request
    containers = [request]
    if isinstance(request.get('extra_body'), dict):
        containers.append(request['extra_body'])
    for container in containers:
        if any(
            container.get(key) is not None
            for key in ('reasoning', 'reasoning_effort', 'include_reasoning')
        ):
            raise CandidateCapabilityMismatchError(
                'Unsupported parameter: reasoning controls are not supported with this model profile'
            )
        include = container.get('include')
        if isinstance(include, list) and any(
            str(value).strip().lower() == 'reasoning.encrypted_content'
            for value in include
        ):
            raise CandidateCapabilityMismatchError(
                'Unsupported parameter: reasoning.encrypted_content is not supported with this model profile'
            )
    return request


def _is_capability_mismatch_error(error: Exception) -> bool:
    text = str(error or '').strip().lower()
    if not text:
        return False
    rejection_markers = (
        'unsupported parameter',
        'unsupported_parameter',
        'unsupported value',
        'unsupported_value',
        'unknown parameter',
        'not supported with this model',
        'does not support',
    )
    capability_markers = (
        'reasoning',
        'tool',
        'function',
        'response_format',
        'structured output',
        'json_schema',
        'temperature',
    )
    return (
        any(marker in text for marker in rejection_markers)
        and any(marker in text for marker in capability_markers)
    )


def _rebuild_request_for_active_agent(agent: Any, api_messages: Any, base_request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(api_messages, list):
        raise RuntimeError('Hermes API messages are unavailable; selected-model request rebuild cannot continue')
    try:
        agent._reapply_reasoning_echo_for_provider(api_messages)
        rebuilt = agent._build_api_kwargs(api_messages)
        if not isinstance(rebuilt, dict):
            raise TypeError(f'_build_api_kwargs returned {type(rebuilt).__name__}, expected dict')
        for key in ('stream', 'tools', 'tool_choice', 'response_format', 'temperature'):
            if base_request.get(key) is not None and rebuilt.get(key) is None:
                rebuilt[key] = base_request[key]
        if getattr(agent, '_force_ascii_payload', False):
            from agent.conversation_loop import _sanitize_structure_non_ascii
            _sanitize_structure_non_ascii(rebuilt)
        if getattr(agent, 'api_mode', None) == 'codex_responses':
            output_limits = [
                *_request_output_token_limits(base_request),
                *_request_output_token_limits(rebuilt),
                *_agent_output_token_limits(agent),
            ]
            if output_limits:
                rebuilt = _enforce_rebuilt_output_limit(
                    agent,
                    rebuilt,
                    min(output_limits),
                )
            rebuilt = agent._get_transport().preflight_kwargs(
                rebuilt,
                allow_stream=bool(rebuilt.get('stream')),
            )
        for key in ('stream', 'tools', 'tool_choice', 'response_format', 'temperature'):
            if base_request.get(key) is not None and rebuilt.get(key) is None:
                rebuilt[key] = base_request[key]
        return rebuilt
    except Exception as exc:
        raise RuntimeError(
            f'Hermes could not rebuild the request for LeRouter selected model: {exc}'
        ) from exc


def _patch_request_identity(messages: Any, model: str, provider: str) -> Any:
    if not isinstance(messages, list) or not messages:
        return messages
    patched = list(messages)
    first = patched[0]
    if not isinstance(first, dict) or first.get('role') != 'system':
        return patched
    content = first.get('content')
    if not isinstance(content, str):
        return patched
    lines = content.splitlines()
    model_idx = None
    provider_idx = None
    for idx, line in enumerate(lines):
        if line.startswith('Model: '):
            model_idx = idx
        elif line.startswith('Provider: '):
            provider_idx = idx
    if model_idx is not None:
        lines[model_idx] = f'Model: {model}'
    if provider_idx is not None:
        lines[provider_idx] = f'Provider: {provider}'
    patched[0] = dict(first)
    patched[0]['content'] = '\n'.join(lines)
    return patched


def _find_catalog_entry(state: dict[str, Any], selection: dict[str, Any]) -> Optional[dict[str, Any]]:
    model_catalog = state.get('model_catalog') or []
    selected_model = selection.get('selected_model')
    if not isinstance(selected_model, dict):
        selected_model = selection.get('best_model')
    if not isinstance(selected_model, dict):
        selected_model = {}
    selected_model_id = (
        selection.get('selected_model_id')
        or selection.get('model_id')
        or selected_model.get('model_id')
    )
    native_model_id = selection.get('native_model_id') or selected_model.get('native_model_id')
    selected_provider = (
        selection.get('provider')
        or selection.get('selected_provider')
        or selected_model.get('provider')
    )
    catalog_entry = None
    if selected_model_id:
        for entry in model_catalog:
            if entry.get('model_id') == selected_model_id:
                catalog_entry = entry
                break
    if catalog_entry is None and native_model_id and selected_provider:
        for entry in model_catalog:
            if entry.get('native_model_id') == native_model_id and entry.get('provider') == selected_provider:
                catalog_entry = entry
                break
    if catalog_entry is None and native_model_id:
        matches = [entry for entry in model_catalog if entry.get('native_model_id') == native_model_id]
        if len(matches) == 1:
            catalog_entry = matches[0]
    if catalog_entry is None:
        return None

    merged = dict(catalog_entry)
    for key in ROUTER_SELECTION_SCORE_FIELDS:
        if selected_model.get(key) is not None:
            merged[key] = selected_model[key]
    for key in SIGNED_ACCOUNTING_PROFILE_FIELDS:
        if selected_model.get(key) is not None:
            merged[key] = selected_model[key]
    for key in ROUTER_EXECUTION_CAPABILITY_FIELDS:
        if selected_model.get(key) is not None:
            merged[key] = selected_model[key]
    if isinstance(selected_model.get('budget_result'), dict):
        merged['budget_result'] = selected_model['budget_result']
    return merged


def _entry_key(entry: dict[str, Any] | None) -> tuple[str | None, str | None, str | None]:
    if not isinstance(entry, dict):
        return (None, None, None)
    return (
        entry.get('model_id'),
        entry.get('provider'),
        entry.get('native_model_id'),
    )


def _visible_routing_line(
    *,
    routing_call_id: str,
    route_name: str | None,
    entry: dict[str, Any],
    router_latency_ms: int | None = None,
    candidate_index: int = 0,
) -> None:
    global _LAST_VISIBLE_ROUTING_KEY
    provider = entry.get('provider') or '-'
    model_id = entry.get('model_id') or entry.get('native_model_id') or '-'
    native_model_id = entry.get('native_model_id') or model_id
    current_key = (str(route_name or '-'), str(provider), str(model_id))
    changed = _LAST_VISIBLE_ROUTING_KEY is not None and current_key != _LAST_VISIBLE_ROUTING_KEY
    _LAST_VISIBLE_ROUTING_KEY = current_key
    parts = [
        'LeRouter',
        f'call={routing_call_id.rsplit(":", 1)[-1]}',
        f'route={route_name or "-"}',
        f'provider={provider}',
        f'model={model_id}',
    ]
    if native_model_id != model_id:
        parts.append(f'native={native_model_id}')
    if router_latency_ms is not None:
        parts.append(f'router={router_latency_ms}ms')
    if candidate_index:
        parts.append(f'retry={candidate_index + 1}')
    if changed:
        parts.append('model_changed=true')
    print(' | '.join(parts), file=sys.stderr, flush=True)


def _compact_routing_model(model: dict[str, Any], index: int) -> dict[str, Any]:
    compact = {
        'rank': index + 1,
        'model_id': model.get('model_id') or model.get('model'),
        'provider': model.get('provider'),
        'native_model_id': model.get('native_model_id'),
    }
    for key in ROUTER_SELECTION_SCORE_FIELDS:
        if model.get(key) is not None:
            compact[key] = model.get(key)
    budget_result = model.get('budget_result')
    if isinstance(budget_result, dict):
        compact['budget_result'] = budget_result
    return {key: value for key, value in compact.items() if value is not None}


def _compact_routing_models(models: Any) -> list[dict[str, Any]]:
    if not isinstance(models, list):
        return []
    return [
        _compact_routing_model(model, index)
        for index, model in enumerate(models)
        if isinstance(model, dict)
    ]


def _selection_metadata(selection: dict[str, Any]) -> dict[str, Any]:
    pipeline = selection.get('pipeline') if isinstance(selection.get('pipeline'), dict) else {}
    metadata = {
        'candidate_model_pool': _compact_routing_models(pipeline.get('candidate_model_pool')),
        'biencoder_ranked_candidates': _compact_routing_models(
            pipeline.get('biencoder') or selection.get('ranked_candidates')
        ),
        'budget_ranked_candidates': _compact_routing_models(
            pipeline.get('euristique_budget_manager') or selection.get('ranked_candidates')
        ),
    }
    e5 = selection.get('e5') if isinstance(selection.get('e5'), dict) else {}
    if e5:
        metadata['e5'] = {
            'model_run_id': e5.get('model_run_id'),
            'code_version': e5.get('code_version'),
            'selected_model': e5.get('selected_model'),
            'ranked': _compact_routing_models(e5.get('ranked')),
        }
    stage_timings_ms = selection.get('stage_timings_ms')
    if isinstance(stage_timings_ms, dict):
        metadata['stage_timings_ms'] = dict(stage_timings_ms)
    client_http_timings_ms = selection.get('_lerouter_http_timings_ms')
    if isinstance(client_http_timings_ms, dict):
        metadata['client_http_timings_ms'] = dict(client_http_timings_ms)
    route_worker = pipeline.get('route_worker') if isinstance(pipeline.get('route_worker'), dict) else {}
    worker_stage_timings_ms = route_worker.get('stage_timings_ms')
    if isinstance(worker_stage_timings_ms, dict):
        metadata['route_worker_stage_timings_ms'] = dict(worker_stage_timings_ms)
    component_timings_ms = route_worker.get('component_timings_ms')
    if isinstance(component_timings_ms, dict):
        metadata['routing_component_timings_ms'] = dict(component_timings_ms)
    execution_identity = selection.get('execution_identity')
    if isinstance(execution_identity, dict):
        metadata['execution_identity'] = dict(execution_identity)
    return {key: value for key, value in metadata.items() if value}


def _candidate_entries(state: dict[str, Any], selection: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        attempt['entry']
        for attempt in _candidate_attempts(state, selection)
    ]


def _candidate_attempts(
    state: dict[str, Any],
    selection: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_candidates = selection.get('execution_candidates')
    candidate_selections = (
        raw_candidates
        if isinstance(raw_candidates, list) and raw_candidates
        else [selection]
    )
    # Only rank 1 is executable. The full ranking remains visible as evidence,
    # but a provider or capability failure must surface instead of switching to
    # a model that differs from select_succeeded.
    candidate_selections = candidate_selections[:1]
    attempts: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for raw_candidate in candidate_selections:
        if not isinstance(raw_candidate, dict):
            continue
        candidate_selection = {
            **selection,
            **raw_candidate,
        }
        entry = _find_catalog_entry(state, candidate_selection)
        if entry is None:
            continue
        key = _entry_key(entry)
        if key in seen:
            continue
        _require_accounting_claim(candidate_selection)
        attempts.append({
            'entry': entry,
            'selection': candidate_selection,
        })
        seen.add(key)
    return attempts


def _require_accounting_claim(selection: dict[str, Any] | None) -> tuple[str, str]:
    if not isinstance(selection, dict):
        raise RuntimeError('LeRouter selection response is missing signed accounting data')
    routing_call_id = str(selection.get('routing_call_id') or '').strip()
    accounting_token = str(selection.get('accounting_token') or '').strip()
    missing = [
        field
        for field, value in (
            ('routing_call_id', routing_call_id),
            ('accounting_token', accounting_token),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            'LeRouter selection response is missing required signed accounting fields: '
            + ', '.join(missing)
        )
    return routing_call_id, accounting_token


def _response_field(response: Any, field: str) -> Any:
    if isinstance(response, dict):
        return response.get(field)
    return getattr(response, field, None)


def _primitive_usage_dict(usage: Any) -> dict[str, Any]:
    if isinstance(usage, dict):
        return dict(usage)
    if usage is None:
        return {}
    return {
        key: getattr(usage, key)
        for key in dir(usage)
        if not key.startswith('_')
        and isinstance(getattr(usage, key), (str, int, float, bool, type(None)))
    }


def _usage_has_token_counts(usage: dict[str, Any]) -> bool:
    input_keys = ('prompt_tokens', 'input_tokens', 'promptTokens', 'inputTokens')
    output_keys = ('completion_tokens', 'output_tokens', 'completionTokens', 'outputTokens')
    return any(key in usage for key in input_keys) and any(key in usage for key in output_keys)


def _openrouter_generation_usage(entry: dict[str, Any], response: Any) -> dict[str, Any]:
    generation_id = (
        _response_field(response, '_lerouter_openrouter_generation_id')
        or _response_field(response, 'id')
    )
    if not isinstance(generation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', generation_id):
        raise ValueError('OpenRouter response generation id is missing or invalid')
    api_key = entry.get('api_key') or os.environ.get('OPENROUTER_API_KEY')
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError('OpenRouter API key is missing for generation usage lookup')
    base_url = str(entry.get('base_url') or 'https://openrouter.ai/api/v1').rstrip('/')
    response_payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            response_payload = _get_json(
                f'{base_url}/generation?id={generation_id}',
                api_key,
            )
            break
        except RuntimeError as error:
            last_error = error
            error_text = str(error).lower()
            if '404' not in error_text or 'not found' not in error_text or attempt == 5:
                raise
            # OpenRouter can return the completion before its authoritative
            # generation ledger is queryable. Poll that same ledger entry so a
            # successful paid generation is never re-executed or estimated.
            time.sleep(0.5 * (attempt + 1))
    if response_payload is None:
        raise RuntimeError(
            f'OpenRouter generation usage remained unavailable: {last_error}'
        )
    generation = response_payload.get('data') if isinstance(response_payload, dict) else None
    if not isinstance(generation, dict):
        raise ValueError('OpenRouter generation usage response is missing data')
    if generation.get('id') != generation_id:
        raise ValueError('OpenRouter generation usage response id does not match completion')
    return {
        'prompt_tokens': generation.get('tokens_prompt'),
        'completion_tokens': generation.get('tokens_completion'),
        'total_tokens': (
            generation.get('tokens_prompt') + generation.get('tokens_completion')
            if isinstance(generation.get('tokens_prompt'), (int, float))
            and not isinstance(generation.get('tokens_prompt'), bool)
            and isinstance(generation.get('tokens_completion'), (int, float))
            and not isinstance(generation.get('tokens_completion'), bool)
            else None
        ),
        'cost': generation.get('total_cost'),
        'generation_id': generation_id,
        'accounting_source': 'openrouter_generation_metadata',
    }


def _usage_payload(state: dict[str, Any], *, routing_call_id: str | None, route_name: str | None, entry: dict[str, Any] | None, success: bool, response: Any = None, error: str | None = None, latency_ms: int | None = None, router_latency_ms: int | None = None, selection: dict[str, Any] | None = None, stream: bool | None = None, semantic_request_hash: str | None = None, protocol_request_hash: str | None = None, protocol_repairs: list[str] | None = None, previous_attempt_error: str | None = None, previous_model_id: str | None = None, retry_reason: str | None = None) -> dict[str, Any]:
    accounting_routing_call_id, accounting_token = _require_accounting_claim(selection)
    accounting_user_id = str((selection or {}).get('user_id') or '').strip()
    if not accounting_user_id:
        raise RuntimeError(
            'LeRouter selection response is missing required signed accounting field: user_id'
        )
    if routing_call_id and routing_call_id != accounting_routing_call_id:
        raise RuntimeError('LeRouter accounting routing_call_id changed after selection')
    usage_dict = _primitive_usage_dict(_response_field(response, 'usage'))
    if (
        success
        and not _usage_has_token_counts(usage_dict)
        and isinstance(entry, dict)
        and entry.get('provider') == 'openrouter'
    ):
        try:
            usage_dict = _openrouter_generation_usage(entry, response)
        except Exception as exc:
            safe_error = _safe_error_message(exc, entry.get('api_key')) or exc.__class__.__name__
            raise MiddlewareFailClosedError(
                f'LeRouter successful OpenRouter response usage lookup failed: {safe_error}'
            ) from None
    if success:
        try:
            final_request_spend_usd = _estimate_final_request_spend_usd(entry, usage_dict)
        except ValueError as exc:
            raise MiddlewareFailClosedError(
                f'LeRouter successful response cannot be accounted: {exc}'
            ) from exc
    else:
        final_request_spend_usd = 0.0
    metadata: dict[str, Any] = {
        'usage': usage_dict,
        'session_id': SESSION_ID,
        'routing_call_id': accounting_routing_call_id,
        'billing': {
            'finalRequestSpendUsd': final_request_spend_usd,
        },
    }
    if isinstance(entry, dict):
        if entry.get('model_id') is not None:
            metadata['selected_model_id'] = entry['model_id']
        for key in ROUTER_SELECTION_SCORE_FIELDS:
            if key == 'request_weight':
                continue
            if entry.get(key) is not None:
                metadata[key] = entry[key]
        if isinstance(entry.get('budget_result'), dict):
            metadata['budget_result'] = entry['budget_result']
    if isinstance(selection, dict):
        metadata.update(_selection_metadata(selection))
    if error:
        metadata['error'] = _safe_error_message(error, (entry or {}).get('api_key'))
    if latency_ms is not None:
        metadata['latency_ms'] = latency_ms
    if router_latency_ms is not None:
        metadata['router_latency_ms'] = router_latency_ms
    if stream is not None:
        metadata['stream'] = stream
    if semantic_request_hash:
        metadata['semantic_request_hash'] = semantic_request_hash
    if protocol_request_hash:
        metadata['protocol_request_hash'] = protocol_request_hash
    if protocol_repairs:
        metadata['protocol_repairs'] = list(protocol_repairs)
    if previous_attempt_error:
        metadata['previous_attempt_error'] = _safe_error_message(previous_attempt_error)
    if previous_model_id:
        metadata['previous_model_id'] = previous_model_id
    if retry_reason:
        metadata['retry_reason'] = retry_reason
    return {
        'user_id': accounting_user_id,
        'route_id': state.get('route_id', 'default'),
        'route_name': route_name,
        'model_id': (entry or {}).get('model_id'),
        'provider': (entry or {}).get('provider'),
        'inference_mode': state.get('inference_mode', 'user_managed'),
        'accounting_token': accounting_token,
        'success': success,
        'spend_usd': final_request_spend_usd,
        'metadata': metadata,
        'update_counters': True,
    }


def _finite_nonnegative_usage_value(value: Any, label: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f'{label} must be a finite nonnegative number')
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} must be a finite nonnegative number') from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f'{label} must be a finite nonnegative number')
    return number


def _validate_authoritative_usage_log(
    response: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(response, dict) or response.get('ok') is not True:
        raise ValueError('LeRouter usage-log response must confirm ok=true')
    usage_log = response.get('usage_log')
    if not isinstance(usage_log, dict):
        raise ValueError('LeRouter usage-log response is missing authoritative usage_log')

    required_fields = (
        'ts',
        'session_id',
        'route_id',
        'route_name',
        'model_id',
        'provider',
        'success',
        'spend_usd',
        'accounted',
        'inference_mode',
        'metadata',
    )
    missing_fields = [field for field in required_fields if field not in usage_log]
    if missing_fields:
        raise ValueError(
            'LeRouter authoritative usage_log is missing required fields: '
            + ', '.join(missing_fields)
        )

    timestamp = usage_log['ts']
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise ValueError('LeRouter authoritative usage_log.ts must be a timezone-aware timestamp')
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ValueError(
            'LeRouter authoritative usage_log.ts must be a timezone-aware timestamp'
        ) from exc
    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
        raise ValueError('LeRouter authoritative usage_log.ts must be a timezone-aware timestamp')

    expected_text_fields = {
        'session_id': SESSION_ID,
        'route_id': payload.get('route_id'),
        'route_name': payload.get('route_name'),
        'model_id': payload.get('model_id'),
        'provider': payload.get('provider'),
        'inference_mode': payload.get('inference_mode'),
    }
    for field, expected in expected_text_fields.items():
        actual = usage_log[field]
        if (
            not isinstance(expected, str)
            or not expected.strip()
            or not isinstance(actual, str)
            or not actual.strip()
            or actual != expected
        ):
            raise ValueError(f'LeRouter authoritative usage_log.{field} does not match request')

    expected_success = payload.get('success')
    if type(expected_success) is not bool or type(usage_log['success']) is not bool:
        raise ValueError('LeRouter authoritative usage_log.success must be boolean')
    if usage_log['success'] is not expected_success:
        raise ValueError('LeRouter authoritative usage_log.success does not match request')
    if usage_log['accounted'] is not True:
        raise ValueError('LeRouter authoritative usage_log.accounted must be true')

    expected_spend = _finite_nonnegative_usage_value(
        payload.get('spend_usd'),
        'LeRouter usage request spend_usd',
    )
    authoritative_spend = _finite_nonnegative_usage_value(
        usage_log['spend_usd'],
        'LeRouter authoritative usage_log.spend_usd',
    )
    if not math.isclose(
        authoritative_spend,
        expected_spend,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError('LeRouter authoritative usage_log.spend_usd does not match request')

    request_metadata = payload.get('metadata')
    authoritative_metadata = usage_log['metadata']
    if not isinstance(request_metadata, dict) or not isinstance(authoritative_metadata, dict):
        raise ValueError('LeRouter authoritative usage_log.metadata must be an object')
    if request_metadata.get('session_id') != SESSION_ID:
        raise ValueError('LeRouter usage request metadata.session_id does not match this session')
    if authoritative_metadata.get('session_id') != SESSION_ID:
        raise ValueError(
            'LeRouter authoritative usage_log.metadata.session_id does not match this session'
        )
    routing_call_id = request_metadata.get('routing_call_id')
    if not isinstance(routing_call_id, str) or not routing_call_id.strip():
        raise ValueError('LeRouter usage request metadata.routing_call_id is missing')
    if authoritative_metadata.get('routing_call_id') != routing_call_id:
        raise ValueError(
            'LeRouter authoritative usage_log.metadata.routing_call_id does not match request'
        )
    if (
        'routing_call_id' in usage_log
        and usage_log['routing_call_id'] != routing_call_id
    ):
        raise ValueError(
            'LeRouter authoritative usage_log.routing_call_id does not match request'
        )

    billing = authoritative_metadata.get('billing')
    if not isinstance(billing, dict):
        raise ValueError('LeRouter authoritative usage_log.metadata.billing must be an object')
    billed_provider_spend = _finite_nonnegative_usage_value(
        billing.get('finalRequestSpendUsd'),
        'LeRouter authoritative usage_log.metadata.billing.finalRequestSpendUsd',
    )
    if not math.isclose(
        billed_provider_spend,
        authoritative_spend,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError(
            'LeRouter authoritative usage_log spend does not match verified provider spend'
        )

    if 'routing_fee_usd' in usage_log:
        routing_fee = _finite_nonnegative_usage_value(
            usage_log['routing_fee_usd'],
            'LeRouter authoritative usage_log.routing_fee_usd',
        )
        metadata_routing_fee = _finite_nonnegative_usage_value(
            billing.get('routingFeeUsd'),
            'LeRouter authoritative usage_log.metadata.billing.routingFeeUsd',
        )
        if not math.isclose(
            routing_fee,
            metadata_routing_fee,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                'LeRouter authoritative usage_log.routing_fee_usd does not match billing metadata'
            )

    return usage_log


def _append_authoritative_usage_log(usage_log: dict[str, Any]) -> None:
    serialized = (
        json.dumps(usage_log, ensure_ascii=False, allow_nan=False) + '\n'
    ).encode('utf-8')
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        descriptor = os.open(
            USAGE_PATH,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        locked = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            locked = True
            os.fchmod(descriptor, 0o600)
            remaining = memoryview(serialized)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError('LeRouter authoritative usage ledger write made no progress')
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            try:
                if locked:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _log_usage(state: dict[str, Any], payload: dict[str, Any]) -> None:
    entry = {
        'model_id': payload.get('model_id'),
        'provider': payload.get('provider'),
    }
    routing_call_id = (payload.get('metadata') or {}).get('routing_call_id')
    started = time.time()
    _log_event(state, 'usage_log_started', routing_call_id=routing_call_id, route_name=payload.get('route_name'), entry=entry)
    try:
        response = _post_json(
            f"{state['base_url']}/lerouter/usage-log",
            payload,
            state['api_key'],
        )
        usage_log = _validate_authoritative_usage_log(response, payload)
        _append_authoritative_usage_log(usage_log)
        _log_event(
            state,
            'usage_log_succeeded',
            routing_call_id=routing_call_id,
            route_name=payload.get('route_name'),
            entry=entry,
            success=True,
            latency_ms=int((time.time() - started) * 1000),
            client_http_timings_ms=response.get('_lerouter_http_timings_ms'),
            server_stage_timings_ms=response.get('stage_timings_ms'),
            mongo_stage_timings_ms=response.get('mongo_stage_timings_ms'),
        )
    except Exception as exc:
        safe_error = _safe_error_message(
            exc,
            state.get('api_key'),
            payload.get('accounting_token'),
        ) or exc.__class__.__name__
        _log_event(
            state,
            'usage_log_failed',
            routing_call_id=routing_call_id,
            route_name=payload.get('route_name'),
            entry=entry,
            success=False,
            latency_ms=int((time.time() - started) * 1000),
            error=safe_error,
        )
        raise MiddlewareFailClosedError(
            f'LeRouter usage accounting failed after selected-model execution: {safe_error}'
        ) from None


def _openai_direct_responses_model(model: str) -> bool:
    normalized = str(model or '').strip().lower().rsplit('/', 1)[-1]
    return normalized.startswith(('gpt-5', 'o1', 'o3', 'o4', 'codex'))


def _enforce_selected_transport(agent: Any, provider: str, model: str) -> None:
    if provider == 'openrouter':
        api_mode = 'chat_completions'
    elif provider in {'openai', 'openai-api'}:
        api_mode = (
            'codex_responses'
            if _openai_direct_responses_model(model)
            else 'chat_completions'
        )
    else:
        return
    agent.api_mode = api_mode
    transport_cache = getattr(agent, '_transport_cache', None)
    if hasattr(transport_cache, 'clear'):
        transport_cache.clear()


def _activate_entry(agent: Any, request: dict[str, Any], entry: dict[str, Any]) -> bool:
    provider = str(entry.get('provider') or '').strip()
    hermes_provider = str(entry.get('hermes_provider') or provider).strip()
    native_model_id = str(entry.get('native_model_id') or '').strip()
    if not provider or not hermes_provider or not native_model_id:
        return False

    request['model'] = native_model_id
    if isinstance(request.get('messages'), list):
        request['messages'] = _patch_request_identity(request['messages'], native_model_id, provider)

    if (
        getattr(agent, 'provider', None) == hermes_provider
        and getattr(agent, 'model', None) == native_model_id
    ):
        _enforce_selected_transport(agent, hermes_provider, native_model_id)
        return True

    runtime_switch = getattr(agent, 'switch_model', None)
    if not callable(runtime_switch):
        return False

    from hermes_cli.config import load_config, get_compatible_custom_providers
    from hermes_cli.model_switch import switch_model as resolve_model_switch

    config = load_config()
    current_api_key = getattr(agent, 'api_key', '')
    result = resolve_model_switch(
        raw_input=native_model_id,
        current_provider=str(getattr(agent, 'provider', '') or ''),
        current_model=str(getattr(agent, 'model', '') or ''),
        current_base_url=str(getattr(agent, 'base_url', '') or ''),
        current_api_key=current_api_key if isinstance(current_api_key, str) else '',
        explicit_provider=hermes_provider,
        user_providers=config.get('providers') if isinstance(config.get('providers'), dict) else {},
        custom_providers=get_compatible_custom_providers(config),
    )
    if not result.success or result.target_provider != hermes_provider:
        return False

    api_mode = 'chat_completions' if hermes_provider == 'openrouter' else result.api_mode
    runtime_switch(
        new_model=native_model_id,
        new_provider=hermes_provider,
        api_key=result.api_key,
        base_url=result.base_url,
        api_mode=api_mode,
    )
    _enforce_selected_transport(agent, hermes_provider, native_model_id)
    try:
        from agent.chat_completion_helpers import rewrite_prompt_model_identity
        rewrite_prompt_model_identity(agent, native_model_id, provider)
    except Exception:
        pass
    return True


def _execute_request(agent: Any, request: dict[str, Any], next_call: Any | None = None) -> Any:
    if callable(next_call):
        capture = getattr(agent, '_stream_diag_capture_response', None)
        if not callable(capture):
            return next_call(request)
        generation_ids: list[str] = []

        def capture_openrouter_generation(diag: Any, http_response: Any) -> Any:
            result = capture(diag, http_response)
            headers = getattr(http_response, 'headers', None) or {}
            generation_id = headers.get('x-generation-id')
            if isinstance(generation_id, str) and generation_id.strip():
                generation_ids.append(generation_id.strip())
            return result

        agent._stream_diag_capture_response = capture_openrouter_generation
        try:
            response = next_call(request)
        finally:
            agent._stream_diag_capture_response = capture
        if generation_ids:
            if isinstance(response, dict):
                response['_lerouter_openrouter_generation_id'] = generation_ids[-1]
            else:
                setattr(
                    response,
                    '_lerouter_openrouter_generation_id',
                    generation_ids[-1],
                )
        return response
    if bool(request.get('stream')) and hasattr(agent, '_interruptible_streaming_api_call'):
        return agent._interruptible_streaming_api_call(request)
    if hasattr(agent, '_interruptible_api_call'):
        return agent._interruptible_api_call(request)
    if hasattr(agent, '_interruptible_streaming_api_call'):
        return agent._interruptible_streaming_api_call(request)
    raise RuntimeError('Hermes direct execution API is unavailable for the LeRouter-selected model')


def _apply_entry_to_request(
    request: dict[str, Any],
    entry: dict[str, Any],
    route_name: str | None = None,
) -> dict[str, Any]:
    provider = str(entry.get('provider') or '').strip()
    native_model_id = str(entry.get('native_model_id') or '').strip()
    if not native_model_id:
        return request
    patched = dict(request)
    patched['model'] = native_model_id
    if provider and isinstance(patched.get('messages'), list):
        messages = _patch_request_identity(patched['messages'], native_model_id, provider)
        routing_note = (
            '[LeRouter middleware note: routing is active for this request. '
            f'The selected provider is {provider}; the selected model is {native_model_id}.'
            + (f' The selected route is {route_name}.' if route_name else '')
            + ']'
        )
        patched_messages = list(messages)
        for idx, message in enumerate(patched_messages):
            if not isinstance(message, dict) or message.get('role') != 'user':
                continue
            content = message.get('content')
            if isinstance(content, str):
                patched_message = dict(message)
                patched_message['content'] = f'{routing_note}\n\n{content}'
                patched_messages[idx] = patched_message
                break
        else:
            patched_messages.append({'role': 'user', 'content': routing_note})
        patched['messages'] = patched_messages
    return patched


def _verify_selected_execution_identity(
    agent: Any,
    request: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    selected_model_id = str(entry.get('model_id') or '').strip()
    selected_native_model_id = str(entry.get('native_model_id') or '').strip()
    selected_provider = str(entry.get('provider') or '').strip()
    selected_hermes_provider = str(
        entry.get('hermes_provider') or selected_provider
    ).strip()
    executed_request_model = str(request.get('model') or '').strip()
    executed_agent_model = str(getattr(agent, 'model', '') or '').strip()
    executed_agent_provider = str(getattr(agent, 'provider', '') or '').strip()

    if not selected_model_id or not selected_native_model_id or not selected_hermes_provider:
        raise RuntimeError('LeRouter selected model identity is incomplete before execution')
    if executed_request_model != selected_native_model_id:
        raise RuntimeError(
            'LeRouter execution request model does not match the router-selected model: '
            f'selected={selected_native_model_id!r}, request={executed_request_model!r}'
        )
    if executed_agent_model and executed_agent_model != selected_native_model_id:
        raise RuntimeError(
            'Hermes active model does not match the router-selected model: '
            f'selected={selected_native_model_id!r}, active={executed_agent_model!r}'
        )
    if executed_agent_provider and executed_agent_provider != selected_hermes_provider:
        raise RuntimeError(
            'Hermes active provider does not match the router-selected provider: '
            f'selected={selected_hermes_provider!r}, active={executed_agent_provider!r}'
        )
    return {
        'selected_model_id': selected_model_id,
        'selected_native_model_id': selected_native_model_id,
        'selected_provider': selected_provider,
        'selected_hermes_provider': selected_hermes_provider,
        'executed_request_model': executed_request_model,
        'executed_agent_model': executed_agent_model or selected_native_model_id,
        'executed_agent_provider': executed_agent_provider or selected_hermes_provider,
        'request_identity_verified': True,
    }


def _verify_provider_reported_model(
    response: Any,
    entry: dict[str, Any],
    execution_identity: dict[str, Any],
) -> dict[str, Any]:
    reported_model = str(_response_field(response, 'model') or '').strip()
    if not reported_model:
        return execution_identity
    selected_model_id = str(entry.get('model_id') or '').strip()
    selected_native_model_id = str(entry.get('native_model_id') or '').strip()
    allowed_reported_models = {
        selected_model_id.lower(),
        selected_native_model_id.lower(),
        selected_model_id.split('/', 1)[-1].lower(),
    }
    if reported_model.lower() not in allowed_reported_models:
        raise RuntimeError(
            'Provider response model does not match the router-selected model: '
            f'selected={selected_native_model_id!r}, reported={reported_model!r}'
        )
    return {
        **execution_identity,
        'provider_reported_model': reported_model,
        'provider_reported_model_verified': True,
    }


def _select(state: dict[str, Any], request: dict[str, Any], routing_call_id: str) -> dict[str, Any]:
    prompt = request.get('prompt') if isinstance(request.get('prompt'), str) else None
    input_value = request.get('input') if isinstance(request.get('input'), str) else None
    setup_job = (state.get('last_setup_response') or {}).get('job') if isinstance(state.get('last_setup_response'), dict) else {}
    user_id = state.get('user_id') or (setup_job or {}).get('userId')
    payload = {
        'route_id': state.get('route_id', 'default'),
        'messages': request.get('messages'),
        'prompt': prompt,
        'input': input_value,
        'tools': request.get('tools'),
        'tool_choice': request.get('tool_choice'),
        'response_format': request.get('response_format'),
        'temperature': request.get('temperature'),
        'max_tokens': request.get('max_tokens'),
        'stream': bool(request.get('stream')),
        'inference_mode': state.get('inference_mode', 'user_managed'),
        'execute': False,
        'max_candidates': state.get('candidates_per_route', 5),
        'provider_options': {},
        'metadata': {
            'routing_call_id': routing_call_id,
            'session_id': SESSION_ID,
            'source': 'hermes_lerouter_plugin',
        },
    }
    if user_id:
        payload['user_id'] = user_id
    url = f"{state['base_url']}/lerouter/select"
    return _post_json(url, payload, state['api_key'])


def on_llm_execution_middleware(**kwargs: Any) -> Any:
    next_call = kwargs.get('next_call')
    if not callable(next_call):
        raise MiddlewareFailClosedError(
            'LeRouter requires the Hermes llm_execution next_call contract'
        )

    if os.environ.get('LEROUTER_DISABLE', '').strip().lower() in {'1', 'true', 'yes', 'on'}:
        return next_call(kwargs.get('request') or {})

    try:
        with _LOCK:
            state = _load_state()
        enabled = state.get('enabled')
        if enabled is not True and enabled is not False:
            raise RuntimeError('LeRouter state must declare enabled as true or false')
    except MiddlewareFailClosedError:
        raise
    except Exception as exc:
        safe_error = _safe_error_message(exc) or exc.__class__.__name__
        raise MiddlewareFailClosedError(
            f'LeRouter state validation failed before provider execution: {safe_error}'
        ) from None

    if enabled is False:
        return next_call(kwargs.get('request') or {})

    selection: dict[str, Any] = {}
    try:
        routing_call_id = _next_routing_call_id()
        _log_event(state, 'middleware_entered', routing_call_id=routing_call_id)

        agent = _current_agent()
        if agent is None:
            agent_error = RuntimeError('LeRouter could not locate the active Hermes agent')
            _log_event(
                state,
                'middleware_failed_no_agent',
                routing_call_id=routing_call_id,
                success=False,
                error=agent_error,
            )
            raise agent_error

        base_request, benchmark_output_limit = _prepare_benchmark_output_limit(
            state,
            dict(kwargs.get('request') or {}),
            agent,
        )
        api_messages = _current_api_messages()
        routing_request = dict(base_request)
        if isinstance(api_messages, list) and api_messages:
            routing_request['messages'] = api_messages

        route_name: str | None = None

        select_started = time.time()
        _log_event(state, 'select_started', routing_call_id=routing_call_id)
        try:
            selection = _select(state, routing_request, routing_call_id)
        except Exception as select_error:
            _log_event(
                state,
                'select_failed',
                routing_call_id=routing_call_id,
                success=False,
                latency_ms=int((time.time() - select_started) * 1000),
                error=select_error,
            )
            raise
        routing_call_id, _accounting_token = _require_accounting_claim(selection)
        route_name = selection.get('route_name') or selection.get('selected_route') or selection.get('route')
        candidate_attempts = _candidate_attempts(state, selection)
        if not candidate_attempts:
            selection_error = RuntimeError('LeRouter selected no executable Hermes candidate')
            _log_event(
                state,
                'select_failed',
                routing_call_id=routing_call_id,
                route_name=route_name,
                success=False,
                latency_ms=int((time.time() - select_started) * 1000),
                error=selection_error,
            )
            raise selection_error

        router_latency_ms = int((time.time() - select_started) * 1000)
        selected_entry = candidate_attempts[0]['entry']
        semantic_request_hash = _semantic_request_hash(base_request)
        retry_request = dict(base_request)
        previous_attempt_error: str | None = None
        previous_model_id: str | None = None
        retry_reason: str | None = None
        protocol_repairs: list[str] = []
        _log_event(
            state,
            'select_succeeded',
            routing_call_id=routing_call_id,
            route_name=route_name,
            entry=selected_entry,
            success=True,
            latency_ms=router_latency_ms,
            **{
                key: value
                for key, value in _selection_metadata(selection).items()
                if key.endswith('timings_ms')
            },
        )
    except MiddlewareFailClosedError:
        raise
    except Exception as exc:
        safe_error = _safe_error_message(
            exc,
            state.get('api_key'),
            (selection or {}).get('accounting_token') if isinstance(selection, dict) else None,
        ) or exc.__class__.__name__
        raise MiddlewareFailClosedError(
            f'LeRouter routing failed before selected-model execution: {safe_error}'
        ) from None

    for candidate_index, attempt in enumerate(candidate_attempts):
        selected_entry = attempt['entry']
        candidate_selection = attempt['selection']
        candidate_routing_call_id, _candidate_accounting_token = _require_accounting_claim(
            candidate_selection
        )
        _visible_routing_line(
            routing_call_id=candidate_routing_call_id,
            route_name=route_name,
            entry=selected_entry,
            router_latency_ms=router_latency_ms if candidate_index == 0 else None,
            candidate_index=candidate_index,
        )

        request = dict(retry_request)
        if not _activate_entry(agent, request, selected_entry):
            activation_error = RuntimeError(
                f"Hermes could not activate selected model {selected_entry.get('model_id')}"
            )
            _log_event(
                state,
                'execution_failed',
                routing_call_id=candidate_routing_call_id,
                route_name=route_name,
                entry=selected_entry,
                success=False,
                error=activation_error,
            )
            _log_usage(
                state,
                _usage_payload(
                    state,
                    routing_call_id=candidate_routing_call_id,
                    route_name=route_name,
                    entry=selected_entry,
                    success=False,
                    error=str(activation_error),
                    router_latency_ms=router_latency_ms,
                    selection=candidate_selection,
                    stream=bool(base_request.get('stream')),
                    semantic_request_hash=semantic_request_hash,
                    protocol_request_hash=_protocol_request_hash(request),
                    protocol_repairs=protocol_repairs,
                    previous_attempt_error=previous_attempt_error,
                    previous_model_id=previous_model_id,
                    retry_reason=retry_reason,
                ),
            )
            if candidate_index + 1 >= len(candidate_attempts) or not _is_retryable_execution_error(activation_error):
                raise activation_error
            retry_request, protocol_repairs = _repair_protocol_request(base_request, activation_error)
            previous_attempt_error = str(activation_error)
            previous_model_id = str(selected_entry.get('model_id') or '')
            retry_reason = 'activation_failure'
            next_attempt = candidate_attempts[candidate_index + 1]
            _log_event(
                state,
                'execution_candidate_retry',
                routing_call_id=next_attempt['selection'].get('routing_call_id'),
                route_name=route_name,
                entry=next_attempt['entry'],
                success=None,
                previous_model_id=previous_model_id,
                retry_reason=retry_reason,
                semantic_request_hash=semantic_request_hash,
                protocol_repairs=protocol_repairs,
                previous_attempt_error=previous_attempt_error,
            )
            continue

        try:
            _clamp_agent_output_tokens(agent, benchmark_output_limit)
            request = _rebuild_request_for_active_agent(agent, api_messages, request)
            request = _enforce_rebuilt_output_limit(
                agent,
                request,
                benchmark_output_limit,
            )
            request = _apply_entry_to_request(request, selected_entry, route_name=route_name)
            request = _apply_reasoning_capability(request, selected_entry)
            request = _bound_openai_prompt_cache_keys(request, selected_entry)
            execution_identity = _verify_selected_execution_identity(
                agent,
                request,
                selected_entry,
            )
            candidate_selection['execution_identity'] = dict(execution_identity)
        except CandidateCapabilityMismatchError as exc:
            _log_event(
                state,
                'execution_candidate_rejected',
                routing_call_id=candidate_routing_call_id,
                route_name=route_name,
                entry=selected_entry,
                success=False,
                error=exc,
                rejection_reason='model_capability_mismatch',
            )
            _log_usage(
                state,
                _usage_payload(
                    state,
                    routing_call_id=candidate_routing_call_id,
                    route_name=route_name,
                    entry=selected_entry,
                    success=False,
                    error=str(exc),
                    router_latency_ms=router_latency_ms,
                    selection=candidate_selection,
                    stream=bool(request.get('stream')),
                    semantic_request_hash=semantic_request_hash,
                    protocol_request_hash=_protocol_request_hash(request),
                    protocol_repairs=protocol_repairs,
                    previous_attempt_error=previous_attempt_error,
                    previous_model_id=previous_model_id,
                    retry_reason=retry_reason,
                ),
            )
            has_next_candidate = candidate_index + 1 < len(candidate_attempts)
            if not has_next_candidate:
                raise
            next_attempt = candidate_attempts[candidate_index + 1]
            _log_event(
                state,
                'execution_candidate_retry',
                routing_call_id=next_attempt['selection'].get('routing_call_id'),
                route_name=route_name,
                entry=next_attempt['entry'],
                success=None,
                previous_model_id=selected_entry.get('model_id'),
                retry_reason='model_capability_mismatch',
            )
            continue
        except MiddlewareFailClosedError:
            raise
        except Exception as exc:
            safe_error = _safe_error_message(
                exc,
                state.get('api_key'),
                candidate_selection.get('accounting_token'),
            ) or exc.__class__.__name__
            raise MiddlewareFailClosedError(
                'LeRouter failed to prepare the selected-model request: '
                f'{safe_error}'
            ) from None

        started = time.time()
        _log_event(
            state,
            'execution_started',
            routing_call_id=candidate_routing_call_id,
            route_name=route_name,
            entry=selected_entry,
            semantic_request_hash=semantic_request_hash,
            protocol_request_hash=_protocol_request_hash(request),
            protocol_repairs=protocol_repairs,
            execution_identity=dict(execution_identity),
        )

        try:
            # Hermes middleware callbacks are single-use. The one router-
            # selected model executes through the callback so the normal
            # middleware chain remains intact.
            response = _execute_request(
                agent,
                request,
                next_call if candidate_index == 0 else None,
            )
            latency_ms = int((time.time() - started) * 1000)
            try:
                execution_identity = _verify_provider_reported_model(
                    response,
                    selected_entry,
                    execution_identity,
                )
                candidate_selection['execution_identity'] = dict(execution_identity)
                _validate_direct_provider_response(response, selected_entry)
                success_payload = _usage_payload(
                    state,
                    routing_call_id=candidate_routing_call_id,
                    route_name=route_name,
                    entry=selected_entry,
                    success=True,
                    response=response,
                    latency_ms=latency_ms,
                    router_latency_ms=router_latency_ms,
                    selection=candidate_selection,
                    stream=bool(request.get('stream')),
                    semantic_request_hash=semantic_request_hash,
                    protocol_request_hash=_protocol_request_hash(request),
                    protocol_repairs=protocol_repairs,
                    previous_attempt_error=previous_attempt_error,
                    previous_model_id=previous_model_id,
                    retry_reason=retry_reason,
                )
            except MiddlewareFailClosedError as accounting_error:
                if not _is_retryable_success_accounting_error(
                    accounting_error,
                    selected_entry,
                ):
                    raise
                raise RuntimeError(
                    'malformed response: '
                    + (_safe_error_message(accounting_error) or 'token usage is unavailable')
                ) from None
            # Only expose execution_succeeded after both response validation
            # and authoritative usage logging have completed. A provider
            # response that cannot be billed therefore fails closed instead
            # of being marked successful.
            _log_usage(state, success_payload)
            _log_event(
                state,
                'execution_succeeded',
                routing_call_id=candidate_routing_call_id,
                route_name=route_name,
                entry=selected_entry,
                success=True,
                latency_ms=latency_ms,
                execution_identity=dict(execution_identity),
            )
            return response
        except MiddlewareFailClosedError:
            raise
        except Exception as exc:
            latency_ms = int((time.time() - started) * 1000)
            _log_event(
                state,
                'execution_failed',
                routing_call_id=candidate_routing_call_id,
                route_name=route_name,
                entry=selected_entry,
                success=False,
                latency_ms=latency_ms,
                error=exc,
            )
            _log_usage(
                state,
                _usage_payload(
                    state,
                    routing_call_id=candidate_routing_call_id,
                    route_name=route_name,
                    entry=selected_entry,
                    success=False,
                    error=str(exc),
                    latency_ms=latency_ms,
                    router_latency_ms=router_latency_ms,
                    selection=candidate_selection,
                    stream=bool(request.get('stream')),
                    semantic_request_hash=semantic_request_hash,
                    protocol_request_hash=_protocol_request_hash(request),
                    protocol_repairs=protocol_repairs,
                    previous_attempt_error=previous_attempt_error,
                    previous_model_id=previous_model_id,
                    retry_reason=retry_reason,
                ),
            )
            has_next_candidate = candidate_index + 1 < len(candidate_attempts)
            if not has_next_candidate or not _is_retryable_execution_error(exc):
                raise
            next_attempt = candidate_attempts[candidate_index + 1]
            retry_request, protocol_repairs = _repair_protocol_request(base_request, exc)
            previous_attempt_error = str(exc)
            previous_model_id = str(selected_entry.get('model_id') or '')
            retry_reason = 'technical_failure'
            _log_event(
                state,
                'execution_candidate_retry',
                routing_call_id=next_attempt['selection'].get('routing_call_id'),
                route_name=route_name,
                entry=next_attempt['entry'],
                success=None,
                retry_reason=retry_reason or 'technical_failure',
                semantic_request_hash=semantic_request_hash,
                protocol_repairs=protocol_repairs,
                previous_attempt_error=previous_attempt_error,
                previous_model_id=previous_model_id,
            )

    raise RuntimeError('LeRouter exhausted every signed ranked execution candidate')


def register(ctx) -> None:
    ctx.register_middleware('llm_execution', on_llm_execution_middleware)
    try:
        state = _load_state()
        if state.get('enabled'):
            _log_event(state, 'plugin_registered')
    except Exception as exc:
        logger.debug(
            'LeRouter plugin_registered event failed: %s',
            _safe_error_message(exc),
        )
