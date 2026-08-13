#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
import re
import shutil
import ssl
import sqlite3
import sys
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import certifi
except ModuleNotFoundError:
    certifi = None

HERMES_HOME = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
REPO = HERMES_HOME / 'hermes-agent'
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_DIR = HERMES_HOME / 'lerouter-user-managed'
STATE_PATH = STATE_DIR / 'state.json'
EVENTS_PATH = STATE_DIR / 'events.jsonl'
EVENTX_PATH = STATE_DIR / 'eventx.json'
ENV_PATH = HERMES_HOME / '.env'
STATE_DB_PATH = HERMES_HOME / 'state.db'

LEROUTER_BASE_URL = os.environ['LEROUTER_API_URL'].rstrip('/')
LEROUTER_DASHBOARD_URL = os.environ.get('LEROUTER_DASHBOARD_URL', 'http://localhost:3000').rstrip('/')
LEROUTER_API_KEY = os.environ['LEROUTER_AGENT_TOKEN']
ROUTE_ID = os.environ.get('LEROUTER_ROUTE_ID', 'route_workspace')
INFERENCE_MODE = 'user_managed'
UPDATE_SCHEDULE = '7d'
CANDIDATES_PER_ROUTE = 7
OPENROUTER_MODELS_URL = os.environ.get('OPENROUTER_MODELS_URL', 'https://openrouter.ai/api/v1/models')
TOGETHER_MODELS_URL = os.environ.get('TOGETHER_MODELS_URL', 'https://api.together.ai/v1/models')
TOGETHER_ENDPOINTS_URL = os.environ.get('TOGETHER_ENDPOINTS_URL', 'https://api.together.ai/v1/endpoints')
TOGETHER_CHAT_COMPLETIONS_URL = os.environ.get(
    'TOGETHER_CHAT_COMPLETIONS_URL',
    'https://api.together.xyz/v1/chat/completions',
)
BUDGET_CYCLE_DAYS = {
    'weekly': 7,
    'monthly': 30,
    'quarterly': 91,
    'yearly': 365,
}
DEFAULT_OUTPUT_TOKEN_WEIGHT = 5.0
DEFAULT_REQUEST_WEIGHT_BETA = 1.0
DEFAULT_REQUEST_DIFFICULTY_ALPHA = 2.0
DEFAULT_REQUEST_WEIGHT_MIN = 0.05
DEFAULT_REQUEST_WEIGHT_CAP_MULTIPLIER = 4.0
MIN_USER_ROUTES = 5
MAX_USER_ROUTES = 7
SETUP_JOB_POLL_INTERVAL_SECONDS = 2
SETUP_JOB_POLL_WINDOW_SECONDS = 3600
SECRET_ENV_MARKERS = ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD')

STOPWORDS = {
    'about', 'after', 'again', 'against', 'and', 'avec', 'before', 'being', 'bring', 'bringing',
    'can', 'cannot', 'dans', 'does', 'done', 'dont', 'each', 'elle', 'exactly', 'for', 'from',
    'has', 'have', 'into', 'line', 'listed', 'mais', 'make', 'more', 'new', 'nous', 'one',
    'pour', 'record', 'records', 'should', 'that', 'the', 'this', 'through', 'use', 'value',
    'values', 'was', 'what', 'when', 'where', 'which', 'with', 'would', 'your',
}

ROUTE_FAMILIES = {
    'structured_data_operations': {
        'keywords': {
            'csv', 'data', 'database', 'field', 'game_results', 'insert', 'json', 'record',
            'row', 'schema', 'score', 'spreadsheet', 'sql', 'standings', 'table', 'update',
            'values',
        },
        'label': 'structured data operations',
        'suffix': 'data_ops',
    },
    'software_engineering': {
        'keywords': {
            'api', 'bug', 'cli', 'code', 'command', 'debug', 'deploy', 'error', 'file',
            'fix', 'function', 'hermes', 'javascript', 'modal', 'openclaw', 'python', 'repo',
            'script', 'setup', 'test', 'typescript', 'vm',
        },
        'label': 'coding, tools, and debugging',
        'suffix': 'engineering',
    },
    'research_lookup': {
        'keywords': {
            'credential', 'credentials', 'docs', 'find', 'lookup', 'mission', 'presentation',
            'question', 'search', 'source', 'termination', 'who',
        },
        'label': 'factual lookup and record matching',
        'suffix': 'lookup',
    },
    'planning_reasoning': {
        'keywords': {
            'analyze', 'architecture', 'budget', 'compare', 'decide', 'design', 'investigate',
            'plan', 'reason', 'route', 'strategy', 'tradeoff', 'why',
        },
        'label': 'planning and analytical reasoning',
        'suffix': 'reasoning',
    },
    'document_content': {
        'keywords': {
            'document', 'docx', 'extract', 'pdf', 'presentation', 'report', 'slides',
            'summarize', 'write',
        },
        'label': 'document and content work',
        'suffix': 'documents',
    },
}

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def configured_secret_values() -> list[str]:
    return [
        value.strip()
        for name, value in os.environ.items()
        if any(marker in name.upper() for marker in SECRET_ENV_MARKERS)
        and isinstance(value, str)
        and len(value.strip()) >= 8
    ]


def redact_secret_text(value: Any, *secrets: Any) -> str:
    text = str(value)
    for secret in [*configured_secret_values(), LEROUTER_API_KEY, *secrets]:
        if isinstance(secret, str) and secret:
            text = text.replace(secret, '<redacted>')
    text = re.sub(r'(?i)\bBearer\s+[^\s"\']+', 'Bearer <redacted>', text)
    return re.sub(
        r'(?i)\b(?:sk-|lr_live_|tgp_)[A-Za-z0-9._~+/=-]+',
        '<redacted>',
        text,
    )


def safe_error_message(error: Any) -> str | None:
    if error is None:
        return None
    return redact_secret_text(error)[:500]


def post_dashboard_setup_event(event: dict[str, Any]) -> None:
    if not LEROUTER_DASHBOARD_URL:
        return
    payload = {
        'routeId': ROUTE_ID,
        'routeName': event.get('route_name'),
        'provider': event.get('provider') or 'lerouter',
        'modelId': event.get('model_id') or 'lerouter/setup',
        'success': bool(event.get('success')),
        'spendUsd': 0,
        'metadata': {
            'kind': 'routing_operation',
            'operation': event.get('event'),
            'session_id': event.get('session_id'),
            'error_type': event.get('error_type'),
            'error_message': event.get('error_message'),
            'source': 'hermes_lerouter_setup',
        },
    }
    try:
        subprocess.run(
            [
                'curl', '-sS',
                '-H', f'Authorization: Bearer {LEROUTER_API_KEY}',
                '-H', 'Content-Type: application/json',
                '-X', 'POST',
                f'{LEROUTER_DASHBOARD_URL}/api/usage-log',
                '--data-binary', '@-',
            ],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except Exception:
        pass


def write_setup_event(event_name: str, *, success: bool = True, error: Any = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE_DIR.chmod(0o700)
    event = {
        'ts': iso_now(),
        'session_id': 'hermes-setup',
        'event': event_name,
        'route_id': ROUTE_ID,
        'route_name': None,
        'provider': 'lerouter',
        'model_id': 'lerouter/setup',
        'success': bool(success),
        'latency_ms': None,
        'error_type': error.__class__.__name__ if isinstance(error, Exception) else None,
        'error_message': safe_error_message(error),
    }
    with EVENTS_PATH.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + '\n')
    EVENTS_PATH.chmod(0o600)
    snapshot = {'version': 1, 'updated_at': event['ts'], 'events': []}
    try:
        if EVENTX_PATH.exists():
            current = json.loads(EVENTX_PATH.read_text(encoding='utf-8'))
            if isinstance(current, dict):
                snapshot['version'] = current.get('version') or 1
                snapshot['events'] = list(current.get('events') or [])
    except Exception:
        pass
    snapshot['updated_at'] = event['ts']
    snapshot['events'] = [*snapshot['events'], event][-500:]
    EVENTX_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    EVENTX_PATH.chmod(0o600)
    post_dashboard_setup_event(event)


def ensure_hermes_python() -> None:
    venv_python = REPO / 'venv' / 'bin' / 'python'
    if not venv_python.exists():
        return
    try:
        current_python = Path(sys.executable).resolve()
        target_python = venv_python.resolve()
    except OSError:
        current_python = Path(sys.executable)
        target_python = venv_python
    if current_python == target_python:
        return
    if os.environ.get('LEROUTER_HERMES_PYTHON_REEXEC') == '1':
        return
    os.environ['LEROUTER_HERMES_PYTHON_REEXEC'] = '1'
    os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])


SUPPORTED_NATIVE_PROVIDERS = {
    'openai-codex',
    'openai',
    'openai-api',
    'copilot',
    'anthropic',
    'google',
    'gemini',
    'deepseek',
    'xai',
    'xai-oauth',
    'groq',
    'mistral',
    'together',
    'togetherai',
    'openrouter',
}

PROVIDER_RUNTIME_ALIASES = {
    'togetherai': 'together',
}

CATALOG_PROVIDER_ALIASES = {
    'openai-api': 'openai',
}

PROVIDER_KEY_HINTS = {
    'openai': ['OPENAI_API_KEY'],
    'openai-api': ['OPENAI_API_KEY'],
    'anthropic': ['ANTHROPIC_API_KEY'],
    'google': ['GOOGLE_API_KEY', 'GEMINI_API_KEY'],
    'deepseek': ['DEEPSEEK_API_KEY'],
    'xai': ['XAI_API_KEY'],
    'groq': ['GROQ_API_KEY'],
    'mistral': ['MISTRAL_API_KEY'],
    'together': ['TOGETHER_API_KEY', 'TOGETHER_AI_API_KEY'],
    'openrouter': ['OPENROUTER_API_KEY'],
}

METADATA = {
    'source': 'hermes_setup',
    'inference_mode': INFERENCE_MODE,
    'budget': {
        'amount_usd': 60,
        'cycle': 'weekly',
    },
    'route_update_policy': 'Review recent user conversations weekly. Update routes only when the user has a genuinely new recurring agent use case or has clearly changed their usage pattern. If no stable change is detected, make no route changes.',
}


def tokenize_text(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r'[a-zA-Z][a-zA-Z0-9_]{2,}', text.lower())
        if token not in STOPWORDS and not token.startswith(('lr_live_', 'sk_'))
    ]


def redact_text(text: str) -> str:
    text = re.sub(r'lr_live_[A-Za-z0-9_-]+', 'lr_live_<redacted>', text)
    text = re.sub(r'sk-[A-Za-z0-9_-]+', 'sk-<redacted>', text)
    text = re.sub(r'Bearer\s+[A-Za-z0-9._-]+', 'Bearer <redacted>', text)
    return text


def is_setup_prompt(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            'lerouter_agent_token',
            'lerouter api',
            '/agent/setup-jobs',
            'install lerouter',
            'curl -fsSL',
            'hermes, install lerouter',
            'reply with exactly',
            'lerouter_smoke_ok',
            'smoke_ok',
            'smoke test',
        )
    )


def budget_cycle_days(cycle: str | None) -> float:
    return BUDGET_CYCLE_DAYS.get(str(cycle or 'monthly').strip().lower(), 30.0)


def current_budget_cycle_days() -> float:
    budget = METADATA.get('budget') if isinstance(METADATA.get('budget'), dict) else {}
    return budget_cycle_days(budget.get('cycle'))


def collect_hermes_history_samples(limit: int = 500) -> list[dict[str, Any]]:
    if not STATE_DB_PATH.exists():
        return []
    cycle_days = current_budget_cycle_days()
    cutoff = time.time() - (cycle_days * 86400)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(STATE_DB_PATH))
        rows = connection.execute(
            """
            select
              m.content,
              m.timestamp,
              m.token_count,
              s.input_tokens,
              s.output_tokens,
              s.api_call_count
            from messages m
            left join sessions s on s.id = m.session_id
            where m.role = 'user'
              and m.content is not null
              and length(trim(m.content)) > 0
              and coalesce(m.timestamp, s.started_at, 0) >= ?
            order by coalesce(m.timestamp, s.started_at, m.id) asc
            limit ?
            """,
            (cutoff, limit),
        ).fetchall()
    except (OSError, sqlite3.Error) as error:
        raise RuntimeError(
            f'Failed to read Hermes budget history from {STATE_DB_PATH}: '
            f'{safe_error_message(error)}'
        ) from None
    finally:
        if connection is not None:
            connection.close()

    samples: list[dict[str, Any]] = []
    for content, timestamp, token_count, session_input_tokens, session_output_tokens, api_call_count in rows:
        text = redact_text(str(content or '').strip())
        if len(text) < 12 or is_setup_prompt(text):
            continue
        request_count = max(1.0, float(api_call_count or 0) or 1.0)
        input_tokens = float(token_count or 0) if token_count else None
        if input_tokens is None and session_input_tokens:
            input_tokens = max(1.0, float(session_input_tokens) / request_count)
        output_tokens = max(1.0, float(session_output_tokens) / request_count) if session_output_tokens else None
        if (
            input_tokens is None
            or input_tokens <= 0
            or output_tokens is None
            or output_tokens <= 0
        ):
            continue
        sample = {
            'content': text[:6000],
            'timestamp': float(timestamp or time.time()),
            'request_count': request_count,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
        }
        samples.append(sample)
    return samples


def collect_hermes_history_messages(limit: int = 120) -> list[str]:
    if not STATE_DB_PATH.exists():
        return []
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(STATE_DB_PATH))
        rows = connection.execute(
            """
            select content
            from messages
            where role = 'user'
              and content is not null
              and length(trim(content)) > 0
            order by coalesce(timestamp, id) desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    except (OSError, sqlite3.Error) as error:
        raise RuntimeError(
            f'Failed to read Hermes route history from {STATE_DB_PATH}: '
            f'{safe_error_message(error)}'
        ) from None
    finally:
        if connection is not None:
            connection.close()

    messages: list[str] = []
    for (content,) in rows:
        text = redact_text(str(content or '').strip())
        if len(text) < 12 or is_setup_prompt(text):
            continue
        messages.append(text[:1200])
    return list(reversed(messages))


def family_for_message(message: str) -> str:
    tokens = set(tokenize_text(message))
    scored = []
    for family_name, definition in ROUTE_FAMILIES.items():
        overlap = len(tokens & definition['keywords'])
        scored.append((overlap, family_name))
    scored.sort(reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][1]
    if '?' in message:
        return 'research_lookup'
    return 'planning_reasoning'


def route_name_for_family(family_name: str, messages: list[str]) -> str:
    definition = ROUTE_FAMILIES[family_name]
    tokens = [
        token
        for message in messages
        for token in tokenize_text(message)
    ]
    common = [
        token
        for token, _count in Counter(tokens).most_common(3)
        if not token.isdigit()
    ]
    stem = '_'.join(common[:2]) if common else family_name
    stem = re.sub(r'[^a-z0-9_]+', '_', stem).strip('_')[:36] or family_name
    suffix = definition['suffix']
    if stem.endswith(suffix):
        return stem
    return f'{stem}_{suffix}'[:48]


def derive_routes_from_hermes_history() -> tuple[dict[str, Any], dict[str, Any]]:
    messages = collect_hermes_history_messages()
    if not messages:
        return {}, {
            'source': 'hermes_state_db',
            'message_count': 0,
            'routes_generated': 0,
            'route_range_satisfied': False,
        }

    grouped: dict[str, list[str]] = defaultdict(list)
    for message in messages:
        grouped[family_for_message(message)].append(message)

    routes: dict[str, Any] = {}
    for family_name, family_messages in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True):
        if not family_messages:
            continue
        route_name = route_name_for_family(family_name, family_messages)
        definition = ROUTE_FAMILIES[family_name]
        top_terms = [
            token
            for token, _count in Counter(
                token
                for message in family_messages
                for token in tokenize_text(message)
            ).most_common(6)
        ]
        routes[route_name] = {
            'trigger': f"Recurring Hermes history pattern: {definition['label']}.",
            'task': f"Route requests like the user's recent {definition['label']} work.",
            'source': 'hermes_history',
            'history_message_count': len(family_messages),
            'top_terms': top_terms,
        }
        if len(routes) >= MAX_USER_ROUTES:
            break

    return routes, {
        'source': 'hermes_state_db',
        'message_count': len(messages),
        'routes_generated': len(routes),
        'route_range_satisfied': route_count_in_user_range(routes),
        'families': {family_name: len(items) for family_name, items in grouped.items()},
    }


def route_count_in_user_range(routes: dict[str, Any]) -> bool:
    return MIN_USER_ROUTES <= len(routes) <= MAX_USER_ROUTES


def route_count_summary(routes: dict[str, Any]) -> dict[str, Any]:
    return {
        'routes_generated': len(routes),
        'min_user_routes': MIN_USER_ROUTES,
        'max_user_routes': MAX_USER_ROUTES,
        'target_route_range': f'{MIN_USER_ROUTES}-{MAX_USER_ROUTES}',
        'route_range_satisfied': route_count_in_user_range(routes),
    }


def insufficient_route_count_error(
    *,
    existing_route_count: int | None,
    history_route_count: int,
    merged_route_count: int | None = None,
) -> ValueError:
    if existing_route_count is None:
        actual_counts = f'found {history_route_count} history-derived user routes'
    else:
        actual_counts = (
            f'found {existing_route_count} user routes in existing state and '
            f'{history_route_count} history-derived user routes, yielding '
            f'{merged_route_count} unique merged routes'
        )
    return ValueError(
        f'LeRouter setup {actual_counts}; it requires between {MIN_USER_ROUTES} and '
        f'{MAX_USER_ROUTES}. Provide LEROUTER_ROUTES_JSON with {MIN_USER_ROUTES}-{MAX_USER_ROUTES} '
        'explicit routes or supply sufficient Hermes history to derive that many routes.'
    )


def load_routes() -> tuple[dict[str, Any], dict[str, Any]]:
    raw_routes = os.environ.get('LEROUTER_ROUTES_JSON', '').strip()
    if raw_routes:
        routes = json.loads(raw_routes)
        if not isinstance(routes, dict) or not routes:
            raise ValueError('LEROUTER_ROUTES_JSON must be a non-empty JSON object keyed by route name')
        if not route_count_in_user_range(routes):
            raise ValueError(
                f'LEROUTER_ROUTES_JSON must define between {MIN_USER_ROUTES} and {MAX_USER_ROUTES} user routes; '
                f'got {len(routes)}.'
            )
        return routes, {'source': 'env', **route_count_summary(routes)}

    existing_routes: dict[str, Any] = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f'Failed to read Hermes LeRouter state from {STATE_PATH}: '
                f'{safe_error_message(error)}'
            ) from None
        if not isinstance(state, dict):
            raise RuntimeError(
                f'Hermes LeRouter state at {STATE_PATH} must be a JSON object; '
                f'got {type(state).__name__}.'
            )
        if 'routes' not in state:
            raise RuntimeError(
                f'Hermes LeRouter state at {STATE_PATH} is missing the required routes object.'
            )
        routes = state['routes']
        if not isinstance(routes, dict):
            raise RuntimeError(
                f'Hermes LeRouter state routes at {STATE_PATH} must be a JSON object; '
                f'got {type(routes).__name__}.'
            )
        if routes:
            existing_routes = dict(routes)

    if route_count_in_user_range(existing_routes):
        return existing_routes, {
            'source': 'existing_state',
            **route_count_summary(existing_routes),
        }

    history_routes, history_summary = derive_routes_from_hermes_history()
    merged_routes = dict(existing_routes)
    for route_name, route_definition in history_routes.items():
        merged_routes.setdefault(route_name, route_definition)
    merged_routes = dict(list(merged_routes.items())[:MAX_USER_ROUTES])

    if existing_routes and route_count_in_user_range(merged_routes):
        return merged_routes, {
            **history_summary,
            **route_count_summary(merged_routes),
            'source': 'existing_state_history_merge',
            'existing_route_count': len(existing_routes),
            'history_route_count': len(history_routes),
        }

    if route_count_in_user_range(history_routes):
        summary = {
            **history_summary,
            **route_count_summary(history_routes),
        }
        if existing_routes:
            summary['ignored_existing_route_count'] = len(existing_routes)
        return history_routes, summary

    raise insufficient_route_count_error(
        existing_route_count=len(existing_routes) if existing_routes else None,
        history_route_count=len(history_routes),
        merged_route_count=len(merged_routes) if existing_routes else None,
    )


def enrich_model(provider: str, native_model_id: str) -> dict[str, Any]:
    hermes_provider = str(provider or '').strip().lower()
    provider = CATALOG_PROVIDER_ALIASES.get(hermes_provider, hermes_provider)
    native_model_id = str(native_model_id or '').strip()
    for provider_prefix in (f'{hermes_provider}/', f'{provider}/'):
        if native_model_id.lower().startswith(provider_prefix):
            native_model_id = native_model_id[len(provider_prefix):]
            break
    model = {
        'model_id': f'{provider}/{native_model_id}',
        'canonical_model_id': (
            native_model_id.lower()
            if provider == 'together' and '/' in native_model_id
            else f'{provider}/{native_model_id}'.lower()
        ),
        'provider': provider,
        'execution_provider': provider,
        'native_model_id': native_model_id,
        'openrouter_native_model_id': '',
        'execution': 'hermes_native',
        'inference_mode': INFERENCE_MODE,
    }
    if hermes_provider != provider:
        model['hermes_provider'] = hermes_provider
    return model


def openrouter_price_per_million(value: Any) -> float:
    try:
        return max(0.0, float(value) * 1_000_000)
    except (TypeError, ValueError):
        return 0.0


def enrich_openrouter_model(raw_model: dict[str, Any]) -> dict[str, Any] | None:
    native_model_id = str(raw_model.get('id') or raw_model.get('canonicalSlug') or raw_model.get('canonical_slug') or '').strip()
    if not native_model_id:
        return None
    if native_model_id.startswith('openrouter/'):
        native_model_id = native_model_id.split('/', 1)[1]

    pricing = raw_model.get('pricing') if isinstance(raw_model.get('pricing'), dict) else {}
    supported_parameters = {
        str(parameter).strip().lower()
        for parameter in raw_model.get('supported_parameters', raw_model.get('supportedParameters', [])) or []
    }
    context_window = raw_model.get('context_length', raw_model.get('contextLength'))
    try:
        context_window = int(context_window)
    except (TypeError, ValueError):
        context_window = None

    model = enrich_model('openrouter', native_model_id)
    model.update({
        'model_id': f'openrouter/{native_model_id}',
        'canonical_model_id': native_model_id.lower(),
        'execution_provider': 'openrouter',
        'native_model_id': native_model_id,
        'openrouter_native_model_id': native_model_id,
        'input_price_per_million': openrouter_price_per_million(pricing.get('prompt')),
        'output_price_per_million': openrouter_price_per_million(pricing.get('completion')),
        'supports_tools': 'tools' in supported_parameters or 'tool_choice' in supported_parameters,
        'supports_json': (
            'response_format' in supported_parameters
            or 'structured_outputs' in supported_parameters
            or 'json_schema' in supported_parameters
        ),
        'openrouter_name': raw_model.get('name'),
    })
    if context_window is not None and context_window > 0:
        model['context_window'] = context_window
    return model


OPENROUTER_PROVIDER_OWNERS = {
    'openai': ('openai',),
    'anthropic': ('anthropic',),
    'google': ('google',),
    'gemini': ('google',),
    'deepseek': ('deepseek',),
    'xai': ('x-ai',),
    'groq': ('groq',),
    'mistral': ('mistralai', 'mistral'),
}


def attach_openrouter_mapping(
    model: dict[str, Any],
    *,
    openrouter_model_ids: set[str],
) -> dict[str, Any]:
    provider = str(model.get('provider') or '').strip().lower()
    native_model_id = str(model.get('native_model_id') or '').strip()
    if not native_model_id or not openrouter_model_ids:
        return model

    candidates = [native_model_id] if provider == 'together' else [
        f'{owner}/{native_model_id}'
        for owner in OPENROUTER_PROVIDER_OWNERS.get(provider, ())
    ]
    openrouter_ids_by_lower = {
        model_id.lower(): model_id
        for model_id in openrouter_model_ids
    }
    openrouter_model_id = next((
        openrouter_ids_by_lower[candidate.lower()]
        for candidate in candidates
        if candidate.lower() in openrouter_ids_by_lower
    ), '')
    if openrouter_model_id:
        model['openrouter_native_model_id'] = openrouter_model_id
        model['canonical_model_id'] = openrouter_model_id.lower()
    return model


def fetch_openrouter_model_catalog() -> list[dict[str, Any]]:
    api_key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if not api_key:
        return []

    request = urllib.request.Request(
        f'{OPENROUTER_MODELS_URL}?output_modalities=text',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Accept': 'application/json',
        },
    )
    try:
        context = ssl.create_default_context(cafile=certifi.where()) if certifi else None
        with urllib.request.urlopen(request, timeout=20, context=context) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f'OpenRouter model discovery failed: {safe_error_message(error)}'
        ) from None

    raw_models = payload.get('data') if isinstance(payload, dict) else payload
    if not isinstance(raw_models, list):
        raise RuntimeError('OpenRouter model discovery returned no model list')

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        model = enrich_openrouter_model(raw_model)
        if not model:
            continue
        model_id = str(model.get('model_id') or '')
        if not model_id or model_id in seen:
            continue
        models.append(model)
        seen.add(model_id)
    if not models:
        raise RuntimeError('OpenRouter model discovery returned zero usable text models')
    return models


def together_price_per_million(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def fetch_together_json(url: str, api_key: str, resource_name: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Accept': 'application/json',
            'User-Agent': 'LeRouterHermesInstaller/1.0',
        },
    )
    try:
        context = ssl.create_default_context(cafile=certifi.where()) if certifi else None
        with urllib.request.urlopen(request, timeout=20, context=context) as response:
            return json.loads(response.read().decode('utf-8'))
    except (OSError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f'Together {resource_name} discovery failed: {safe_error_message(error)}'
        ) from None


def together_response_items(payload: Any, resource_name: str) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError(f'Together {resource_name} discovery returned no {resource_name} list')
    return [item for item in items if isinstance(item, dict)]


def together_capability_names(raw_model: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for field in (
        'capabilities',
        'supported_parameters',
        'supportedParameters',
        'supported_features',
        'supportedFeatures',
    ):
        value = raw_model.get(field)
        if isinstance(value, (list, tuple, set)):
            names.update(
                str(item).strip().lower().replace('-', '_')
                for item in value
                if isinstance(item, str) and item.strip()
            )
        elif isinstance(value, dict):
            for name, enabled in value.items():
                if enabled is True or (
                    isinstance(enabled, dict)
                    and any(enabled.get(key) is True for key in ('supported', 'enabled', 'available'))
                ):
                    names.add(str(name).strip().lower().replace('-', '_'))
    return names


def together_supports_capability(
    raw_model: dict[str, Any],
    *,
    boolean_fields: tuple[str, ...],
    capability_names: set[str],
) -> bool:
    for field in boolean_fields:
        value = raw_model.get(field)
        if isinstance(value, bool):
            return value
    return bool(together_capability_names(raw_model) & capability_names)


def probe_together_tool_support(native_model_id: str, api_key: str) -> dict[str, Any]:
    payload = {
        'model': native_model_id,
        'messages': [
            {
                'role': 'user',
                'content': 'Call the lookup tool exactly once with value ping.',
            }
        ],
        'tools': [
            {
                'type': 'function',
                'function': {
                    'name': 'lookup',
                    'description': 'Return a value.',
                    'parameters': {
                        'type': 'object',
                        'properties': {'value': {'type': 'string'}},
                        'required': ['value'],
                        'additionalProperties': False,
                    },
                },
            }
        ],
        'tool_choice': 'required',
        'max_tokens': 64,
        'temperature': 0,
    }
    request = urllib.request.Request(
        TOGETHER_CHAT_COMPLETIONS_URL,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'LeRouterHermesInstaller/1.0',
        },
        method='POST',
    )
    started = time.perf_counter()
    try:
        context = ssl.create_default_context(cafile=certifi.where()) if certifi else None
        with urllib.request.urlopen(request, timeout=60, context=context) as response:
            response_payload = json.loads(response.read().decode('utf-8'))
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError):
            try:
                response_body = error.read().decode('utf-8', errors='replace')
            except Exception:
                response_body = ''
            error_message = f'HTTP {error.code}: {response_body}'
        else:
            error_message = safe_error_message(error) or error.__class__.__name__
        return {
            'supports_tools': False,
            'source': 'live_chat_completions_probe',
            'latency_ms': round((time.perf_counter() - started) * 1000, 2),
            'error': redact_secret_text(error_message, api_key)[:500],
        }

    choices = response_payload.get('choices') if isinstance(response_payload, dict) else None
    first_choice = choices[0] if isinstance(choices, list) and choices else {}
    message = first_choice.get('message') if isinstance(first_choice, dict) else {}
    tool_calls = message.get('tool_calls') if isinstance(message, dict) else None
    supports_tools = isinstance(tool_calls, list) and bool(tool_calls)
    raw_usage = response_payload.get('usage') if isinstance(response_payload, dict) else None
    usage = raw_usage if isinstance(raw_usage, dict) else {}
    prompt_tokens = max(0, int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0))
    completion_tokens = max(0, int(usage.get('completion_tokens') or usage.get('output_tokens') or 0))
    usage_accounting_status = (
        'provider_reported'
        if isinstance(raw_usage, dict)
        and (
            'prompt_tokens' in raw_usage
            or 'input_tokens' in raw_usage
        )
        and (
            'completion_tokens' in raw_usage
            or 'output_tokens' in raw_usage
        )
        else 'missing'
    )
    return {
        'supports_tools': supports_tools,
        'source': 'live_chat_completions_probe',
        'latency_ms': round((time.perf_counter() - started) * 1000, 2),
        'finish_reason': first_choice.get('finish_reason') if isinstance(first_choice, dict) else None,
        'tool_call_count': len(tool_calls) if isinstance(tool_calls, list) else 0,
        'usage': {
            'input_tokens': prompt_tokens,
            'output_tokens': completion_tokens,
            'total_tokens': prompt_tokens + completion_tokens,
        },
        'usage_accounting_status': usage_accounting_status,
        'error': None if supports_tools else 'Successful response did not contain a tool call',
    }


def enrich_together_model(raw_model: dict[str, Any], endpoint: dict[str, Any]) -> dict[str, Any]:
    native_model_id = str(raw_model['id']).strip()
    model = enrich_model('together', native_model_id)
    pricing = raw_model.get('pricing') if isinstance(raw_model.get('pricing'), dict) else {}
    input_price = together_price_per_million(pricing.get('input'))
    output_price = together_price_per_million(pricing.get('output'))
    try:
        context_window = int(raw_model.get('context_length'))
    except (TypeError, ValueError):
        context_window = None

    supports_tools = together_supports_capability(
        raw_model,
        boolean_fields=('supports_tools', 'supportsTools'),
        capability_names={'tools', 'tool_choice', 'function_call', 'function_calling'},
    )
    supports_json = together_supports_capability(
        raw_model,
        boolean_fields=('supports_json', 'supportsJson'),
        capability_names={
            'response_format',
            'structured_output',
            'structured_outputs',
            'json',
            'json_mode',
            'json_schema',
        },
    )
    model.update({
        'together_display_name': raw_model.get('display_name'),
        'together_endpoint_id': endpoint.get('id'),
        'together_endpoint_type': endpoint.get('type'),
        'together_endpoint_state': endpoint.get('state'),
        'supports_tools': supports_tools,
        'supports_json': supports_json,
    })
    if input_price is not None:
        model['input_price_per_million'] = input_price
    if output_price is not None:
        model['output_price_per_million'] = output_price
    if context_window is not None and context_window > 0:
        model['context_window'] = context_window
    return model


def fetch_together_model_catalog() -> list[dict[str, Any]]:
    api_key = (
        os.environ.get('TOGETHER_API_KEY', '').strip()
        or os.environ.get('TOGETHER_AI_API_KEY', '').strip()
    )
    if not api_key:
        raise RuntimeError('Together model discovery requires TOGETHER_API_KEY or TOGETHER_AI_API_KEY')

    raw_models = together_response_items(
        fetch_together_json(TOGETHER_MODELS_URL, api_key, 'model'),
        'model',
    )
    raw_endpoints = together_response_items(
        fetch_together_json(TOGETHER_ENDPOINTS_URL, api_key, 'endpoint'),
        'endpoint',
    )
    live_endpoints: dict[str, dict[str, Any]] = {}
    for endpoint in raw_endpoints:
        if endpoint.get('type') != 'serverless' or endpoint.get('state') != 'STARTED':
            continue
        native_model_id = endpoint.get('model')
        if not isinstance(native_model_id, str) or not native_model_id.strip():
            continue
        live_endpoints.setdefault(native_model_id.strip(), endpoint)

    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_model in raw_models:
        native_model_id = raw_model.get('id')
        if not isinstance(native_model_id, str) or not native_model_id.strip():
            continue
        native_model_id = native_model_id.strip()
        if raw_model.get('type') != 'chat' or native_model_id not in live_endpoints or native_model_id in seen:
            continue
        model = enrich_together_model(raw_model, live_endpoints[native_model_id])
        tool_probe = probe_together_tool_support(native_model_id, api_key)
        probe_usage = tool_probe.get('usage') if isinstance(tool_probe.get('usage'), dict) else {}
        probe_input_tokens = max(0, int(probe_usage.get('input_tokens') or 0))
        probe_output_tokens = max(0, int(probe_usage.get('output_tokens') or 0))
        input_price = float(model.get('input_price_per_million') or 0)
        output_price = float(model.get('output_price_per_million') or 0)
        tool_probe['estimated_spend_usd'] = round(
            (
                probe_input_tokens * input_price
                + probe_output_tokens * output_price
            )
            / 1_000_000,
            10,
        )
        tool_probe['cost_source'] = 'provider_reported_tokens_x_live_catalog_price'
        model['supports_tools'] = tool_probe['supports_tools']
        model['latency_ms'] = tool_probe['latency_ms']
        model['latency_source'] = 'live_tool_call_probe'
        model['tool_support_probe'] = tool_probe
        models.append(model)
        seen.add(native_model_id)

    if not models:
        raise RuntimeError(
            'Together model discovery returned zero runnable serverless STARTED chat models '
            'in the exact /v1/models and /v1/endpoints intersection'
        )
    return models


def unique_model_ids(model_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    discovered: list[str] = []
    for model_id in model_ids:
        normalized = str(model_id).strip()
        if not normalized or normalized in seen:
            continue
        discovered.append(normalized)
        seen.add(normalized)
    return discovered


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def sync_provider_env_file_from_process() -> None:
    existing: dict[str, str] = {}
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            existing[key.strip()] = value.strip()

    process_values: dict[str, str] = {}
    for names in PROVIDER_KEY_HINTS.values():
        for name in names:
            value = os.environ.get(name, '').strip()
            if value and name not in existing:
                process_values[name] = value

    if os.environ.get('ANTROPIC_API_KEY', '').strip() and not os.environ.get('ANTHROPIC_API_KEY', '').strip():
        os.environ['ANTHROPIC_API_KEY'] = os.environ['ANTROPIC_API_KEY'].strip()
        if 'ANTHROPIC_API_KEY' not in existing:
            process_values['ANTHROPIC_API_KEY'] = os.environ['ANTHROPIC_API_KEY']
    if os.environ.get('TOGETHER_AI_API_KEY', '').strip() and not os.environ.get('TOGETHER_API_KEY', '').strip():
        os.environ['TOGETHER_API_KEY'] = os.environ['TOGETHER_AI_API_KEY'].strip()
        if 'TOGETHER_API_KEY' not in existing:
            process_values['TOGETHER_API_KEY'] = os.environ['TOGETHER_API_KEY']
    if os.environ.get('GEMINI_API_KEY', '').strip() and not os.environ.get('GOOGLE_API_KEY', '').strip():
        os.environ['GOOGLE_API_KEY'] = os.environ['GEMINI_API_KEY'].strip()
        if 'GOOGLE_API_KEY' not in existing:
            process_values['GOOGLE_API_KEY'] = os.environ['GOOGLE_API_KEY']

    if not process_values:
        return

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ENV_PATH.open('a', encoding='utf-8') as handle:
        for key, value in sorted(process_values.items()):
            escaped = value.replace('\\', '\\\\').replace('"', '\\"')
            handle.write(f'{key}="{escaped}"\n')
    try:
        ENV_PATH.chmod(0o600)
    except OSError:
        pass


def import_hermes_modules():
    sys.path.insert(0, str(REPO))
    from hermes_cli.config import get_compatible_custom_providers, load_config
    from hermes_cli.model_switch import get_authenticated_provider_slugs
    from hermes_cli.models import provider_model_ids
    return load_config, get_compatible_custom_providers, get_authenticated_provider_slugs, provider_model_ids


def compute_catalog() -> dict[str, Any]:
    ensure_hermes_python()
    sync_provider_env_file_from_process()
    load_env_file(ENV_PATH)
    load_config, get_compatible_custom_providers, get_authenticated_provider_slugs, provider_model_ids = import_hermes_modules()
    cfg = load_config()
    raw_model_cfg = cfg.get('model', {}) if isinstance(cfg, dict) else {}
    if isinstance(raw_model_cfg, dict):
        current_provider = str(raw_model_cfg.get('provider', '') or '')
        current_model = str(raw_model_cfg.get('model', '') or '')
    elif isinstance(raw_model_cfg, str):
        raw_model = raw_model_cfg.strip()
        if '/' in raw_model:
            current_provider, current_model = raw_model.split('/', 1)
        else:
            current_provider = ''
            current_model = raw_model
    else:
        current_provider = ''
        current_model = ''
    user_providers = cfg.get('providers', {}) if isinstance(cfg, dict) else {}
    custom_providers = get_compatible_custom_providers(cfg)
    authed = get_authenticated_provider_slugs(
        current_provider=current_provider,
        user_providers=user_providers,
        custom_providers=custom_providers,
    )
    usable_providers = []
    seen_runtime_providers: set[str] = set()
    for provider in authed:
        if provider not in SUPPORTED_NATIVE_PROVIDERS:
            continue
        runtime_provider = PROVIDER_RUNTIME_ALIASES.get(provider, provider)
        if runtime_provider in seen_runtime_providers:
            continue
        usable_providers.append(runtime_provider)
        seen_runtime_providers.add(runtime_provider)
    usable_provider_set = set(usable_providers)
    usable_catalog_provider_set = {
        CATALOG_PROVIDER_ALIASES.get(provider, provider)
        for provider in usable_provider_set
    }

    def env_has_any(provider: str) -> bool:
        return any(bool(os.environ.get(name, '').strip()) for name in PROVIDER_KEY_HINTS.get(provider, []))

    together_models: list[dict[str, Any]] = []
    if env_has_any('together'):
        if 'together' not in usable_provider_set:
            raise RuntimeError(
                'Together credentials are present, but Hermes did not detect a usable '
                'Together native or custom provider.'
            )
        together_models = fetch_together_model_catalog()
    elif 'together' in usable_provider_set:
        usable_providers = [provider for provider in usable_providers if provider != 'together']
        usable_provider_set.discard('together')

    openrouter_models: list[dict[str, Any]] = []
    openrouter_discovery_error: str | None = None
    provider_discovery_errors: list[dict[str, str]] = []
    if env_has_any('openrouter'):
        try:
            openrouter_models = fetch_openrouter_model_catalog()
        except RuntimeError as error:
            openrouter_discovery_error = safe_error_message(error) or error.__class__.__name__
            raise RuntimeError(
                f'Configured OpenRouter model discovery failed: {openrouter_discovery_error}'
            ) from None
        if openrouter_models and 'openrouter' not in usable_provider_set:
            usable_providers.append('openrouter')
            usable_provider_set.add('openrouter')

    model_catalog: list[dict[str, Any]] = []
    openrouter_model_ids = {
        str(model.get('native_model_id') or '').strip()
        for model in openrouter_models
        if str(model.get('native_model_id') or '').strip()
    }
    for provider in list(usable_providers):
        if provider == 'together':
            together_by_id = {
                str(model.get('native_model_id') or ''): model
                for model in together_models
                if model.get('native_model_id')
            }
            selected_ids = unique_model_ids(list(together_by_id))
            model_catalog.extend(
                attach_openrouter_mapping(
                    together_by_id[model_id],
                    openrouter_model_ids=openrouter_model_ids,
                )
                for model_id in selected_ids
            )
            continue
        if provider == 'openrouter' and openrouter_models:
            model_catalog.extend(openrouter_models)
            continue
        if provider == 'openrouter' and openrouter_discovery_error:
            continue
        discovery_provider = next((raw for raw in authed if PROVIDER_RUNTIME_ALIASES.get(raw, raw) == provider), provider)
        try:
            model_ids = provider_model_ids(discovery_provider) or provider_model_ids(provider)
        except Exception as error:
            raise RuntimeError(
                f'Authenticated Hermes provider {provider!r} model discovery failed: '
                f'{safe_error_message(error)}'
            ) from None
        if not model_ids:
            raise RuntimeError(
                f'Authenticated Hermes provider {provider!r} model discovery returned zero models.'
            )
        for native_model_id in unique_model_ids(list(model_ids)):
            model_catalog.append(
                attach_openrouter_mapping(
                    enrich_model(provider, native_model_id),
                    openrouter_model_ids=openrouter_model_ids,
                )
            )

    if env_has_any('openrouter') and not any(model.get('provider') == 'openrouter' for model in model_catalog):
        if 'openrouter' in usable_provider_set:
            usable_providers = [provider for provider in usable_providers if provider != 'openrouter']
            usable_provider_set.discard('openrouter')
        if not openrouter_discovery_error:
            openrouter_discovery_error = 'OpenRouter credentials are present, but no OpenRouter models were discovered.'

    missing_provider_keys: list[dict[str, Any]] = []
    configured_but_unusable_providers: list[dict[str, Any]] = []
    if 'openai' not in usable_catalog_provider_set:
        if env_has_any('openai'):
            configured_but_unusable_providers.append({
                'provider': 'openai',
                'env_vars': PROVIDER_KEY_HINTS['openai'],
                'note': 'OPENAI_API_KEY is present, but Hermes did not detect a usable direct OpenAI native adapter/catalog for this account right now. OpenAI Codex OAuth remains separately usable.',
            })
        else:
            missing_provider_keys.append({
                'provider': 'openai',
                'env_vars': PROVIDER_KEY_HINTS['openai'],
                'note': 'Direct OpenAI coverage is not configured. OpenAI Codex OAuth is usable, but it is a separate native adapter/catalog.',
            })
    for provider in ['anthropic', 'google', 'deepseek', 'xai', 'groq', 'mistral', 'together', 'openrouter']:
        live_aliases = {provider}
        if provider == 'google':
            live_aliases.add('gemini')
        if provider == 'xai':
            live_aliases.add('xai-oauth')
        if usable_provider_set.isdisjoint(live_aliases):
            if env_has_any(provider):
                note = f'{provider} credentials are present, but Hermes did not detect a usable native adapter/catalog for this provider in the current install.'
                if provider == 'openrouter' and openrouter_discovery_error:
                    note = openrouter_discovery_error
                configured_but_unusable_providers.append({
                    'provider': provider,
                    'env_vars': PROVIDER_KEY_HINTS[provider],
                    'note': note,
                })
            else:
                missing_provider_keys.append({
                    'provider': provider,
                    'env_vars': PROVIDER_KEY_HINTS[provider],
                })

    return {
        'current_provider': current_provider,
        'current_model': current_model,
        'authenticated_providers': authed,
        'usable_providers': usable_providers,
        'model_catalog': model_catalog,
        'missing_provider_keys': missing_provider_keys,
        'configured_but_unusable_providers': configured_but_unusable_providers,
        'provider_discovery_errors': provider_discovery_errors,
    }


def build_state(catalog_info: dict[str, Any]) -> dict[str, Any]:
    routes, route_generation = load_routes()
    return {
        'version': 1,
        'enabled': True,
        'base_url': LEROUTER_BASE_URL,
        'dashboard_url': LEROUTER_DASHBOARD_URL,
        'api_key': LEROUTER_API_KEY,
        'route_id': ROUTE_ID,
        'inference_mode': INFERENCE_MODE,
        'update_schedule': UPDATE_SCHEDULE,
        'candidates_per_route': CANDIDATES_PER_ROUTE,
        'metadata': METADATA,
        'routes': routes,
        'route_generation': route_generation,
        'model_catalog': catalog_info['model_catalog'],
        'usable_providers': catalog_info['usable_providers'],
        'authenticated_providers': catalog_info['authenticated_providers'],
        'missing_provider_keys': catalog_info['missing_provider_keys'],
        'configured_but_unusable_providers': catalog_info['configured_but_unusable_providers'],
        'provider_discovery_errors': catalog_info.get('provider_discovery_errors') or [],
        'plugin_name': 'lerouter-user-managed',
        'state_path': str(STATE_PATH),
    }


def post_json(url: str, payload: dict[str, Any], api_key: str) -> dict[str, Any]:
    proc = subprocess.run(
        [
            'curl', '-sS', '--fail-with-body',
            '-H', f'Authorization: Bearer {api_key}',
            '-H', 'Content-Type: application/json',
            '-X', 'POST',
            url,
            '--data-binary', '@-',
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = ' | '.join(
            part
            for part in (proc.stderr.strip(), proc.stdout.strip())
            if part
        ) or f'curl exited {proc.returncode}'
        raise RuntimeError(safe_error_message(detail) or f'curl exited {proc.returncode}')
    raw = proc.stdout.strip()
    return json.loads(raw) if raw else {}


def get_json(url: str, api_key: str) -> dict[str, Any]:
    proc = subprocess.run(
        [
            'curl', '-sS', '--fail-with-body',
            '-H', f'Authorization: Bearer {api_key}',
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = ' | '.join(
            part
            for part in (proc.stderr.strip(), proc.stdout.strip())
            if part
        ) or f'curl exited {proc.returncode}'
        raise RuntimeError(safe_error_message(detail) or f'curl exited {proc.returncode}')
    raw = proc.stdout.strip()
    return json.loads(raw) if raw else {}


def require_hermes_fail_closed_middleware() -> None:
    middleware_path = REPO / 'hermes_cli' / 'middleware.py'
    try:
        source = middleware_path.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f'Hermes fail-closed middleware contract could not be verified: {middleware_path}'
        ) from exc
    if 'class MiddlewareFailClosedError' not in source:
        class_marker = '@dataclass\nclass RequestMiddlewareResult:'
        if class_marker not in source:
            raise RuntimeError(
                'Hermes does not expose a compatible execution middleware contract; '
                'refusing to enable LeRouter because routing failures could execute the default model.'
            )
        source = source.replace(
            class_marker,
            'class MiddlewareFailClosedError(RuntimeError):\n'
            '    """Stop execution instead of falling through to the default model."""\n\n\n'
            f'{class_marker}',
            1,
        )

    if 'except MiddlewareFailClosedError:' not in source:
        exception_marker = (
            '        except _DownstreamExecutionError as exc:\n'
            '            raise exc.original\n'
            '        except Exception as exc:\n'
        )
        if exception_marker not in source:
            raise RuntimeError(
                'Hermes does not expose a compatible execution middleware error path; '
                'refusing to enable LeRouter because routing failures could execute the default model.'
            )
        source = source.replace(
            exception_marker,
            '        except _DownstreamExecutionError as exc:\n'
            '            raise exc.original\n'
            '        except MiddlewareFailClosedError:\n'
            '            raise\n'
            '        except Exception as exc:\n',
            1,
        )

    try:
        backup_path = middleware_path.with_suffix('.py.bak.lerouter')
        if not backup_path.exists():
            shutil.copy2(middleware_path, backup_path)
        middleware_path.write_text(source, encoding='utf-8')
        compile(source, str(middleware_path), 'exec')
    except (OSError, SyntaxError) as exc:
        raise RuntimeError(
            f'Hermes fail-closed middleware contract could not be installed: {middleware_path}'
        ) from exc


def install_plugin() -> dict[str, Any]:
    require_hermes_fail_closed_middleware()
    plugin_dir = HERMES_HOME / 'plugins' / 'lerouter-user-managed'
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for filename in ('plugin.py', 'plugin.yaml', '__init__.py'):
        source = SCRIPT_DIR / filename
        if source.exists():
            shutil.copy2(source, plugin_dir / filename)
    hermes_bin = REPO / 'venv' / 'bin' / 'hermes'
    if not hermes_bin.exists():
        raise RuntimeError(f'Hermes executable is unavailable: {hermes_bin}')
    runtime_commands = [
        (
            [str(hermes_bin), 'config', 'set', 'agent.api_max_retries', '1'],
            'set agent.api_max_retries=1',
        ),
        (
            [str(hermes_bin), 'fallback', 'clear'],
            'clear the Hermes fallback provider chain',
        ),
    ]
    for command, purpose in runtime_commands:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f'exit {result.returncode}'
            raise RuntimeError(
                f'Failed to disable Hermes runtime fallback ({purpose}): '
                f'{safe_error_message(detail)}'
            )
    result = subprocess.run(
        [str(hermes_bin), 'plugins', 'enable', 'lerouter-user-managed'],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f'exit {result.returncode}'
        raise RuntimeError(
            f'Failed to enable Hermes LeRouter plugin: {safe_error_message(detail)}'
        )
    return {
        'enabled': True,
        'plugin': 'lerouter-user-managed',
        'command_output': result.stdout.strip(),
        'runtime_fallback_disabled': True,
    }


def poll_setup_job(state: dict[str, Any], job_id: str) -> dict[str, Any]:
    status_url = f"{state['base_url']}/agent/setup-jobs/{job_id}"
    last_job: dict[str, Any] = {}
    attempts = math.ceil(SETUP_JOB_POLL_WINDOW_SECONDS / SETUP_JOB_POLL_INTERVAL_SECONDS)
    for _ in range(attempts):
        response = get_json(status_url, state['api_key'])
        last_job = response.get('job') if isinstance(response.get('job'), dict) else response
        status = str(last_job.get('status') or '').lower()
        if status in {'succeeded', 'failed'}:
            return last_job
        time.sleep(SETUP_JOB_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f'setup job {job_id} did not finish before installer polling ended')


def run_setup(state: dict[str, Any]) -> dict[str, Any]:
    if not state.get('routes'):
        raise RuntimeError(
            'No routes configured. The installer could not derive routes from Hermes local history and no explicit LEROUTER_ROUTES_JSON override was provided.'
        )
    history_samples = collect_hermes_history_samples()
    if not history_samples:
        raise RuntimeError(
            'Cannot compute W: Hermes local history has no usable requests in the selected budget cycle.'
        )
    budget = state.get('metadata', {}).get('budget') if isinstance(state.get('metadata'), dict) else {}
    budget = budget if isinstance(budget, dict) else {}
    cycle_days = budget_cycle_days(budget.get('cycle'))
    payload = {
        'route_id': state['route_id'],
        'update_schedule': state['update_schedule'],
        'candidates_per_route': state['candidates_per_route'],
        'metadata': state['metadata'],
        'model_catalog': state['model_catalog'],
        'routes': state['routes'],
    }
    payload['metadata'] = {
        **payload['metadata'],
        'budget': {
            **budget,
            'history_queries': history_samples,
            'history_period_days': cycle_days,
            'cycle_days': cycle_days,
        },
        'route_generation': state.get('route_generation') or {},
    }
    return post_json(f"{state['base_url']}/agent/setup-jobs", payload, state['api_key'])


def save_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE_DIR.chmod(0o700)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    STATE_PATH.chmod(0o600)


def hydrated_catalog_from_setup_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    models = result.get('hydrated_model_catalog')
    if not isinstance(models, list) or not models:
        raise RuntimeError('Succeeded setup job returned no hydrated model catalog.')
    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            raise RuntimeError('Succeeded setup job returned a non-object hydrated model.')
        model_id = str(model.get('model_id') or '').strip()
        if not model_id:
            raise RuntimeError('Succeeded setup job returned a hydrated model without model_id.')
        if model_id in seen:
            raise RuntimeError(f'Succeeded setup job returned duplicate hydrated model {model_id}.')
        catalog.append(dict(model))
        seen.add(model_id)
    if not catalog:
        raise RuntimeError('Succeeded setup job returned an empty hydrated model catalog.')
    return catalog


def build_setup_report(state: dict[str, Any], setup_job_response: dict[str, Any] | None = None) -> dict[str, Any]:
    setup_job = state.get('last_setup_job') if isinstance(state.get('last_setup_job'), dict) else {}
    result = setup_job.get('result') if isinstance(setup_job.get('result'), dict) else {}
    summary = result.get('catalog_summary') if isinstance(result.get('catalog_summary'), dict) else {}
    routes = summary.get('routes') if isinstance(summary.get('routes'), (dict, list)) else {}
    route_count = len(routes) if isinstance(routes, (dict, list)) else 0
    return {
        'state_path': str(STATE_PATH),
        'routable_models': summary.get('routable_models') or [],
        'eligible_not_selected_models': summary.get('eligible_not_selected_models') or [],
        'providers': summary.get('providers') or state.get('usable_providers') or [],
        'model_count': summary.get('model_count'),
        'routable_model_count': summary.get('routable_model_count'),
        'routes_covered': route_count,
        'configured_route_count': len(state.get('routes') or {}),
        'route_candidate_pool_count': route_count,
        'route_coverage_note': 'routes_covered is the number of configured user routes with candidate pools, not the number of provider models available.',
        'provider_api_keys_to_add': state.get('missing_provider_keys') or [],
        'configured_but_unusable_provider_keys': state.get('configured_but_unusable_providers') or [],
        'provider_discovery_errors': state.get('provider_discovery_errors') or [],
        'usable_providers': state.get('usable_providers') or [],
        'model_catalog_size': len(state.get('model_catalog') or []),
        'profile_rejections': result.get('profile_rejections') or state.get('profile_rejections') or [],
        'hydrated_candidate_pools': result.get('candidate_pools') or state.get('candidate_pools') or {},
        'plugin_enable': state.get('plugin_enable'),
        'setup_job_response': setup_job_response,
        'setup_job': setup_job or None,
        'status_url': f"{state['base_url']}/agent/setup-jobs/{state['last_setup_job_id']}" if state.get('last_setup_job_id') else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Set up LeRouter user-managed routing for Hermes.')
    parser.add_argument('--catalog-only', action='store_true', help='Print the current executable model catalog and exit.')
    parser.add_argument('--routes-only', action='store_true', help='Print routes derived from local Hermes history and exit.')
    parser.add_argument('--install-only', action='store_true', help='Install and enable the plugin without creating a setup job.')
    args = parser.parse_args()

    if args.install_only:
        print(json.dumps(install_plugin(), indent=2, ensure_ascii=False))
        return 0

    if args.routes_only:
        routes, route_generation = load_routes()
        print(json.dumps({
            'routes': routes,
            'route_generation': route_generation,
        }, indent=2, ensure_ascii=False))
        return 0

    if args.catalog_only:
        catalog_info = compute_catalog()
        print(json.dumps(catalog_info, indent=2, ensure_ascii=False))
        return 0

    write_setup_event('setup_started')
    try:
        catalog_info = compute_catalog()
        state = build_state(catalog_info)

        if not state['model_catalog']:
            message = 'No executable Hermes-native models are available right now.'
            write_setup_event('setup_failed', success=False, error=message)
            print(json.dumps({
                'error': message,
                'action_required': 'Configure at least one native provider key or OAuth-backed native adapter before starting /agent/setup-jobs.',
                'provider_api_keys_to_add': state['missing_provider_keys'],
                'missing_provider_keys': state['missing_provider_keys'],
                'configured_but_unusable_provider_keys': state['configured_but_unusable_providers'],
            }, indent=2, ensure_ascii=False))
            return 2

        setup_job_response = run_setup(state)
        state['last_setup_job_response'] = setup_job_response
        state['last_setup_job_id'] = (setup_job_response.get('job') or {}).get('id')
        if not state.get('last_setup_job_id'):
            raise RuntimeError('Setup API did not return a setup job id; plugin installation was not attempted.')
        state['last_setup_job'] = poll_setup_job(state, state['last_setup_job_id'])
        final_status = str((state.get('last_setup_job') or {}).get('status') or '').lower()
        if final_status != 'succeeded':
            write_setup_event('setup_failed', success=False, error=f'setup job status: {final_status or "missing"}')
            print(json.dumps(build_setup_report(state, setup_job_response), indent=2, ensure_ascii=False))
            return 2

        job_user_id = state['last_setup_job'].get('userId')
        if job_user_id:
            state['user_id'] = str(job_user_id)
        result = state['last_setup_job'].get('result')
        if not isinstance(result, dict):
            raise RuntimeError('Succeeded setup job returned no result object.')
        if result.get('user_id'):
            state['user_id'] = str(result['user_id'])
        if result.get('route_id'):
            state['route_id'] = str(result['route_id'])
        state['model_catalog'] = hydrated_catalog_from_setup_result(result)
        state['candidate_pools'] = result.get('candidate_pools')
        state['profile_rejections'] = result.get('profile_rejections') or []
        state['plugin_enable'] = install_plugin()
        save_state(state)
        write_setup_event('setup_succeeded', success=True)
        print(json.dumps(build_setup_report(state, setup_job_response), indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        write_setup_event('setup_failed', success=False, error=exc)
        raise


if __name__ == '__main__':
    raise SystemExit(main())
