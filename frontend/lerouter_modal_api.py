"""
LeRouter Modal-ready routing API.

Local run:
  pip install fastapi uvicorn
  uvicorn lerouter_modal_api:api --reload --port 8000

Modal deploy:
  pip install modal
  modal deploy lerouter_modal_api.py

Endpoints:
  POST /agent/candidate-models
  POST /lerouter/route
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import weakref
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

FRONTEND_DIR = str(Path(__file__).resolve().parent)
if FRONTEND_DIR not in sys.path:
    sys.path.insert(0, FRONTEND_DIR)

from workflow_budget import (
    WorkflowBudgetError,
    authorize_candidate,
    conservative_input_tokens,
    effective_scope_target,
    predicted_provider_cost,
    workflow_request_weight,
)

try:
    import modal
except Exception:  # Modal is optional for local development.
    modal = None

try:
    import certifi
except Exception:
    certifi = None

try:
    import httpx
except Exception:
    httpx = None

try:
    from pymongo import MongoClient, ReturnDocument
    from pymongo.errors import DuplicateKeyError, PyMongoError
except Exception:
    MongoClient = None
    ReturnDocument = None
    DuplicateKeyError = Exception
    PyMongoError = Exception


AGENT_TOKEN = os.environ.get("LEROUTER_AGENT_TOKEN")
USE_MODAL_MODELS = os.environ.get("LEROUTER_USE_MODAL_MODELS", "1").lower() not in {
    "0",
    "false",
    "no",
}
ARCHROUTER_APP_NAME = os.environ.get("LEROUTER_ARCHROUTER_APP_NAME", "lerouter-archrouter-schema")
ARCHROUTER_CLASS_NAME = os.environ.get("LEROUTER_ARCHROUTER_CLASS_NAME", "ArchRouterSchema")
E5_ROUTER_APP_NAME = os.environ.get("LEROUTER_E5_ROUTER_APP_NAME", "lerouter-e5-quality-router")
E5_ROUTER_CLASS_NAME = os.environ.get("LEROUTER_E5_ROUTER_CLASS_NAME", "E5QualityRouter")
E5_ROUTER_URL = os.environ.get("LEROUTER_E5_ROUTER_URL", "").rstrip("/")
LFM_METADATA_APP_NAME = os.environ.get("LEROUTER_LFM_METADATA_APP_NAME", "lerouter-routing-worker")
LFM_METADATA_CLASS_NAME = os.environ.get("LEROUTER_LFM_METADATA_CLASS_NAME", "LFMMetadataWorker")
LFM_METADATA_ENVIRONMENT_NAME = os.environ.get("LEROUTER_LFM_METADATA_ENVIRONMENT_NAME", "main")
ROUTING_WORKER_APP_NAME = os.environ.get("LEROUTER_ROUTING_WORKER_APP_NAME", "lerouter-routing-worker")
ROUTING_WORKER_CLASS_NAME = os.environ.get("LEROUTER_ROUTING_WORKER_CLASS_NAME", "LeRouterRoutingWorker")
ROUTING_WORKER_ENVIRONMENT_NAME = os.environ.get("LEROUTER_ROUTING_WORKER_ENVIRONMENT_NAME", "main")
ROUTING_WORKER_CODE_VERSION = "lerouter-routing-worker-v32-debiased-gemma4"
API_CODE_VERSION = "lerouter-api-v43-catalog-wide-routing"
PROFILE_EMBEDDING_FIELD = "gemma4_profile_embedding"
CATALOG_WIDE_ROUTING_STRATEGY = "catalog_wide"
CATALOG_ROUTE_NAME = "__catalog__"
LFM_METADATA_MODEL_ID = os.environ.get("LEROUTER_LFM_METADATA_MODEL_ID", "LiquidAI/LFM2-1.2B-Extract")
LFM_METADATA_ADAPTER_RUN = os.environ.get(
    "LEROUTER_LFM_METADATA_ADAPTER_RUN",
    "lfm12b-extract-lora-luna-summary-v2",
)
SETUP_HISTORY_LFM_CONCURRENCY = 16
DEFAULT_MODEL_SELECTOR_URL = os.environ.get("LEROUTER_DEFAULT_MODEL_SELECTOR_URL", "").rstrip("/")
MODEL_SELECTOR_URL = os.environ.get("LEROUTER_MODEL_SELECTOR_URL", DEFAULT_MODEL_SELECTOR_URL).rstrip("/")
MIN_ROUTE_CANDIDATES = 5
MAX_ROUTE_CANDIDATES = 7
DEFAULT_ROUTE_CANDIDATES = 7
QUALITY_CALIBRATION_VERSION = 3
MIN_USER_ROUTES = 5
MAX_USER_ROUTES = 7
ROUTING_FEE_RATE = 0.03
SPEND_ACCOUNTING_ROUTING_FEE = "routing_fee"
SPEND_ACCOUNTING_PROVIDER_SPEND = "provider_spend"
SPEND_ACCOUNTING_MODES = {
    SPEND_ACCOUNTING_ROUTING_FEE,
    SPEND_ACCOUNTING_PROVIDER_SPEND,
}
USAGE_LOG_TRANSACTION_MAX_ATTEMPTS = 8
USAGE_LOG_TRANSACTION_RETRY_DELAY_SECONDS = 0.025
USAGE_LOG_TRANSACTION_RETRY_MAX_DELAY_SECONDS = 0.8
USAGE_LOG_TRANSACTION_RETRY_MAX_JITTER_SECONDS = 0.05
MONGO_WRITE_CONFLICT_CODES = frozenset({112})
MODEL_SELECTOR_TIMEOUT_SECONDS = int(os.environ.get("LEROUTER_MODEL_SELECTOR_TIMEOUT_SECONDS", "3600"))
MONGO_CLIENT = None
USAGE_LOG_INDEX_READY = False
CANDIDATE_POOL_INDEX_READY = False
DATA_DIR = Path(os.environ.get("LEROUTER_DATA_DIR", "/data"))
JOB_STORE_DIR = DATA_DIR / "candidate_selection_jobs"
ROUTE_UPDATE_JOB_STORE_DIR = DATA_DIR / "route_update_jobs"
CANDIDATE_POOL_STORE_DIR = DATA_DIR / "candidate_pools"
ALLOW_ANONYMOUS_DEV = os.environ.get("LEROUTER_ALLOW_ANONYMOUS_DEV", "0").lower() in {
    "1",
    "true",
    "yes",
}
INFERENCE_MODES = {"user_managed", "router_managed"}
HTTP_CLIENTS_BY_LOOP: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()
ACCOUNTING_TOKEN_VERSION = 1
WORKFLOW_ACCOUNTING_TOKEN_VERSION = 2
WORKFLOW_INDEXES_READY = False
DEFAULT_LENGTH_PREDICTOR_URL = (
    "https://promptrail--lerouter-lenght-prediction-output-lenght-predicator.modal.run"
)
LENGTH_PREDICTOR_URL = os.environ.get(
    "LEROUTER_LENGTH_PREDICTOR_URL",
    DEFAULT_LENGTH_PREDICTOR_URL,
).rstrip("/")
WORKFLOW_HORIZON_PREDICTOR_URL = os.environ.get(
    "LEROUTER_WORKFLOW_HORIZON_PREDICTOR_URL",
    "https://promptrail--lerouter-workflow-horizon-predict-workflow-horizon.modal.run",
).rstrip("/")
ACCOUNTING_MODEL_PROFILE_FIELDS = (
    "provider_native_model_id",
    "native_model_id",
    "profile_model",
    "profile_alias",
    "model_owner",
    "execution_provider",
    "profile_hydrated",
    "is_open_source",
    "context_k_tokens",
    "latency_ms",
    "model_size",
    "input_cache_read_usd_per_million",
    "input_cache_write_usd_per_million",
    "supports_tools",
    "supports_json",
    "supports_reasoning_effort",
)


def archrouter_url() -> str:
    return os.environ.get("LEROUTER_ARCHROUTER_URL", "").rstrip("/")


def routing_worker_url() -> str:
    return os.environ.get("LEROUTER_ROUTING_WORKER_URL", "").rstrip("/")


async def call_lfm2_metadata(*, task: str) -> dict[str, Any]:
    if not USE_MODAL_MODELS or modal is None:
        raise HTTPException(status_code=503, detail="Modal LFM2 metadata inference is required")
    worker_class = modal.Cls.from_name(
        LFM_METADATA_APP_NAME,
        LFM_METADATA_CLASS_NAME,
        environment_name=LFM_METADATA_ENVIRONMENT_NAME,
    )
    result = await worker_class().infer.remote.aio({"task": task})
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="LFM2 metadata worker returned a non-object result")
    if result.get("model") != LFM_METADATA_MODEL_ID:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "invalid_lfm2_metadata_model",
                "expected": LFM_METADATA_MODEL_ID,
                "actual": result.get("model"),
            },
        )
    if result.get("adapter_run") != LFM_METADATA_ADAPTER_RUN:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "invalid_lfm2_metadata_adapter",
                "expected": LFM_METADATA_ADAPTER_RUN,
                "actual": result.get("adapter_run"),
            },
        )
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=502, detail="LFM2 metadata worker returned no metadata object")
    difficulty = parse_required_number(metadata.get("x"), "lfm2_metadata.difficulty")
    if not 0.0 <= difficulty <= 1.0:
        raise HTTPException(status_code=502, detail="LFM2 metadata difficulty must be between 0 and 1")
    return result


def internal_service_headers() -> dict[str, str]:
    token = os.environ.get("LEROUTER_INTERNAL_SERVICE_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="LEROUTER_INTERNAL_SERVICE_TOKEN is required for Modal routing services",
        )
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def accounting_signing_secret() -> bytes:
    token = os.environ.get("LEROUTER_INTERNAL_SERVICE_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "accounting_signing_secret_required",
                "message": "LEROUTER_INTERNAL_SERVICE_TOKEN is required for signed usage accounting",
            },
        )
    return token.encode("utf-8")


def base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def signed_accounting_model_profile(model: dict[str, Any], provider: str) -> dict[str, Any]:
    if not isinstance(model, dict):
        raise HTTPException(status_code=502, detail={"error": "accounting_model_profile_invalid"})
    model_id = str(model.get("model_id") or "").strip()
    normalized_provider = str(provider or "").strip().lower()
    input_price = finite_profile_number(model.get("input_price_per_million"))
    output_price = finite_profile_number(model.get("output_price_per_million"))
    if (
        not model_id
        or not normalized_provider
        or input_price is None
        or input_price < 0
        or output_price is None
        or output_price < 0
    ):
        raise HTTPException(
            status_code=502,
            detail={
                "error": "accounting_model_profile_incomplete",
                "message": "Selected model identity and finite nonnegative prices are required for accounting",
            },
        )

    profile: dict[str, Any] = {
        "model_id": model_id,
        "provider": normalized_provider,
        "input_price_per_million": input_price,
        "output_price_per_million": output_price,
    }
    for cache_key, field_name in (
        ("read", "input_cache_read_usd_per_million"),
        ("write", "input_cache_write_usd_per_million"),
    ):
        cache_price = model_cache_price_per_million(model, cache_key)
        if cache_price > 0:
            profile[field_name] = cache_price
    for field in ACCOUNTING_MODEL_PROFILE_FIELDS:
        value = model.get(field)
        if value is None:
            continue
        if field in {
            "context_k_tokens",
            "latency_ms",
            "model_size",
            "input_cache_read_usd_per_million",
            "input_cache_write_usd_per_million",
        }:
            value = finite_profile_number(value)
            if value is None:
                raise HTTPException(
                    status_code=502,
                    detail={"error": "accounting_model_profile_invalid", "field": field},
                )
        elif field in {
            "profile_hydrated",
            "is_open_source",
            "supports_tools",
            "supports_json",
            "supports_reasoning_effort",
        }:
            if not isinstance(value, bool):
                raise HTTPException(
                    status_code=502,
                    detail={"error": "accounting_model_profile_invalid", "field": field},
                )
        else:
            value = str(value).strip()
            if not value:
                continue
        profile[field] = value
    return profile


def issue_accounting_token(
    *,
    user_id: str,
    route_id: str,
    route_name: str,
    model_id: str,
    provider: str,
    model_profile: dict[str, Any],
    request_weight: float,
    routing_call_id: str,
    workflow_execution: dict[str, Any] | None = None,
) -> str:
    normalized_weight = finite_profile_number(request_weight, positive=True)
    normalized_provider = str(provider).strip().lower()
    signed_profile = signed_accounting_model_profile(model_profile, normalized_provider)
    normalized_model_id = str(model_id).strip()
    if not hmac.compare_digest(signed_profile["model_id"], normalized_model_id):
        raise HTTPException(
            status_code=502,
            detail={"error": "accounting_model_identity_mismatch"},
        )
    claim = {
        "v": WORKFLOW_ACCOUNTING_TOKEN_VERSION if workflow_execution else ACCOUNTING_TOKEN_VERSION,
        "user_id": str(user_id).strip(),
        "route_id": str(route_id).strip(),
        "route_name": str(route_name).strip(),
        "model_id": normalized_model_id,
        "provider": normalized_provider,
        "model_profile": signed_profile,
        "request_weight": normalized_weight,
        "routing_call_id": str(routing_call_id).strip(),
    }
    if workflow_execution:
        claim.update(
            {
                "workflow_run_id": str(workflow_execution.get("workflow_run_id") or "").strip(),
                "budget_scope_id": str(workflow_execution.get("budget_scope_id") or "").strip(),
                "reservation_id": str(workflow_execution.get("reservation_id") or "").strip(),
                "max_output_tokens": int(workflow_execution.get("max_output_tokens") or 0),
                "call_limit_usd": float(workflow_execution.get("call_limit_usd") or 0.0),
            }
        )
    missing = [key for key, value in claim.items() if key != "v" and (value is None or value == "")]
    if missing:
        raise HTTPException(
            status_code=502,
            detail={"error": "accounting_claim_incomplete", "missing": missing},
        )
    payload = json.dumps(claim, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(accounting_signing_secret(), payload, hashlib.sha256).digest()
    return f"{base64url_encode(payload)}.{base64url_encode(signature)}"


def verify_accounting_token(token: str) -> dict[str, Any]:
    generic_error = HTTPException(
        status_code=401,
        detail={"error": "invalid_accounting_token", "message": "Signed accounting token validation failed"},
    )
    if not isinstance(token, str) or not token.strip() or len(token) > 8192:
        raise generic_error
    try:
        payload_part, signature_part = token.strip().split(".", 1)
        payload = base64url_decode(payload_part)
        signature = base64url_decode(signature_part)
        expected_signature = hmac.new(accounting_signing_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise generic_error
        claim = json.loads(payload.decode("utf-8"))
    except HTTPException:
        raise
    except Exception as error:
        raise generic_error from error
    legacy_keys = {
        "v",
        "user_id",
        "route_id",
        "route_name",
        "model_id",
        "provider",
        "model_profile",
        "request_weight",
        "routing_call_id",
    }
    workflow_keys = legacy_keys | {
        "workflow_run_id",
        "budget_scope_id",
        "reservation_id",
        "max_output_tokens",
        "call_limit_usd",
    }
    if not isinstance(claim, dict):
        raise generic_error
    expected_keys = workflow_keys if claim.get("v") == WORKFLOW_ACCOUNTING_TOKEN_VERSION else legacy_keys
    if set(claim) != expected_keys or claim.get("v") not in {ACCOUNTING_TOKEN_VERSION, WORKFLOW_ACCOUNTING_TOKEN_VERSION}:
        raise generic_error
    numeric_keys = {"v", "request_weight", "model_profile", "max_output_tokens", "call_limit_usd"}
    for key in expected_keys - numeric_keys:
        if not isinstance(claim.get(key), str) or not claim[key].strip():
            raise generic_error
    request_weight = finite_profile_number(claim.get("request_weight"), positive=True)
    if request_weight is None:
        raise generic_error
    claim["request_weight"] = request_weight
    claim["provider"] = claim["provider"].lower()
    try:
        normalized_profile = signed_accounting_model_profile(claim.get("model_profile"), claim["provider"])
    except HTTPException as error:
        raise generic_error from error
    if normalized_profile != claim.get("model_profile"):
        raise generic_error
    if not hmac.compare_digest(normalized_profile["model_id"], claim["model_id"]):
        raise generic_error
    if claim.get("v") == WORKFLOW_ACCOUNTING_TOKEN_VERSION:
        max_output_tokens = claim.get("max_output_tokens")
        call_limit_usd = finite_profile_number(claim.get("call_limit_usd"), positive=True)
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens <= 0:
            raise generic_error
        if call_limit_usd is None:
            raise generic_error
        claim["call_limit_usd"] = call_limit_usd
    claim["model_profile"] = normalized_profile
    return claim


def accounting_request_weight_debit(claim: dict[str, Any]) -> float:
    request_weight = as_float(claim.get("request_weight"), -1.0)
    if request_weight <= 0:
        raise HTTPException(status_code=401, detail={"error": "invalid_accounting_token"})
    routing_call_id = str(claim.get("routing_call_id") or "").strip()
    return 0.0 if routing_call_id.endswith(":candidate:2") else request_weight


def get_db_path() -> Path:
    return DATA_DIR


def normalize_inference_mode(value: Any, fallback: str = "router_managed") -> str:
    if value is None or not str(value).strip():
        return fallback
    mode = str(value).strip().lower().replace("-", "_")
    if mode == "lerouter_managed":
        return "router_managed"
    if mode not in INFERENCE_MODES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_inference_mode",
                "message": "inference_mode must be user_managed or router_managed",
                "inference_mode": str(value),
                "supported_inference_modes": sorted(INFERENCE_MODES),
            },
        )
    return mode


def model_company_from_id(model_id: str | None) -> str | None:
    if not model_id:
        return None
    owner = str(model_id).split("/", 1)[0].lower().replace("~", "")
    labels = {
        "anthropic": "Anthropic",
        "deepseek-ai": "DeepSeek",
        "deepseek": "DeepSeek",
        "google": "Google",
        "gemini": "Google",
        "meta": "Meta",
        "meta-llama": "Meta",
        "mistral": "Mistral",
        "mistralai": "Mistral",
        "moonshot": "Moonshot",
        "moonshotai": "Moonshot",
        "nvidia": "NVIDIA",
        "openai": "OpenAI",
        "qwen": "Qwen",
        "qwenlm": "Qwen",
        "x-ai": "xAI",
        "z-ai": "Z.ai",
    }
    return labels.get(owner, owner or None)


def setup_catalog_summary(
    *,
    catalog: list[dict[str, Any]],
    route_candidates: dict[str, list[dict[str, Any]]],
    inference_mode: str,
) -> dict[str, Any]:
    routed_model_ids = []
    for models in route_candidates.values():
        for model in models:
            model_id = model.get("model_id") if isinstance(model, dict) else None
            if model_id:
                routed_model_ids.append(str(model_id))
    unique_model_ids = sorted(set(routed_model_ids))
    catalog_model_ids = sorted(
        {
            str(model.get("model_id") or model.get("id") or model.get("model"))
            for model in catalog
            if isinstance(model, dict) and (model.get("model_id") or model.get("id") or model.get("model"))
        }
    )
    eligible_not_selected_models = sorted(set(catalog_model_ids) - set(unique_model_ids))
    eligible_models_by_provider: dict[str, int] = {}
    for model in catalog:
        if not isinstance(model, dict):
            continue
        model_id = str(model.get("model_id") or model.get("id") or model.get("model") or "").strip()
        if not model_id:
            continue
        provider = model_provider(model) or "unknown"
        eligible_models_by_provider[provider] = eligible_models_by_provider.get(provider, 0) + 1
    candidate_models_by_provider: dict[str, int] = {}
    for models in route_candidates.values():
        for model in models:
            if not isinstance(model, dict):
                continue
            provider = model_provider(model) or "unknown"
            candidate_models_by_provider[provider] = candidate_models_by_provider.get(provider, 0) + 1
    providers = sorted({model_provider(model) or "unknown" for model in catalog if isinstance(model, dict)})
    companies = sorted({company for model_id in unique_model_ids if (company := model_company_from_id(model_id))})
    return {
        "inference_mode": inference_mode,
        "providers": providers,
        "model_companies": companies,
        "model_count": len(catalog_model_ids),
        "eligible_model_count": len(catalog_model_ids),
        "eligible_models_by_provider": dict(sorted(eligible_models_by_provider.items())),
        "routable_model_count": len(unique_model_ids),
        "routable_models": unique_model_ids,
        "eligible_not_selected_models": eligible_not_selected_models,
        "candidate_pool_model_count": len(routed_model_ids),
        "candidate_pool_unique_model_count": len(unique_model_ids),
        "candidate_pool_models_by_provider": dict(sorted(candidate_models_by_provider.items())),
        "routes": {
            route_name: [
                str(model.get("model_id"))
                for model in models
                if isinstance(model, dict) and model.get("model_id")
            ]
            for route_name, models in route_candidates.items()
        },
        "user_message": (
            "Hermes will route only across these models because they are the providers "
            "available in your current inference mode."
        ),
    }


def compact_routing_model(model: dict[str, Any], index: int) -> dict[str, Any]:
    model_id = str(model.get("model_id") or model.get("model") or "")
    provider = model.get("provider") or model_provider(model, model_id)
    compact: dict[str, Any] = {
        "rank": index + 1,
        "model_id": model_id,
        "provider": provider,
        "model_lab": model_company_from_id(model_id),
    }
    for key in (
        "native_model_id",
        "biencoder_source",
        "biencoder_rank",
        "biencoder_score",
        "biencoder_probability",
        "quality_scoring_version",
        "embedding_score",
        "quality_route_id",
        "quality_shrunk_relative_score",
        "quality_win_rate",
        "quality_route_relative_weight",
        "quality_win_rate_weight",
        "quality_route_relative_adjustment",
        "quality_win_rate_adjustment",
        "quality_prior_adjustment",
        "candidate_optimizer_score",
        "switch_cost_penalty",
        "prompt_cache_loss_usd",
        "continued_model_cache_savings_usd",
        "cache_pricing_available",
        "cache_stickiness_bonus",
        "cache_stickiness_bonus_multiplier",
        "cacheable_input_tokens",
        "cached_input_price_difference_per_million",
        "budget_malus",
        "request_budget_usd",
        "expected_price_usd",
        "request_weight",
        "final_score",
        "quality_utility",
        "quality",
    ):
        value = model.get(key)
        if value is not None:
            compact[key] = value
    budget_result = model.get("budget_result")
    if isinstance(budget_result, dict):
        compact["budget_result"] = budget_result
    return compact


def compact_routing_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        compact_routing_model(model, index)
        for index, model in enumerate(models)
        if isinstance(model, dict)
    ]


def routing_model_without_internal_fields(model: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in model.items()
        if key != PROFILE_EMBEDDING_FIELD
    }


def routing_models_without_internal_fields(
    models: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        routing_model_without_internal_fields(model)
        for model in models
        if isinstance(model, dict)
    ]


ROUTE_KEYWORDS = {
    "coding": ["code", "debug", "bug", "api", "python", "javascript", "typescript", "sql"],
    "reasoning": ["reason", "analyze", "compare", "strategy", "architecture", "plan"],
    "long-context": ["document", "transcript", "large", "long", "context", "report"],
    "fast-chat": ["short", "quick", "simple", "chat", "summarize"],
    "multimodal": ["image", "vision", "screenshot", "photo", "diagram"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mongo_database():
    global MONGO_CLIENT
    uri = os.environ.get("MONGODB_URI")
    if not uri or MongoClient is None:
        return None

    if MONGO_CLIENT is None:
        kwargs = {
            "serverSelectionTimeoutMS": int(os.environ.get("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "2500")),
            "connectTimeoutMS": int(os.environ.get("MONGODB_CONNECT_TIMEOUT_MS", "2500")),
            "socketTimeoutMS": int(os.environ.get("MONGODB_SOCKET_TIMEOUT_MS", "2500")),
        }
        if certifi:
            kwargs["tlsCAFile"] = certifi.where()
        MONGO_CLIENT = MongoClient(uri, **kwargs)
    return MONGO_CLIENT[os.environ.get("LEROUTER_MODEL_PROFILE_DB", "lerouter")]


def mongo_collection(name: str, fallback: str):
    database = mongo_database()
    if database is None:
        return None
    return database[os.environ.get(name, fallback)]


def require_mongo_collection(name: str, fallback: str):
    collection = mongo_collection(name, fallback)
    if collection is None:
        raise HTTPException(
            status_code=503,
            detail="MONGODB_URI is required for LeRouter Modal runtime storage.",
        )
    return collection


def init_database() -> None:
    if mongo_database() is None:
        raise HTTPException(
            status_code=503,
            detail="MONGODB_URI is required for LeRouter Modal runtime storage.",
        )


def workflow_collections() -> tuple[Any, Any, Any]:
    global WORKFLOW_INDEXES_READY
    runs = require_mongo_collection("LEROUTER_WORKFLOW_RUN_COLLECTION", "workflow_budget_runs")
    scopes = require_mongo_collection("LEROUTER_WORKFLOW_SCOPE_COLLECTION", "workflow_budget_scopes")
    reservations = require_mongo_collection("LEROUTER_WORKFLOW_RESERVATION_COLLECTION", "workflow_budget_reservations")
    if not WORKFLOW_INDEXES_READY:
        runs.create_index([("userId", 1), ("routeId", 1), ("id", 1)], unique=True, name="workflow_run_identity")
        scopes.create_index([("userId", 1), ("routeId", 1), ("runId", 1), ("id", 1)], unique=True, name="workflow_scope_identity")
        reservations.create_index([("userId", 1), ("routeId", 1), ("runId", 1), ("id", 1)], unique=True, name="workflow_reservation_identity")
        WORKFLOW_INDEXES_READY = True
    return runs, scopes, reservations


def workflow_scope_chain(*, user_id: str, route_id: str, run_id: str, scope_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    runs, scopes, _ = workflow_collections()
    run = runs.find_one({"userId": user_id, "routeId": route_id, "id": run_id})
    if not run:
        raise HTTPException(status_code=404, detail={"error": "workflow_run_missing", "workflow_run_id": run_id})
    if run.get("status") != "active":
        raise HTTPException(status_code=409, detail={"error": "workflow_run_not_active", "status": run.get("status")})
    by_id: dict[str, dict[str, Any]] = {}
    current_id: str | None = scope_id
    chain: list[dict[str, Any]] = []
    while current_id:
        if current_id in by_id:
            raise HTTPException(status_code=409, detail={"error": "workflow_scope_cycle"})
        row = scopes.find_one({"userId": user_id, "routeId": route_id, "runId": run_id, "id": current_id})
        if not row:
            raise HTTPException(status_code=404, detail={"error": "workflow_scope_missing", "budget_scope_id": current_id})
        if row.get("status") != "active":
            raise HTTPException(status_code=409, detail={"error": "workflow_scope_not_active", "budget_scope_id": current_id})
        by_id[current_id] = row
        chain.append(row)
        current_id = str(row.get("parentScopeId") or "").strip() or None
    chain.reverse()
    if not chain or chain[0].get("id") != run.get("rootScopeId"):
        raise HTTPException(status_code=409, detail={"error": "workflow_scope_not_in_run"})
    return run, chain


async def workflow_predictions(
    *,
    run: dict[str, Any],
    scopes: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    workflow_events: list[dict[str, Any]],
) -> dict[str, Any]:
    if not LENGTH_PREDICTOR_URL:
        raise HTTPException(status_code=503, detail={"error": "length_predictor_required"})
    if not WORKFLOW_HORIZON_PREDICTOR_URL:
        raise HTTPException(status_code=503, detail={"error": "workflow_horizon_predictor_required"})
    prompt = messages_text(messages)
    length_result, horizon_result = await asyncio.gather(
        post_json_async(
            LENGTH_PREDICTOR_URL,
            internal_service_headers(),
            {"verbosity_multiplier": 1.0, "prompt": prompt},
        ),
        post_json_async(
            WORKFLOW_HORIZON_PREDICTOR_URL,
            internal_service_headers(),
            {
                "workflow_run_id": run["id"],
                "root_goal": run["goal"],
                "messages": messages,
                "events": workflow_events,
                "scopes": [
                    {"scope_id": scope["id"], "name": scope["name"], "goal": scope["goal"]}
                    for scope in scopes
                ],
            },
        ),
    )
    predicted_tokens = finite_profile_number(
        first_present(length_result, "predicted_tokens", "predicted_tokens_rounded"),
        positive=True,
    )
    predictions = horizon_result.get("predictions") if isinstance(horizon_result, dict) else None
    if predicted_tokens is None:
        raise HTTPException(status_code=502, detail={"error": "invalid_length_prediction"})
    if not isinstance(predictions, list):
        raise HTTPException(status_code=502, detail={"error": "invalid_workflow_horizon_prediction"})
    return {
        "predicted_output_tokens": predicted_tokens,
        "length": length_result,
        "horizon": horizon_result,
    }


async def output_length_prediction(
    *,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    if not LENGTH_PREDICTOR_URL:
        raise HTTPException(status_code=503, detail={"error": "length_predictor_required"})
    result = await post_json_async(
        LENGTH_PREDICTOR_URL,
        internal_service_headers(),
        {"verbosity_multiplier": 1.0, "prompt": messages_text(messages)},
    )
    predicted_tokens = finite_profile_number(
        first_present(result, "predicted_tokens", "predicted_tokens_rounded"),
        positive=True,
    )
    if predicted_tokens is None:
        raise HTTPException(status_code=502, detail={"error": "invalid_length_prediction"})
    return {
        "predicted_output_tokens": predicted_tokens,
        "length": result,
    }


def caller_bounded_output_prediction(
    predicted_tokens: float,
    request_options: dict[str, Any],
) -> float:
    prediction = finite_profile_number(predicted_tokens, positive=True)
    if prediction is None:
        raise HTTPException(status_code=502, detail={"error": "invalid_length_prediction"})
    caller_max_tokens = parse_optional_number(request_options.get("max_tokens"))
    if caller_max_tokens is None:
        return prediction
    if caller_max_tokens <= 0:
        raise HTTPException(status_code=422, detail={"error": "invalid_max_tokens"})
    return min(prediction, caller_max_tokens)


def apply_workflow_weighted_target(
    *,
    scopes: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    request_options: dict[str, Any],
    route_schema: dict[str, Any],
    predictions: dict[str, Any],
) -> dict[str, Any]:
    horizon_result = predictions.get("horizon")
    horizon_predictions = horizon_result.get("predictions") if isinstance(horizon_result, dict) else None
    weight_config = horizon_result.get("weight_config") if isinstance(horizon_result, dict) else None
    if not isinstance(horizon_predictions, list) or not isinstance(weight_config, dict):
        raise HTTPException(status_code=502, detail={"error": "invalid_workflow_horizon_prediction"})

    input_tokens = conservative_input_tokens(messages, request_options)
    try:
        request_weight_result = workflow_request_weight(
            input_tokens=input_tokens,
            output_tokens=predictions["predicted_output_tokens"],
            difficulty=route_difficulty(route_schema),
            median_weighted_tokens=weight_config.get("median_weighted_tokens"),
            output_token_weight=weight_config.get("output_token_weight"),
            size_beta=weight_config.get("request_size_beta"),
            difficulty_alpha=weight_config.get("request_difficulty_alpha"),
            minimum=weight_config.get("request_weight_min"),
            maximum=weight_config.get("request_weight_max"),
        )
        horizon_by_scope = {
            str(item.get("scope_id") or ""): item
            for item in horizon_predictions
            if isinstance(item, dict) and str(item.get("scope_id") or "").strip()
        }
        target = effective_scope_target(
            scopes,
            horizon_by_scope,
            current_request_weight=request_weight_result["request_weight"],
        )
    except (KeyError, WorkflowBudgetError) as error:
        raise HTTPException(
            status_code=409,
            detail={"error": "workflow_budget_prediction_invalid", "message": str(error)},
        ) from error
    return {
        **predictions,
        **target,
        "request_weight": request_weight_result["request_weight"],
        "request_weight_result": request_weight_result,
    }


def workflow_rank_candidates(
    *,
    candidates: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    request_options: dict[str, Any],
    predictions: dict[str, Any],
) -> list[dict[str, Any]]:
    input_tokens = conservative_input_tokens(messages, request_options)
    predicted_output = float(predictions["predicted_output_tokens"])
    effective_target = float(predictions["effective_target_usd"])
    available = float(predictions["available_usd"])
    request_weight = float(predictions["request_weight"])
    ranked: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    supported_providers = request_options.get("workflow_supported_providers")
    supported_providers = {str(item).lower() for item in supported_providers} if isinstance(supported_providers, list) else set()
    for candidate in candidates:
        model_id = str(candidate.get("model_id") or "")
        provider = executable_provider_for_model(candidate)
        if supported_providers and provider not in supported_providers:
            failures.append({"model_id": model_id, "reason": f"local SDK does not support provider {provider}"})
            continue
        try:
            predicted_cost = predicted_provider_cost(
                input_tokens=input_tokens,
                output_tokens=predicted_output,
                input_price_per_million=candidate.get("input_price_per_million"),
                output_price_per_million=candidate.get("output_price_per_million"),
            )
            authorization = authorize_candidate(
                available_usd=available,
                effective_target_usd=effective_target,
                predicted_cost_usd=predicted_cost,
                input_tokens=input_tokens,
                input_price_per_million=candidate.get("input_price_per_million"),
                output_price_per_million=candidate.get("output_price_per_million"),
                caller_max_tokens=request_options.get("max_tokens"),
            )
        except WorkflowBudgetError as error:
            failures.append({"model_id": model_id, "reason": str(error)})
            continue
        quality = as_float(candidate.get("biencoder_score"), 0.0)
        over_target = max(0.0, predicted_cost - effective_target)
        enriched = dict(candidate)
        enriched.update(
            {
                "quality_utility": quality,
                "predicted_provider_cost_usd": predicted_cost,
                "workflow_budget_penalty": over_target / max(effective_target, 1e-12),
                "final_score": quality - (over_target / max(effective_target, 1e-12)),
                "workflow_authorization": authorization,
                "workflow_predictions": predictions,
                "request_weight": request_weight,
            }
        )
        ranked.append(enriched)
    ranked.sort(key=lambda item: (-as_float(item.get("final_score")), as_float(item.get("predicted_provider_cost_usd"))))
    if not ranked:
        raise HTTPException(
            status_code=409,
            detail={"error": "workflow_budget_exhausted", "candidate_failures": failures},
        )
    return ranked


def reserve_workflow_call(
    *,
    user_id: str,
    route_id: str,
    run_id: str,
    scope_chain: list[dict[str, Any]],
    selected_model: dict[str, Any],
    routing_call_id: str,
) -> dict[str, Any]:
    database = mongo_database()
    if database is None:
        raise HTTPException(status_code=503, detail="Mongo transaction support is required")
    _, scopes, reservations = workflow_collections()
    authorization = selected_model.get("workflow_authorization")
    if not isinstance(authorization, dict):
        raise HTTPException(status_code=500, detail={"error": "workflow_authorization_missing"})
    call_limit = float(authorization["call_limit_usd"])
    reservation_id = f"wfr_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    with database.client.start_session() as session:
        with session.start_transaction():
            for scope in scope_chain:
                updated = scopes.update_one(
                    {
                        "_id": scope["_id"],
                        "status": "active",
                        "$expr": {
                            "$gte": [
                                {"$subtract": ["$maxUsd", {"$add": ["$spentUsd", "$reservedUsd"]}]},
                                call_limit,
                            ]
                        },
                    },
                    {"$inc": {"reservedUsd": call_limit}, "$set": {"updatedAt": now}},
                    session=session,
                )
                if getattr(updated, "matched_count", 0) != 1:
                    raise HTTPException(status_code=409, detail={"error": "workflow_budget_exhausted", "budget_scope_id": scope["id"]})
            reservations.insert_one(
                {
                    "id": reservation_id,
                    "userId": user_id,
                    "routeId": route_id,
                    "runId": run_id,
                    "scopeId": scope_chain[-1]["id"],
                    "scopeIds": [scope["id"] for scope in scope_chain],
                    "routingCallId": routing_call_id,
                    "status": "reserved",
                    "callLimitUsd": call_limit,
                    "maxOutputTokens": int(authorization["max_output_tokens"]),
                    "modelId": selected_model["model_id"],
                    "provider": executable_provider_for_model(selected_model),
                    "createdAt": now,
                    "updatedAt": now,
                },
                session=session,
            )
    return {
        "workflow_run_id": run_id,
        "budget_scope_id": scope_chain[-1]["id"],
        "reservation_id": reservation_id,
        "call_limit_usd": call_limit,
        "max_output_tokens": int(authorization["max_output_tokens"]),
    }


def workflow_reservation_from_claim(claim: dict[str, Any]) -> dict[str, Any]:
    if claim.get("v") != WORKFLOW_ACCOUNTING_TOKEN_VERSION:
        raise HTTPException(status_code=422, detail={"error": "workflow_accounting_token_required"})
    _, _, reservations = workflow_collections()
    reservation = reservations.find_one(
        {
            "userId": claim["user_id"],
            "routeId": claim["route_id"],
            "runId": claim["workflow_run_id"],
            "scopeId": claim["budget_scope_id"],
            "id": claim["reservation_id"],
            "routingCallId": claim["routing_call_id"],
            "modelId": claim["model_id"],
            "provider": claim["provider"],
            "callLimitUsd": claim["call_limit_usd"],
            "maxOutputTokens": claim["max_output_tokens"],
        }
    )
    if not reservation:
        raise HTTPException(status_code=409, detail={"error": "workflow_reservation_claim_mismatch"})
    return reservation


def finalize_workflow_reservation(*, claim: dict[str, Any], spend_usd: float, outcome: str) -> dict[str, Any]:
    reservation = workflow_reservation_from_claim(claim)
    if reservation.get("status") in {"settled", "failed", "cancelled"}:
        if reservation.get("status") == outcome and math.isclose(float(reservation.get("spendUsd", 0.0)), spend_usd, rel_tol=0.0, abs_tol=1e-12):
            return reservation
        raise HTTPException(status_code=409, detail={"error": "workflow_reservation_already_finalized"})
    required_status = "reserved" if outcome == "cancelled" else "started"
    if reservation.get("status") != required_status:
        raise HTTPException(status_code=409, detail={"error": "workflow_reservation_invalid_state", "status": reservation.get("status")})
    call_limit = float(reservation["callLimitUsd"])
    debit = 0.0 if outcome == "cancelled" else float(spend_usd)
    if debit < 0 or debit > call_limit + 1e-12:
        raise HTTPException(status_code=409, detail={"error": "workflow_budget_cap_violation", "call_limit_usd": call_limit, "spend_usd": debit})
    database = mongo_database()
    _, scopes, reservations = workflow_collections()
    now = datetime.now(timezone.utc)
    with database.client.start_session() as session:
        with session.start_transaction():
            for scope_id in reservation["scopeIds"]:
                result = scopes.update_one(
                    {"userId": claim["user_id"], "routeId": claim["route_id"], "runId": claim["workflow_run_id"], "id": scope_id, "reservedUsd": {"$gte": call_limit}},
                    {"$inc": {"reservedUsd": -call_limit, "spentUsd": debit}, "$set": {"updatedAt": now}},
                    session=session,
                )
                if getattr(result, "matched_count", 0) != 1:
                    raise HTTPException(status_code=409, detail={"error": "workflow_scope_settlement_conflict", "budget_scope_id": scope_id})
            updated = reservations.find_one_and_update(
                {"_id": reservation["_id"], "status": required_status},
                {"$set": {"status": outcome, "spendUsd": debit, "finishedAt": now, "updatedAt": now}},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if not updated:
                raise HTTPException(status_code=409, detail={"error": "workflow_reservation_settlement_conflict"})
    return updated


def safe_store_key(value: Any) -> str:
    text = str(value or "none")
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", text)[:180]


def read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        volume = globals().get("modal_volume")
        if volume is not None and hasattr(volume, "reload"):
            try:
                volume.reload()
            except Exception:
                pass
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as error:
        print(f"LEROUTER_JSON_STORE_READ_FAILED {path}: {error}", flush=True)
        return None


def write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    volume = globals().get("modal_volume")
    if volume is not None and hasattr(volume, "commit"):
        try:
            volume.commit()
        except Exception as error:
            print(f"LEROUTER_JSON_STORE_COMMIT_FAILED {path}: {error}", flush=True)


def job_file_path(job_id: str) -> Path:
    return JOB_STORE_DIR / f"{safe_store_key(job_id)}.json"


def route_update_job_file_path(job_id: str) -> Path:
    return ROUTE_UPDATE_JOB_STORE_DIR / f"{safe_store_key(job_id)}.json"


def candidate_pool_file_path(user_id: str | None, route_id: str, route_name: str) -> Path:
    return (
        CANDIDATE_POOL_STORE_DIR
        / safe_store_key(user_id or "global")
        / safe_store_key(route_id)
        / f"{safe_store_key(route_name)}.json"
    )


def route_policy_file_path(user_id: str | None, route_id: str) -> Path:
    return CANDIDATE_POOL_STORE_DIR / safe_store_key(user_id or "global") / safe_store_key(route_id) / "_policy.json"


def ensure_user_exists(user_id: str | None) -> None:
    if not user_id:
        raise HTTPException(status_code=422, detail="Exact user_id is required")
    try:
        users = require_mongo_collection("LEROUTER_USER_COLLECTION", "user")
        user = users.find_one({"id": user_id}, {"_id": 1, "id": 1})
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "user_store_unavailable",
                "message": "Exact user state could not be verified",
            },
        ) from error
    if not user:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "user_state_missing",
                "message": "Exact existing user state is required; LeRouter does not synthesize users",
                "user_id": user_id,
            },
        )


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def bearer_token(authorization: str | None) -> str | None:
    if not isinstance(authorization, str):
        return None
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return None
    return authorization[len(prefix):].strip()


def authenticate_agent(authorization: str | None) -> dict[str, Any]:
    token = bearer_token(authorization)
    if token and AGENT_TOKEN and token == AGENT_TOKEN:
        return {"type": "service", "user_id": None, "route_id": None}

    if token:
        token_hash = hash_api_key(token)
        try:
            api_keys = mongo_collection("LEROUTER_API_KEY_COLLECTION", "api_keys")
        except Exception as error:
            print(f"LEROUTER_API_KEY_MONGO_UNAVAILABLE {error.__class__.__name__}: {error}", flush=True)
            api_keys = None
        if api_keys is not None:
            try:
                now = datetime.now(timezone.utc)
                row = api_keys.find_one_and_update(
                    {"keyHash": token_hash, "revokedAt": None},
                    {"$set": {"lastUsedAt": now, "updatedAt": now}},
                    return_document=ReturnDocument.AFTER,
                )
                if row:
                    return {
                        "type": "api_key",
                        "key_id": row.get("id"),
                        "user_id": row.get("userId"),
                        "route_id": row.get("routeId") or "default",
                    }
            except Exception as error:
                global MONGO_CLIENT
                MONGO_CLIENT = None
                raise HTTPException(
                    status_code=503,
                    detail=f"LeRouter API key database unavailable: {error.__class__.__name__}",
                ) from error

    if ALLOW_ANONYMOUS_DEV:
        return {"type": "anonymous_dev", "user_id": None, "route_id": None}

    raise HTTPException(status_code=401, detail="Invalid LeRouter API key.")


def require_agent_access(authorization: str | None) -> dict[str, Any]:
    return authenticate_agent(authorization)


def auth_user_id(auth_context: dict[str, Any], fallback: str | None = None) -> str | None:
    return auth_context.get("user_id") or fallback


def auth_route_id(auth_context: dict[str, Any], fallback: str = "default") -> str:
    return str(auth_context.get("route_id") or fallback or "default")


def as_float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if number == number else fallback


REQUEST_OUTPUT_TOKEN_WEIGHT_K = as_float(os.environ.get("LEROUTER_REQUEST_OUTPUT_TOKEN_WEIGHT_K"), 5.0)
REQUEST_SIZE_BETA = as_float(os.environ.get("LEROUTER_REQUEST_SIZE_BETA"), 1.0)
REQUEST_DIFFICULTY_ALPHA = as_float(os.environ.get("LEROUTER_REQUEST_DIFFICULTY_ALPHA"), 2.0)
REQUEST_WEIGHT_MIN = as_float(os.environ.get("LEROUTER_REQUEST_WEIGHT_MIN"), 0.05)
REQUEST_WEIGHT_CAP_MULTIPLIER = as_float(os.environ.get("LEROUTER_REQUEST_WEIGHT_CAP_MULTIPLIER"), 4.0)
BUDGET_SHADOW_PRICE_INITIAL = as_float(os.environ.get("LEROUTER_BUDGET_SHADOW_PRICE_INITIAL"), 0.75)
BUDGET_CONTROLLER_LEARNING_RATE = as_float(os.environ.get("LEROUTER_BUDGET_CONTROLLER_LEARNING_RATE"), 10.0)
BUDGET_SHADOW_PRICE_MIN = as_float(os.environ.get("LEROUTER_BUDGET_SHADOW_PRICE_MIN"), 0.0)
BUDGET_SHADOW_PRICE_MAX = as_float(os.environ.get("LEROUTER_BUDGET_SHADOW_PRICE_MAX"), 100.0)
BUDGET_CONTROLLER_VERSION = "dual_gradient_allocated_ratio_v2"


def routing_fee_usd(final_request_spend_usd: float) -> float:
    return round(max(0.0, as_float(final_request_spend_usd)) * ROUTING_FEE_RATE, 8)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def normalize_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        normalized = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user")
            content = message.get("content")
            normalized_message = {
                "role": role,
                "content": content if content is not None else "",
            }
            for key in ("tool_calls", "tool_call_id", "name"):
                if message.get(key) is not None:
                    normalized_message[key] = message[key]
            if message_content_text(content) or normalized_message.get("tool_calls") or role == "tool":
                normalized.append(normalized_message)
        if normalized:
            return normalized

    prompt = str(payload.get("prompt") or payload.get("input") or "")
    return [{"role": "user", "content": prompt}]


def provider_request_options(payload: dict[str, Any]) -> dict[str, Any]:
    options = dict(payload.get("provider_options") or {})
    for key in ("tools", "tool_choice", "response_format", "temperature", "max_tokens"):
        value = payload.get(key)
        if value is not None:
            options[key] = value
    if payload.get("stream"):
        options["stream"] = True
    return options


def messages_text(messages: list[dict[str, Any]]) -> str:
    lines = []
    for message in messages:
        role = str(message.get("role") or "user")
        parts = [f"{role}:"]
        if message.get("name"):
            parts.append(f"name={message['name']}")
        if message.get("tool_call_id"):
            parts.append(f"tool_call_id={message['tool_call_id']}")
        content = message_content_text(message.get("content"))
        if content:
            parts.append(content)
        if message.get("tool_calls"):
            parts.append(f"tool_calls={json.dumps(message.get('tool_calls'), ensure_ascii=True)}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def routing_messages_text(
    messages: list[dict[str, Any]],
) -> str:
    latest_user_content = ""
    for message in reversed(messages):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        latest_user_content = message_content_text(message.get("content")).strip()
        if latest_user_content:
            break

    for message_index in range(len(messages) - 1, -1, -1):
        message = messages[message_index]
        role = str(message.get("role") or "user").strip().lower()
        if role == "system":
            continue
        content = message_content_text(message.get("content")).strip()
        tool_calls = message.get("tool_calls")
        if role == "tool":
            preceding_tool_calls = None
            for preceding in reversed(messages[:message_index]):
                candidate_calls = preceding.get("tool_calls")
                if candidate_calls:
                    preceding_tool_calls = candidate_calls
                    break
            tool_name = str(message.get("name") or "unknown").strip() or "unknown"
            status = "error" if "error" in content.lower() else "completed"
            parts = [
                f"user_goal:\n{latest_user_content[-4000:]}",
                f"current_step:\ntool={tool_name} status={status} output_chars={len(content)}",
            ]
            if preceding_tool_calls:
                parts.append(
                    "requested_tool_calls:\n"
                    + json.dumps(
                        preceding_tool_calls,
                        ensure_ascii=True,
                        separators=(",", ":"),
                    )[-2000:]
                )
            return "\n".join(parts)
        if content or tool_calls:
            parts = []
            if latest_user_content:
                parts.append(f"user_goal:\n{latest_user_content[-4000:]}")
            if content:
                parts.append(f"latest_{role}_data:\n{content[-2000:]}")
            if tool_calls:
                parts.append(
                    "requested_tool_calls:\n"
                    + json.dumps(tool_calls, ensure_ascii=True, separators=(",", ":"))[-2000:]
                )
            return "\n".join(parts)
    raise ValueError("routing requires a non-empty latest user, assistant, or tool message")


def tokenize(text: str) -> set[str]:
    return {
        token.strip(".,;:!?()[]{}\"'").lower()
        for token in text.split()
        if token.strip(".,;:!?()[]{}\"'")
    }


def route_keywords(route_name: str) -> list[str]:
    tokens = set(tokenize(route_name.replace("-", " ").replace("_", " ")))
    keywords = set(tokens)
    for keyword_route, route_keyword_list in ROUTE_KEYWORDS.items():
        if route_name == keyword_route or keyword_route in tokens:
            keywords.update(route_keyword_list)
    return sorted(keywords)


def model_identity(model: dict[str, Any]) -> tuple[str, str]:
    model_id = str(model.get("model_id") or model.get("id") or model.get("model") or "")
    provider = model_provider(model, model_id) or "unknown"
    return model_id, provider


def infer_catalog_metadata(model: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(model)
    model_id, provider = model_identity(enriched)
    native_model_id = str(enriched.get("native_model_id") or model_id.split("/", 1)[-1])
    haystack = f"{model_id} {native_model_id} {provider}".lower()
    provider_key = provider.lower()

    tags = {str(tag).lower() for tag in enriched.get("tags", []) if tag}
    provider_defaults = {
        "anthropic": {
            "quality": 0.93,
            "input_price_per_million": 3.0,
            "output_price_per_million": 15.0,
            "context_window": 200000,
            "tags": ["agent", "reasoning", "coding", "long-context", "premium"],
        },
        "copilot": {
            "quality": 0.9,
            "input_price_per_million": 1.5,
            "output_price_per_million": 6.0,
            "context_window": 200000,
            "tags": ["agent", "coding", "reasoning", "tools"],
        },
        "openai-codex": {
            "quality": 0.91,
            "input_price_per_million": 1.5,
            "output_price_per_million": 6.0,
            "context_window": 200000,
            "tags": ["agent", "coding", "reasoning", "tools"],
        },
        "openai": {
            "quality": 0.89,
            "input_price_per_million": 0.4,
            "output_price_per_million": 1.6,
            "context_window": 1000000,
            "tags": ["general", "tools", "fast", "long-context"],
        },
        "together": {
            "quality": 0.84,
            "input_price_per_million": 0.8,
            "output_price_per_million": 0.8,
            "context_window": 131072,
            "tags": ["general", "opensource", "fast"],
        },
    }
    defaults = provider_defaults.get(provider_key, {
        "quality": 0.78,
        "input_price_per_million": 1.0,
        "output_price_per_million": 3.0,
        "context_window": 128000,
        "tags": ["general"],
    })
    tags.update(defaults["tags"])

    if "claude" in haystack or "opus" in haystack or "sonnet" in haystack:
        tags.update({"agent", "reasoning", "coding", "long-context", "premium"})
        enriched.setdefault("quality", 0.93)
    if "haiku" in haystack or "mini" in haystack or "flash" in haystack:
        tags.update({"fast", "cheap"})
    if "gpt" in haystack or "codex" in haystack:
        tags.update({"agent", "coding", "reasoning", "tools"})
    if "deepseek" in haystack or "coder" in haystack or "qwen" in haystack:
        tags.update({"coding", "reasoning", "tools"})
    if "llama" in haystack or "gemma" in haystack or "open-source" in haystack:
        tags.update({"general", "chat", "opensource"})
    if "kimi" in haystack or "moonshot" in haystack:
        tags.update({"agent", "coding", "long-context"})
    if "gemini" in haystack:
        tags.update({"long-context", "reasoning", "multimodal"})

    enriched.setdefault("model_id", model_id)
    enriched.setdefault("provider", provider)
    enriched.setdefault("quality", defaults["quality"])
    enriched.setdefault("input_price_per_million", defaults["input_price_per_million"])
    enriched.setdefault("output_price_per_million", defaults["output_price_per_million"])
    enriched.setdefault("context_window", defaults["context_window"])
    enriched["tags"] = sorted(tags)
    return enriched


def normalize_route_definitions(routes: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    definitions: dict[str, dict[str, Any]] = {}
    if not isinstance(routes, dict):
        return definitions

    for route_name, definition in routes.items():
        clean_route_name = str(route_name or "").strip()
        if not clean_route_name:
            continue
        if isinstance(definition, dict):
            public_definition = {
                str(key): value
                for key, value in definition.items()
                if key not in {"models", "model_ids", "candidate_models"}
            }
            definitions[clean_route_name] = public_definition
        elif isinstance(definition, str):
            definitions[clean_route_name] = {
                "trigger": definition,
                "task": definition,
            }
    return definitions


def load_route_policy_document(user_id: str | None, route_id: str) -> dict[str, Any]:
    route_policies = require_mongo_collection("LEROUTER_ROUTE_POLICY_COLLECTION", "route_policies")
    try:
        row = route_policies.find_one(
            {"routeId": route_id, "userId": user_id},
            sort=[("updatedAt", -1)],
        )
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "route_policy_store_unavailable",
                "message": "Mongo route policy storage could not be queried",
                "route_id": route_id,
            },
        ) from error

    if row:
        routes = row.get("routes")
        if isinstance(routes, str):
            try:
                routes = json.loads(routes)
            except json.JSONDecodeError:
                routes = {}
        if isinstance(routes, dict) and routes:
            return row

    return {}


def load_route_policy(user_id: str | None, route_id: str) -> dict[str, Any]:
    policy = load_route_policy_document(user_id, route_id)
    routes = policy.get("routes") if isinstance(policy, dict) else None
    return routes if isinstance(routes, dict) else {}


def load_route_names(user_id: str | None, route_id: str) -> list[str]:
    return list(load_route_policy(user_id, route_id).keys())


def route_descriptors_from_names(
    route_names: list[str],
    route_definitions: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    descriptors = []
    for route_name in route_names:
        route_definition = route_definitions.get(route_name) if isinstance(route_definitions, dict) else None
        if isinstance(route_definition, dict):
            trigger = str(
                route_definition.get("trigger")
                or route_definition.get("description")
                or route_definition.get("task")
                or route_name
            )
            task_summary = str(
                route_definition.get("task")
                or route_definition.get("task_summary")
                or route_definition.get("description")
                or trigger
            )
            descriptor = {
                "route_id": route_name,
                "trigger": trigger,
                "task_summary": task_summary,
                # E5 quality calibration is trained on the coding/debugging
                # route for SWE-bench and must be explicit at the worker
                # boundary even when user-managed route definitions omit it.
                "quality_route_id": str(
                    route_definition.get("quality_route_id")
                    or "coding_debugging"
                ),
            }
            for key in (
                "actions",
                "domains",
                "difficulty",
                "language",
                "format",
                "quality_route_id",
            ):
                value = route_definition.get(key)
                if value is not None:
                    descriptor[key] = value
            descriptors.append(descriptor)
            continue

        readable = route_name.replace("_", " ").replace("-", " ")
        keywords = ROUTE_KEYWORDS.get(route_name, [])
        trigger = ", ".join(keywords) if keywords else readable
        descriptors.append(
            {
                "route_id": route_name,
                "trigger": trigger,
                "task_summary": f"Handle {readable} tasks.",
                "quality_route_id": "coding_debugging",
            }
        )
    return descriptors


def route_definitions_from_policy(route_policy: dict[str, Any]) -> dict[str, Any] | None:
    route_definitions = route_policy.get("routeDefinitions") if isinstance(route_policy, dict) else None
    if not isinstance(route_definitions, dict):
        route_definitions = route_policy.get("route_definitions") if isinstance(route_policy, dict) else None
    if not isinstance(route_definitions, dict):
        metadata = route_policy.get("metadata") if isinstance(route_policy, dict) else None
        route_definitions = metadata.get("route_definitions") if isinstance(metadata, dict) else None
    return route_definitions if isinstance(route_definitions, dict) else None


async def call_modal_archrouter(
    *,
    task: str,
    route_names: list[str],
    route_definitions: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not USE_MODAL_MODELS:
        raise HTTPException(status_code=503, detail="Modal routing models are disabled")
    url = routing_worker_url()
    if not url:
        raise HTTPException(status_code=503, detail="LEROUTER_ROUTING_WORKER_URL is required")

    body = {
        "task": task,
        "routes": route_descriptors_from_names(route_names, route_definitions),
        "classify_only": True,
    }

    return await post_json_async(
        url,
        internal_service_headers(),
        body,
    )


async def call_routing_worker(
    *,
    task: str,
    user_id: str,
    route_id: str,
    route_names: list[str],
    route_definitions: dict[str, Any] | None,
    candidate_limit: int,
    candidate_pool_versions_by_route: dict[str, str],
    request_options: dict[str, Any],
    predicted_output_tokens: float,
) -> dict[str, Any]:
    if not USE_MODAL_MODELS or modal is None:
        raise HTTPException(status_code=503, detail="Modal routing models are disabled")

    body = {
        "task": task,
        "user_id": user_id,
        "route_id": route_id,
        "routes": route_descriptors_from_names(route_names, route_definitions),
        "candidate_limit": route_candidate_limit(candidate_limit),
        "candidate_pool_versions_by_route": candidate_pool_versions_by_route,
    }
    body["expected_output_k_tokens"] = caller_bounded_output_prediction(
        predicted_output_tokens,
        request_options,
    ) / 1000.0

    worker_class = modal.Cls.from_name(
        ROUTING_WORKER_APP_NAME,
        ROUTING_WORKER_CLASS_NAME,
        environment_name=ROUTING_WORKER_ENVIRONMENT_NAME,
    )
    result = await worker_class().route.remote.aio(body)
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Modal routing worker returned a non-object result")
    return result


async def call_catalog_routing_worker(
    *,
    task: str,
    catalog: list[dict[str, Any]],
    request_options: dict[str, Any],
    predicted_output_tokens: float,
) -> dict[str, Any]:
    if not USE_MODAL_MODELS or modal is None:
        raise HTTPException(status_code=503, detail="Modal routing models are disabled")
    body = {
        "task": task,
        "catalog_models": catalog,
        "expected_output_k_tokens": caller_bounded_output_prediction(
            predicted_output_tokens,
            request_options,
        ) / 1000.0,
    }
    worker_class = modal.Cls.from_name(
        ROUTING_WORKER_APP_NAME,
        ROUTING_WORKER_CLASS_NAME,
        environment_name=ROUTING_WORKER_ENVIRONMENT_NAME,
    )
    result = await worker_class().route_catalog.remote.aio(body)
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="Modal catalog routing worker returned a non-object result")
    if result.get("code_version") != ROUTING_WORKER_CODE_VERSION:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "catalog_routing_worker_version_mismatch",
                "expected": ROUTING_WORKER_CODE_VERSION,
                "actual": result.get("code_version"),
            },
        )
    if result.get("archrouter") is not None:
        raise HTTPException(status_code=502, detail="Catalog-wide routing unexpectedly ran ArchRouter")
    return result


async def precompute_candidate_pool_embeddings(
    route_candidates: dict[str, list[dict[str, Any]]],
    *,
    user_id: str,
    route_id: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    if not USE_MODAL_MODELS or modal is None:
        raise HTTPException(status_code=503, detail="Modal routing models are disabled")
    route_candidates = {
        route_name: [normalize_model_identity_fields(model) for model in models]
        for route_name, models in route_candidates.items()
    }
    worker_class = modal.Cls.from_name(
        ROUTING_WORKER_APP_NAME,
        ROUTING_WORKER_CLASS_NAME,
        environment_name=ROUTING_WORKER_ENVIRONMENT_NAME,
    )
    result = await worker_class().embed_candidate_pools.remote.aio(
        {
            "user_id": user_id,
            "route_id": route_id,
            "candidate_pools_by_route": route_candidates,
        }
    )
    if not isinstance(result, dict) or result.get("code_version") != ROUTING_WORKER_CODE_VERSION:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "candidate_embedding_worker_version_mismatch",
                "expected": ROUTING_WORKER_CODE_VERSION,
                "actual": result.get("code_version") if isinstance(result, dict) else None,
            },
        )
    embedded = result.get("candidate_pools_by_route")
    versions = result.get("candidate_pool_versions_by_route")
    if not isinstance(embedded, dict) or not isinstance(versions, dict):
        raise HTTPException(status_code=502, detail="Routing worker returned no versioned candidate embeddings")
    if set(embedded) != set(route_candidates) or set(versions) != set(route_candidates):
        raise HTTPException(status_code=502, detail="Routing worker returned incomplete candidate embeddings")
    return embedded, {str(route): str(version) for route, version in versions.items()}


async def precompute_catalog_embeddings(
    catalog: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    if not USE_MODAL_MODELS or modal is None:
        raise HTTPException(status_code=503, detail="Modal routing models are disabled")
    worker_class = modal.Cls.from_name(
        ROUTING_WORKER_APP_NAME,
        ROUTING_WORKER_CLASS_NAME,
        environment_name=ROUTING_WORKER_ENVIRONMENT_NAME,
    )
    result = await worker_class().embed_catalog.remote.aio(
        {"catalog_models": validate_hydrated_model_catalog(catalog)}
    )
    if not isinstance(result, dict) or result.get("code_version") != ROUTING_WORKER_CODE_VERSION:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "catalog_embedding_worker_version_mismatch",
                "expected": ROUTING_WORKER_CODE_VERSION,
                "actual": result.get("code_version") if isinstance(result, dict) else None,
            },
        )
    embedded = result.get("catalog_models")
    version = str(result.get("catalog_version") or "").strip()
    embedded = validate_hydrated_model_catalog(embedded)
    if not version or any(
        not isinstance(model.get(PROFILE_EMBEDDING_FIELD), dict) for model in embedded
    ):
        raise HTTPException(status_code=502, detail="Routing worker returned an incomplete embedded catalog")
    return embedded, version


def ranked_candidates_from_modal(
    *,
    candidates: list[dict[str, Any]],
    modal_result: dict[str, Any],
    source: str,
) -> list[dict[str, Any]]:
    if not isinstance(modal_result, dict):
        raise HTTPException(status_code=502, detail="Remote E5 router returned no ranking object")
    if modal_result.get("dry_run") is True:
        raise HTTPException(status_code=502, detail="Remote E5 router returned a dry-run result")
    model_run_id = str(modal_result.get("model_run_id") or "").strip()
    code_version = str(modal_result.get("code_version") or "").strip()
    selected_model = str(modal_result.get("selected_model") or "").strip()
    if source != "test" and (not model_run_id or not code_version or not selected_model):
        raise HTTPException(
            status_code=502,
            detail={
                "error": "incomplete_e5_evidence",
                "message": "Real E5 ranking requires model_run_id, code_version, and selected_model",
            },
        )
    raw_ranked = modal_result.get("ranked")
    ranked_by_model: dict[str, dict[str, Any]] = {}
    duplicate_models: list[str] = []
    if isinstance(raw_ranked, list):
        for item in raw_ranked:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("model") or item.get("model_id") or "").strip()
            if not model_id:
                continue
            if model_id in ranked_by_model:
                duplicate_models.append(model_id)
            ranked_by_model[model_id] = item
    candidate_ids = [str(model.get("model_id") or "").strip() for model in candidates]
    candidate_set = set(candidate_ids)
    ranked_set = set(ranked_by_model)
    if duplicate_models or ranked_set != candidate_set or len(candidate_ids) != len(candidate_set):
        raise HTTPException(
            status_code=502,
            detail={
                "error": "biencoder_candidate_set_mismatch",
                "message": "Remote E5 ranking must contain every candidate exactly once and no other models",
                "missing_models": sorted(candidate_set - ranked_set),
                "unexpected_models": sorted(ranked_set - candidate_set),
                "duplicate_models": sorted(set(duplicate_models)),
            },
        )
    ranked = []
    seen_ranks: set[int] = set()
    for model in candidates:
        model_id = model["model_id"]
        e5_item = ranked_by_model.get(model_id)
        if not e5_item:
            raise HTTPException(status_code=502, detail="Remote E5 router omitted a candidate")
        score = finite_profile_number(e5_item.get("score"))
        probability = finite_profile_number(e5_item.get("probability"))
        rank_number = finite_profile_number(e5_item.get("rank"), positive=True)
        if (
            score is None
            or probability is None
            or not 0.0 <= probability <= 1.0
            or rank_number is None
            or not rank_number.is_integer()
        ):
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "invalid_biencoder_score",
                    "model_id": model_id,
                    "message": "E5 score, probability, and positive integer rank are required",
                },
            )
        rank = int(rank_number)
        if rank in seen_ranks:
            raise HTTPException(
                status_code=502,
                detail={"error": "duplicate_biencoder_rank", "rank": rank},
            )
        seen_ranks.add(rank)
        enriched = dict(model)
        enriched["biencoder_score"] = score
        enriched["biencoder_probability"] = probability
        enriched["biencoder_rank"] = rank
        enriched["biencoder_source"] = source
        for key in (
            "quality_scoring_version",
            "embedding_score",
            "quality_route_id",
            "quality_shrunk_relative_score",
            "quality_win_rate",
            "quality_route_relative_weight",
            "quality_win_rate_weight",
            "quality_route_relative_adjustment",
            "quality_win_rate_adjustment",
            "quality_prior_adjustment",
        ):
            value = e5_item.get(key)
            if value is not None:
                enriched[key] = value
        ranked.append(enriched)

    expected_ranks = set(range(1, len(candidates) + 1))
    if seen_ranks != expected_ranks:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "invalid_biencoder_ranks",
                "expected_ranks": sorted(expected_ranks),
                "actual_ranks": sorted(seen_ranks),
            },
        )

    if not ranked:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "empty_biencoder_result",
                "message": "Remote E5 router returned no ranked candidates",
                "source": source,
            },
        )

    ranked.sort(key=lambda model: model["biencoder_rank"])
    if source != "test" and ranked[0]["model_id"] != selected_model:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "e5_selected_model_mismatch",
                "selected_model": selected_model,
                "rank_one_model": ranked[0]["model_id"],
            },
        )
    return ranked


async def archrouter_classify_against_routes(
    *,
    route_id: str,
    messages: list[dict[str, str]],
    route_names: list[str],
    route_definitions: dict[str, Any] | None = None,
    previous_query_data: dict[str, Any] | None,
) -> dict[str, Any]:
    text = routing_messages_text(messages)

    modal_result = await call_modal_archrouter(
        task=text,
        route_names=route_names,
        route_definitions=route_definitions if isinstance(route_definitions, dict) else None,
    )
    route_schema = modal_result.get("route_schema") if isinstance(modal_result, dict) else None
    route_schema = route_schema if isinstance(route_schema, dict) else {}
    route_name = str(route_schema.get("route_id") or "").strip()
    if not route_name or (route_names and route_name not in route_names):
        raise HTTPException(
            status_code=502,
            detail={
                "error": "invalid_archrouter_result",
                "message": "ArchRouter returned no valid route_id from the configured route distribution",
                "route_name": route_name,
                "available_routes": route_names,
            },
        )

    return {
        "route_id": route_id,
        "route_name": route_name,
        "confidence": 0.9,
        "route_schema": route_schema,
        "previous_query_data_injected": False,
        "previous_query_data_present": bool(previous_query_data),
        "source": "modal_routing_worker_archrouter",
        "modal": modal_result,
    }


async def archrouter_classify(
    *,
    user_id: str | None,
    route_id: str,
    messages: list[dict[str, str]],
    previous_query_data: dict[str, Any] | None,
) -> dict[str, Any]:
    route_policy = load_route_policy_document(user_id, route_id)
    routes = route_policy.get("routes") if isinstance(route_policy, dict) else {}
    route_names = list(routes.keys()) if isinstance(routes, dict) else []
    return await archrouter_classify_against_routes(
        route_id=route_id,
        messages=messages,
        route_names=route_names,
        route_definitions=route_definitions_from_policy(route_policy),
        previous_query_data=previous_query_data,
    )


def model_provider(model: dict[str, Any] | None, model_id: str | None = None) -> str | None:
    if model:
        provider = model.get("execution_provider") or model.get("provider")
        if provider:
            return str(provider).lower()
    return provider_from_model_id(model_id or (model or {}).get("model_id"))


def nested_model_cost(model: dict[str, Any], key: str) -> float:
    model_cost_value = model.get("model_cost")
    if isinstance(model_cost_value, dict):
        value = as_float(model_cost_value.get(key), 0.0)
        if value > 0:
            return value
    return 0.0


def model_input_price_per_million(model: dict[str, Any]) -> float:
    explicit = as_float(model.get("input_price_per_million"), 0.0)
    if explicit > 0:
        return explicit
    nested = nested_model_cost(model, "input_usd_per_million")
    if nested > 0:
        return nested
    pricing = model.get("pricing")
    if isinstance(pricing, dict):
        return as_float(pricing.get("prompt"), 0.0) * 1_000_000
    return 0.0


def model_cache_price_per_million(model: dict[str, Any], cache_key: str) -> float:
    direct_keys = {
        "read": ("input_cache_read_usd_per_million", "input_cache_read_per_million"),
        "write": ("input_cache_write_usd_per_million", "input_cache_write_per_million"),
    }[cache_key]
    for key in direct_keys:
        value = as_float(model.get(key), 0.0)
        if value > 0:
            return value
    nested = nested_model_cost(model, f"input_cache_{cache_key}_usd_per_million")
    if nested > 0:
        return nested
    pricing = model.get("pricing")
    if isinstance(pricing, dict):
        return as_float(pricing.get(f"input_cache_{cache_key}"), 0.0) * 1_000_000
    return 0.0


def prompt_cache_factor(model: dict[str, Any], messages: list[dict[str, Any]]) -> float:
    prompt_price = model_input_price_per_million(model)
    cache_read_price = model_cache_price_per_million(model, "read")
    if prompt_price <= 0 or cache_read_price <= 0:
        return 1.0

    prompt_tokens = max(1.0, len(messages_text(messages)) / 4.0)
    cacheable_tokens = prompt_tokens * 0.75
    if cacheable_tokens < 1024:
        return 1.0

    cache_write_price = model_cache_price_per_million(model, "write")
    amortized_write_price = cache_write_price / 4.0 if cache_write_price > 0 else 0.0
    uncached_cost = prompt_tokens * prompt_price
    effective_cost = (
        (prompt_tokens - cacheable_tokens) * prompt_price
        + cacheable_tokens * cache_read_price
        + cacheable_tokens * amortized_write_price
    )
    return round(clamp(effective_cost / uncached_cost, 0.25, 1.0), 4)


def cacheable_prompt_text(messages: list[dict[str, Any]]) -> str:
    latest_user_index = None
    for index in range(len(messages) - 1, -1, -1):
        if str(messages[index].get("role") or "user") == "user":
            latest_user_index = index
            break

    prefix_messages = messages[:latest_user_index] if latest_user_index is not None else messages[:-1]
    return messages_text(prefix_messages)


def estimate_cacheable_input_tokens(messages: list[dict[str, Any]]) -> float:
    return max(0.0, len(cacheable_prompt_text(messages)) / 4.0)


def cached_input_price_difference_per_million(model: dict[str, Any]) -> float:
    input_price = model_input_price_per_million(model)
    cache_read_price = model_cache_price_per_million(model, "read")
    if input_price <= 0 or cache_read_price <= 0:
        return 0.0
    return max(0.0, input_price - cache_read_price)


def prompt_cache_savings(
    *,
    model: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, float | bool]:
    cacheable_tokens = estimate_cacheable_input_tokens(messages)
    input_price = model_input_price_per_million(model)
    cache_read_price = model_cache_price_per_million(model, "read")
    price_difference = cached_input_price_difference_per_million(model)
    savings = (price_difference / 1_000_000.0) * cacheable_tokens
    return {
        "cacheable_input_tokens": round(cacheable_tokens, 4),
        "cached_input_price_difference_per_million": round(price_difference, 6),
        "cache_pricing_available": input_price > 0 and cache_read_price > 0,
        "prompt_cache_savings_usd": round(max(0.0, savings), 8),
    }


def load_previous_route_usage(
    *,
    user_id: str,
    route_id: str,
    session_id: str | None,
) -> dict[str, Any] | None:
    usage_logs = require_mongo_collection("LEROUTER_ROUTE_USAGE_COLLECTION", "route_usage_logs")
    try:
        return usage_logs.find_one(
            {
                "userId": user_id,
                "routeId": route_id,
                "sessionId": str(session_id or ""),
            },
            sort=[("createdAt", -1)],
        )
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "switch_cost_store_unavailable",
                "message": "LeRouter could not load the previous route model",
                "route_id": route_id,
            },
        ) from error


async def switch_cost_estimator(
    *,
    user_id: str,
    route_id: str,
    route_name: str,
    session_id: str | None,
    messages: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    previous_usage: dict[str, Any] | None = None,
    previous_usage_loaded: bool = False,
) -> dict[str, Any]:
    previous = (
        previous_usage
        if previous_usage_loaded
        else load_previous_route_usage(
            user_id=user_id,
            route_id=route_id,
            session_id=session_id,
        )
    )

    previous_model_id = previous.get("modelId") if previous else None
    previous_model = previous.get("modelProfile") if isinstance(previous, dict) else None
    if previous_model_id and not isinstance(previous_model, dict):
        previous_model = {
            "model_id": previous_model_id,
            "provider": previous.get("provider") if isinstance(previous, dict) else None,
        }
    continued_model = next(
        (
            model
            for model in candidates
            if str(model.get("model_id") or "") == str(previous_model_id or "")
        ),
        previous_model,
    )
    cache_savings = (
        prompt_cache_savings(model=continued_model, messages=messages)
        if previous_model_id and isinstance(continued_model, dict)
        else {
            "cacheable_input_tokens": round(estimate_cacheable_input_tokens(messages), 4),
            "cached_input_price_difference_per_million": 0.0,
            "cache_pricing_available": False,
            "prompt_cache_savings_usd": 0.0,
        }
    )
    continued_model_cache_savings = as_float(cache_savings["prompt_cache_savings_usd"])
    penalties: dict[str, float] = {}
    bonuses: dict[str, float] = {}
    details: dict[str, Any] = {}

    for model in candidates:
        model_id = model["model_id"]
        is_previous_model = bool(previous_model_id and model_id == previous_model_id)
        switch_penalty = continued_model_cache_savings if previous_model_id and not is_previous_model else 0.0
        penalties[model_id] = switch_penalty
        bonuses[model_id] = 0.0
        details[model_id] = {
            "is_previous_model": is_previous_model,
            "switching_cost_penalty": switch_penalty,
            "prompt_cache_loss_usd": switch_penalty,
            "continued_model_cache_savings_usd": continued_model_cache_savings,
            "cache_stickiness_bonus_multiplier": 1.0,
            "cache_stickiness_bonus": 0.0,
            **cache_savings,
        }

    return {
        "route_name": route_name,
        "session_id": session_id,
        "previous_model_id": previous_model_id,
        "source": "prompt_cache_loss_v1",
        "continued_model_cache_savings_usd": continued_model_cache_savings,
        "penalties": penalties,
        "bonuses": bonuses,
        "details": details,
    }


def executable_catalog(catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [model for model in catalog if not isinstance(model, dict) or model.get("executable") is not False]


def require_explicit_model_catalog(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "model_catalog_required",
                "message": "model_catalog must be supplied explicitly and must not be empty; LeRouter has no implicit model catalog",
            },
        )

    catalog = executable_catalog(value)
    if len(catalog) < MIN_ROUTE_CANDIDATES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_model_catalog",
                "message": f"model_catalog must contain at least {MIN_ROUTE_CANDIDATES} executable models",
                "catalog_model_count": len(catalog),
            },
        )
    return catalog


def finite_profile_number(value: Any, *, positive: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def canonical_anthropic_profile_alias(native_model_id: str) -> str:
    native_model_id = re.sub(
        r"^anthropic/",
        "",
        str(native_model_id or "").strip(),
        flags=re.IGNORECASE,
    )
    if not native_model_id:
        return ""
    version_alias = re.fullmatch(
        r"(claude-(?:opus|sonnet|haiku))-(\d+)-(\d{1,2})(?:-\d{8})?",
        native_model_id,
        flags=re.IGNORECASE,
    )
    if version_alias:
        family, major, minor = version_alias.groups()
        return f"anthropic/{family}-{major}.{minor}"
    dated_major_alias = re.fullmatch(
        r"(claude-(?:opus|sonnet|haiku))-(\d+)-\d{8}",
        native_model_id,
        flags=re.IGNORECASE,
    )
    if dated_major_alias:
        family, major = dated_major_alias.groups()
        return f"anthropic/{family}-{major}"
    return f"anthropic/{native_model_id}"


def catalog_profile_alias(model: dict[str, Any]) -> str:
    model_id = str(model.get("model_id") or model.get("id") or model.get("model") or "").strip()
    provider = str(model.get("provider") or model.get("execution_provider") or "").strip().lower()
    native_model_id = str(
        model.get("provider_native_model_id") or model.get("native_model_id") or ""
    ).strip()
    if not native_model_id and "/" in model_id:
        native_model_id = model_id.split("/", 1)[1]
    if provider == "openai-codex":
        native_model_id = re.sub(r"^(?:openai-codex|openai)/", "", native_model_id, flags=re.IGNORECASE)
        return f"openai/{native_model_id}" if native_model_id else ""
    if provider in {"openrouter", "together"}:
        native_model_id = re.sub(rf"^{re.escape(provider)}/", "", native_model_id, flags=re.IGNORECASE)
        return native_model_id
    if provider == "anthropic":
        return canonical_anthropic_profile_alias(native_model_id)
    return model_id


def profile_cost_values(profile: dict[str, Any]) -> tuple[float | None, float | None]:
    model_cost = profile.get("model_cost") if isinstance(profile.get("model_cost"), dict) else {}
    return (
        finite_profile_number(
            first_present(
                model_cost,
                "input_usd_per_million",
                "input_price_per_million",
                "input",
            )
        ),
        finite_profile_number(
            first_present(
                model_cost,
                "output_usd_per_million",
                "output_price_per_million",
                "output",
            )
        ),
    )


def candidate_completeness_issues(model: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if model.get("profile_hydrated") is not True:
        issues.append("profile_hydrated")
    if not str(model.get("model_id") or "").strip():
        issues.append("model_id")
    if not str(model.get("provider") or model.get("execution_provider") or "").strip():
        issues.append("provider")
    if not str(model.get("provider_native_model_id") or model.get("native_model_id") or "").strip():
        issues.append("native_model_id")
    strengths = model.get("strengths")
    if not isinstance(strengths, list):
        issues.append("profile_strengths")
    if finite_profile_number(model.get("context_k_tokens"), positive=True) is None:
        issues.append("context_k_tokens")
    if finite_profile_number(model.get("latency_ms")) is None:
        issues.append("latency_ms")
    if finite_profile_number(model.get("input_price_per_million")) is None:
        issues.append("input_price_per_million")
    if finite_profile_number(model.get("output_price_per_million")) is None:
        issues.append("output_price_per_million")
    if not isinstance(model.get("supports_tools"), bool):
        issues.append("supports_tools")
    if not isinstance(model.get("supports_json"), bool):
        issues.append("supports_json")
    if not isinstance(model.get("supports_reasoning_effort"), bool):
        issues.append("supports_reasoning_effort")
    quality_calibration = model.get("quality_calibration")
    if not isinstance(quality_calibration, dict):
        issues.append("quality_calibration")
    else:
        if quality_calibration.get("version") != QUALITY_CALIBRATION_VERSION:
            issues.append("quality_calibration_version")
        if not isinstance(quality_calibration.get("routes"), dict):
            issues.append("quality_calibration_routes")
    return issues


DIRECT_PROVIDER_PRECEDENCE = {
    "together": 0,
    "openai": 1,
    "anthropic": 2,
    "openrouter": 3,
}


def canonical_model_identity(model: dict[str, Any]) -> str:
    """Return a provider-independent identity for candidate deduplication."""
    provider = str(model.get("execution_provider") or model.get("provider") or "").strip().lower()
    model_id = str(model.get("model_id") or model.get("id") or model.get("model") or "").strip()
    openrouter_native = str(model.get("openrouter_native_model_id") or "").strip()
    if openrouter_native:
        return re.sub(r"^openrouter/", "", openrouter_native, flags=re.IGNORECASE).lower()
    explicit = str(model.get("canonical_model_id") or "").strip()
    if explicit:
        explicit = re.sub(r"^openrouter/", "", explicit, flags=re.IGNORECASE)
        if provider == "together" and explicit.lower().startswith("together/"):
            providerless = explicit.split("/", 1)[1]
            if "/" in providerless:
                explicit = providerless
        return explicit.lower()
    native = str(
        model.get("native_model_id")
        or model.get("provider_native_model_id")
        or ""
    ).strip()
    if provider == "openrouter":
        native = native or model_id
        native = re.sub(r"^openrouter/", "", native, flags=re.IGNORECASE)
        return native.lower()
    native = native or model_id
    native = re.sub(rf"^{re.escape(provider)}/", "", native, flags=re.IGNORECASE)
    if provider == "together" and "/" in native:
        return native.lower()
    return f"{provider}/{native}".lower() if provider else native.lower()


def provider_precedence(model: dict[str, Any]) -> int:
    provider = str(model.get("execution_provider") or model.get("provider") or "").strip().lower()
    return DIRECT_PROVIDER_PRECEDENCE.get(provider, 99)


def normalize_model_identity_fields(model: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(model)
    provider = str(normalized.get("execution_provider") or normalized.get("provider") or "").strip().lower()
    model_id = str(normalized.get("model_id") or normalized.get("id") or normalized.get("model") or "").strip()
    native = str(normalized.get("native_model_id") or normalized.get("provider_native_model_id") or "").strip()
    if not native and model_id:
        native = provider_native_model_id(model_id, provider, normalized)
    normalized["canonical_model_id"] = canonical_model_identity(normalized)
    normalized["model_id"] = model_id
    normalized["provider"] = provider
    normalized["execution_provider"] = provider
    normalized["native_model_id"] = native
    normalized["provider_native_model_id"] = native
    if provider == "openrouter":
        normalized["openrouter_native_model_id"] = openrouter_native_model_id(normalized) or native
    else:
        normalized.setdefault("openrouter_native_model_id", "")
    return normalized


def dedupe_canonical_models(models: list[dict[str, Any]], *, enrich: bool = False) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for raw_model in models:
        model = normalize_model_identity_fields(raw_model)
        key = model.get("canonical_model_id")
        if not key:
            continue
        current = selected.get(key)
        if current is None or provider_precedence(model) < provider_precedence(current):
            selected[key] = model if enrich else dict(raw_model)
    return list(selected.values())


def prefer_direct_provider_models(
    models: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace an OpenRouter alias with its direct catalog equivalent when present."""
    catalog_by_key: dict[str, list[dict[str, Any]]] = {}
    for candidate in catalog:
        key = canonical_model_identity(candidate)
        if key:
            catalog_by_key.setdefault(key, []).append(candidate)
    selected: list[dict[str, Any]] = []
    for model in models:
        key = canonical_model_identity(model)
        equivalents = [*catalog_by_key.get(key, []), model]
        selected.append(min(equivalents, key=provider_precedence))
    return selected


def validate_complete_candidate_pool(
    models: Any,
    *,
    route_name: str,
) -> list[dict[str, Any]]:
    if not isinstance(models, list):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "invalid_candidate_pool",
                "route": route_name,
                "message": "candidate pool must be a list of 5-7 complete models",
            },
        )
    issues: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            issues.append({"index": index, "missing": ["model_object"]})
            continue
        model_id = str(model.get("model_id") or "").strip()
        normalized_model_id = model_id.lower()
        missing = candidate_completeness_issues(model)
        if normalized_model_id in seen:
            missing = [*missing, "unique_model_id"]
        if missing:
            issues.append({"index": index, "model_id": model_id or None, "missing": sorted(set(missing))})
            continue
        seen.add(normalized_model_id)
        normalized.append(dict(model))
    deduped = dedupe_canonical_models(normalized)
    if len(deduped) != len(normalized):
        issues.append({"missing": ["unique_canonical_model_id"]})
    if not MIN_ROUTE_CANDIDATES <= len(deduped) <= MAX_ROUTE_CANDIDATES or issues:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "invalid_candidate_pool",
                "route": route_name,
                "model_count": len(deduped),
                "expected": f"{MIN_ROUTE_CANDIDATES}-{MAX_ROUTE_CANDIDATES}",
                "issues": issues,
            },
        )
    return deduped


def validate_hydrated_model_catalog(catalog: Any) -> list[dict[str, Any]]:
    catalog = require_explicit_model_catalog(catalog)
    issues: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, model in enumerate(catalog):
        if not isinstance(model, dict):
            issues.append({"index": index, "missing": ["model_object"]})
            continue
        model_id = str(model.get("model_id") or "").strip()
        normalized_id = model_id.lower()
        missing = candidate_completeness_issues(model)
        if normalized_id in seen:
            missing = [*missing, "unique_model_id"]
        if missing:
            issues.append({"index": index, "model_id": model_id or None, "missing": sorted(set(missing))})
            continue
        normalized.append(dict(model))
        seen.add(normalized_id)
    normalized = dedupe_canonical_models(normalized)
    if issues or len(normalized) < MIN_ROUTE_CANDIDATES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_hydrated_model_catalog",
                "model_count": len(normalized),
                "issues": issues,
            },
        )
    return normalized


def hydrate_model_catalog(value: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_catalog = require_explicit_model_catalog(value)
    profiles = require_mongo_collection("LEROUTER_MODEL_PROFILE_COLLECTION", "model_profiles")
    hydrated: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    seen_model_ids: set[str] = set()

    for raw_model in raw_catalog:
        if not isinstance(raw_model, dict):
            rejections.append({"model_id": None, "reason": "catalog_entry_not_object"})
            continue
        model = dict(raw_model)
        model_id = str(model.get("model_id") or model.get("id") or model.get("model") or "").strip()
        alias = catalog_profile_alias(model)
        if not model_id or not alias:
            rejections.append({"model_id": model_id or None, "profile_alias": alias or None, "reason": "identity_incomplete"})
            continue
        if model_id.lower() in seen_model_ids:
            rejections.append({"model_id": model_id, "profile_alias": alias, "reason": "duplicate_model_id"})
            continue
        try:
            profile = profiles.find_one(
                {"model": {"$regex": f"^{re.escape(alias)}$", "$options": "i"}}
            )
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "model_profile_store_unavailable",
                    "message": "Mongo model_profiles could not be queried",
                    "model_id": model_id,
                    "profile_alias": alias,
                },
            ) from error
        if not isinstance(profile, dict):
            rejections.append({"model_id": model_id, "profile_alias": alias, "reason": "profile_missing"})
            continue

        forces = profile.get("forces") or profile.get("strengths") or []
        context_window = finite_profile_number(
            first_present(profile, "model_context_window", "context_window"),
            positive=True,
        )
        context_k_tokens = finite_profile_number(profile.get("context_k_tokens"), positive=True)
        if context_window is None and context_k_tokens is not None:
            context_window = context_k_tokens * 1000
        if context_k_tokens is None and context_window is not None:
            context_k_tokens = context_window / 1000
        latency_ms = finite_profile_number(model.get("latency_ms"))
        if latency_ms is None:
            latency_ms = finite_profile_number(first_present(profile, "model_latency", "latency_ms"))
        model_size = finite_profile_number(profile.get("model_size"), positive=True)
        profile_input_price, profile_output_price = profile_cost_values(profile)
        live_input_price = finite_profile_number(model.get("input_price_per_million"))
        live_output_price = finite_profile_number(model.get("output_price_per_million"))
        input_price = (
            live_input_price
            if live_input_price is not None
            else profile_input_price
        )
        output_price = (
            live_output_price
            if live_output_price is not None
            else profile_output_price
        )
        live_supports_tools = model.get("supports_tools")
        live_supports_json = model.get("supports_json")
        live_supports_reasoning_effort = model.get("supports_reasoning_effort")
        resolved_supports_tools = (
            live_supports_tools
            if isinstance(live_supports_tools, bool)
            else profile.get("supports_tools")
        )
        resolved_supports_json = (
            live_supports_json
            if isinstance(live_supports_json, bool)
            else profile.get("supports_json")
        )
        resolved_supports_reasoning_effort = (
            live_supports_reasoning_effort
            if isinstance(live_supports_reasoning_effort, bool)
            else profile.get("supports_reasoning_effort")
        )
        profile_missing: list[str] = []
        if (
            not isinstance(forces, list)
            or not forces
            or any(
                not isinstance(item, str) or not item.strip()
                for item in forces
            )
        ):
            profile_missing.append("profile_forces")
        if context_k_tokens is None:
            profile_missing.append("profile_context")
        if latency_ms is None:
            profile_missing.append("profile_latency")
        if input_price is None:
            profile_missing.append("profile_input_price")
        if output_price is None:
            profile_missing.append("profile_output_price")

        model["model_id"] = model_id
        model["profile_model"] = str(profile.get("model") or alias)
        model["profile_alias"] = alias
        model["profile_hydrated"] = True
        model["forces"] = list(forces) if isinstance(forces, list) else forces
        model["strengths"] = list(forces) if isinstance(forces, list) else forces
        model["weaknesses"] = list(profile.get("weaknesses") or [])
        model["context_window"] = int(context_window) if context_window is not None else None
        model["context_k_tokens"] = context_k_tokens
        model["latency_ms"] = latency_ms
        model["model_latency"] = latency_ms
        model["model_size"] = model_size
        model["model_cost"] = dict(profile.get("model_cost") or {})
        model["benchmark_results"] = dict(profile.get("benchmark_results") or {})
        model["quality_calibration"] = profile.get("quality_calibration")
        model["model_release_timestamp"] = finite_profile_number(
            first_present(
                profile,
                "model_release_timestamp",
                "release_timestamp",
            )
        ) or finite_profile_number(first_present(model, "model_release_timestamp", "created"))
        model["input_price_per_million"] = input_price
        model["output_price_per_million"] = output_price
        model["supports_tools"] = resolved_supports_tools
        model["supports_json"] = resolved_supports_json
        model["supports_reasoning_effort"] = resolved_supports_reasoning_effort
        missing = [*profile_missing, *candidate_completeness_issues(model)]
        if missing:
            rejections.append({
                "model_id": model_id,
                "profile_alias": alias,
                "profile_model": profile.get("model"),
                "reason": "profile_incomplete",
                "missing": missing,
            })
            continue
        hydrated.append(normalize_model_identity_fields(model))
        seen_model_ids.add(model_id.lower())

    if len(hydrated) < MIN_ROUTE_CANDIDATES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_hydrated_model_catalog",
                "message": "At least five complete Mongo-profiled models are required",
                "usable_model_count": len(hydrated),
                "profile_rejections": rejections,
            },
        )
    return validate_hydrated_model_catalog(dedupe_canonical_models(hydrated, enrich=True)), rejections


def route_candidate_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = DEFAULT_ROUTE_CANDIDATES
    return max(MIN_ROUTE_CANDIDATES, min(MAX_ROUTE_CANDIDATES, limit))


def catalog_model_by_id(
    model_id: str,
    catalog: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for model in catalog:
        if model.get("model_id") == model_id or model.get("model") == model_id or model.get("id") == model_id:
            normalized = dict(model)
            normalized["model_id"] = model_id
            return normalized

    return None


def normalize_selector_result(
    *,
    selector_result: Any,
    catalog: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    route_candidates: dict[str, list[dict[str, Any]]] = {}
    assignments = selector_result
    if isinstance(selector_result, dict):
        assignments = selector_result.get("routes") or selector_result.get("assignments") or selector_result

    def normalize_models(models: list[Any]) -> list[dict[str, Any]]:
        normalized_models: list[dict[str, Any]] = []
        seen_model_ids: set[str] = set()
        for model in models:
            model_id = str(model.get("model") or model.get("model_id") or model) if isinstance(model, dict) else str(model)
            model_id = model_id.strip()
            if not model_id:
                continue
            if model_id in seen_model_ids:
                raise HTTPException(
                    status_code=502,
                    detail={"error": "duplicate_selector_model", "model_id": model_id},
                )
            normalized = catalog_model_by_id(model_id, catalog)
            if not normalized:
                raise HTTPException(
                    status_code=502,
                    detail={"error": "selector_model_not_in_catalog", "model_id": model_id},
                )
            normalized_models.append(normalized)
            seen_model_ids.add(model_id)
        return normalized_models

    if isinstance(assignments, dict):
        for route_name, models in assignments.items():
            if isinstance(models, dict):
                models = models.get("models", [])
            if not isinstance(models, list):
                continue
            route_candidates[str(route_name)] = normalize_models(models)
        return route_candidates

    if isinstance(assignments, list):
        for assignment in assignments:
            if not isinstance(assignment, dict):
                continue
            route_name = str(
                assignment.get("route")
                or assignment.get("route_id")
                or assignment.get("routeName")
                or ""
            ).strip()
            models = assignment.get("models") or []
            if not route_name or not isinstance(models, list):
                continue
            route_candidates[route_name] = normalize_models(models)
    return route_candidates


def validate_modal_selector_candidates(
    *,
    routes: dict[str, Any],
    catalog: list[dict[str, Any]],
    route_candidates: dict[str, list[dict[str, Any]]],
    target_limit: int,
    required_providers: list[str] | None = None,
    max_model_route_occurrences: int | None = None,
    route_occurrence_exempt_providers: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    catalog = executable_catalog(catalog)
    catalog_model_ids = {
        str(model.get("model_id") or model.get("model") or model.get("id") or "").strip()
        for model in catalog
    }
    catalog_model_ids.discard("")
    if len(catalog_model_ids) < MIN_ROUTE_CANDIDATES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_model_catalog",
                "message": f"model_catalog must contain at least {MIN_ROUTE_CANDIDATES} executable models for Modal route model selection",
                "catalog_model_count": len(catalog_model_ids),
            },
        )

    validated: dict[str, list[dict[str, Any]]] = {}
    issues: list[dict[str, Any]] = []
    required_providers = list(required_providers or [])
    route_occurrence_exempt_providers = list(route_occurrence_exempt_providers or [])
    for route_name in routes:
        models = list(route_candidates.get(route_name) or [])
        models = prefer_direct_provider_models(models, catalog)
        try:
            models = validate_complete_candidate_pool(models, route_name=route_name)
        except HTTPException as error:
            issues.append({
                "route": route_name,
                "error": "incomplete_candidate_pool",
                "detail": error.detail,
            })
            continue
        if len(models) > target_limit:
            issues.append(
                {
                    "route": route_name,
                    "error": "too_many_models_for_requested_limit",
                    "model_count": len(models),
                    "requested_limit": target_limit,
                }
            )
            continue
        model_ids = [
            str(model.get("model_id") or model.get("model") or model.get("id") or "").strip()
            for model in models
        ]
        model_ids = [model_id for model_id in model_ids if model_id]
        if len(model_ids) < MIN_ROUTE_CANDIDATES:
            issues.append(
                {
                    "route": route_name,
                    "error": "too_few_models",
                    "model_count": len(model_ids),
                    "expected": f"{MIN_ROUTE_CANDIDATES}-{MAX_ROUTE_CANDIDATES}",
                }
            )
            continue
        if len(model_ids) > MAX_ROUTE_CANDIDATES:
            issues.append(
                {
                    "route": route_name,
                    "error": "too_many_models",
                    "model_count": len(model_ids),
                    "expected": f"{MIN_ROUTE_CANDIDATES}-{MAX_ROUTE_CANDIDATES}",
                }
            )
            continue
        unknown_model_ids = [model_id for model_id in model_ids if model_id not in catalog_model_ids]
        if unknown_model_ids:
            issues.append(
                {
                    "route": route_name,
                    "error": "models_not_in_catalog",
                    "models": unknown_model_ids,
                }
            )
            continue
        selected_providers = {
            executable_provider_for_model(model)
            for model in models
        }
        missing_required_providers = [
            provider
            for provider in required_providers
            if provider not in selected_providers
        ]
        if missing_required_providers:
            issues.append(
                {
                    "route": route_name,
                    "error": "required_providers_missing",
                    "missing_providers": missing_required_providers,
                    "selected_providers": sorted(selected_providers),
                }
            )
            continue
        validated[route_name] = models

    if max_model_route_occurrences is not None:
        model_occurrences: Counter[str] = Counter()
        model_providers: dict[str, str] = {}
        for models in validated.values():
            for model in models:
                model_id = str(
                    model.get("model_id") or model.get("model") or model.get("id") or ""
                ).strip()
                if not model_id:
                    continue
                model_occurrences[model_id] += 1
                model_providers[model_id] = executable_provider_for_model(model)
        for model_id, occurrences in sorted(model_occurrences.items()):
            provider = model_providers.get(model_id, "")
            if (
                provider not in route_occurrence_exempt_providers
                and occurrences > max_model_route_occurrences
            ):
                issues.append(
                    {
                        "error": "model_route_occurrence_limit_exceeded",
                        "model_id": model_id,
                        "provider": provider,
                        "route_occurrences": occurrences,
                        "max_route_occurrences": max_model_route_occurrences,
                    }
                )

    if issues:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "invalid_modal_model_selector_result",
                "message": "Modal model selector must return 5-7 executable catalog models and all required providers for every route",
                "issues": issues,
            },
        )
    return validated


async def select_route_candidates(
    *,
    routes: dict[str, Any],
    catalog: list[dict[str, Any]],
    candidates_per_route: int,
    metadata: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    target_limit = route_candidate_limit(candidates_per_route)
    catalog = validate_hydrated_model_catalog(catalog)
    raw_required_providers = (metadata or {}).get("required_providers", [])
    if not isinstance(raw_required_providers, list):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_required_providers",
                "message": "metadata.required_providers must be a list",
            },
        )
    required_providers: list[str] = []
    for value in raw_required_providers:
        provider = str(value or "").strip().lower()
        if provider and provider not in required_providers:
            required_providers.append(provider)
    raw_max_occurrences = (metadata or {}).get("max_model_route_occurrences")
    max_model_route_occurrences: int | None = None
    if raw_max_occurrences is not None:
        if isinstance(raw_max_occurrences, bool):
            raise HTTPException(
                status_code=422,
                detail="metadata.max_model_route_occurrences must be a positive integer",
            )
        try:
            max_model_route_occurrences = int(raw_max_occurrences)
        except (TypeError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail="metadata.max_model_route_occurrences must be a positive integer",
            ) from error
        if max_model_route_occurrences <= 0 or max_model_route_occurrences > len(routes):
            raise HTTPException(
                status_code=422,
                detail="metadata.max_model_route_occurrences must be between 1 and the route count",
            )
    raw_exempt_providers = (metadata or {}).get("route_occurrence_exempt_providers", [])
    if not isinstance(raw_exempt_providers, list):
        raise HTTPException(
            status_code=422,
            detail="metadata.route_occurrence_exempt_providers must be a list",
        )
    route_occurrence_exempt_providers: list[str] = []
    for value in raw_exempt_providers:
        provider = str(value or "").strip().lower()
        if provider and provider not in route_occurrence_exempt_providers:
            route_occurrence_exempt_providers.append(provider)
    if MODEL_SELECTOR_URL:
        selector_payload = {
            "routes": [
                {
                    "route": route_name,
                    "trigger": route_definition.get("trigger", "")
                    if isinstance(route_definition, dict)
                    else str(route_definition),
                    "task": route_definition.get("task", route_definition.get("description", ""))
                    if isinstance(route_definition, dict)
                    else str(route_definition),
                    "quality_route_id": route_definition.get("quality_route_id")
                    if isinstance(route_definition, dict)
                    else None,
                }
                for route_name, route_definition in routes.items()
            ],
            "allowed_model_catalog": catalog,
            "allow_tests": True,
            "dry_run": bool((metadata or {}).get("dry_run")),
            "min_models": MIN_ROUTE_CANDIDATES,
            "max_models": MAX_ROUTE_CANDIDATES,
            "candidates_per_route": target_limit,
            "required_providers": required_providers,
            "max_model_route_occurrences": max_model_route_occurrences,
            "route_occurrence_exempt_providers": route_occurrence_exempt_providers,
            "max_model_researches": (metadata or {}).get("max_model_researches"),
            "max_fusion_comparisons": int((metadata or {}).get("max_fusion_comparisons", 40)),
            "max_iterations": int((metadata or {}).get("max_iterations", 48)),
            "provider_name": str((metadata or {}).get("model_selector_provider") or "openai"),
            "model": str((metadata or {}).get("model_selector_model") or "gpt-5.6-terra"),
            "reasoning_effort": str((metadata or {}).get("model_selector_reasoning_effort") or "medium"),
        }
        selector_result = await post_json_async(
            MODEL_SELECTOR_URL,
            internal_service_headers(),
            selector_payload,
            timeout_seconds=MODEL_SELECTOR_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        normalized = normalize_selector_result(selector_result=selector_result, catalog=catalog)
        if normalized:
            return validate_modal_selector_candidates(
                routes=routes,
                catalog=catalog,
                route_candidates=normalized,
                target_limit=target_limit,
                required_providers=required_providers,
                max_model_route_occurrences=max_model_route_occurrences,
                route_occurrence_exempt_providers=route_occurrence_exempt_providers,
            )

        raise HTTPException(
            status_code=502,
            detail={
                "error": "empty_modal_model_selector_result",
                "message": "Modal model selector returned no route assignments",
                "model_selector_url": MODEL_SELECTOR_URL,
            },
        )

    raise HTTPException(
        status_code=503,
        detail={
            "error": "modal_model_selector_not_configured",
            "message": "LEROUTER_MODEL_SELECTOR_URL must point to the Modal route model selector",
        },
    )


def candidate_pool_version(route_name: str, models: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        {"route_name": route_name, "models": models},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def public_candidate_pool_hash(route_name: str, models: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        {"route_name": route_name, "models": routing_models_without_internal_fields(models)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_candidate_pool(
    *,
    user_id: str | None,
    route_id: str,
    route_candidates: dict[str, list[dict[str, Any]]],
    metadata: dict[str, Any],
    route_definitions: dict[str, Any] | None = None,
) -> None:
    now_dt = datetime.now(timezone.utc)
    route_candidates = {
        route_name: [
            normalize_model_identity_fields(model)
            for model in validate_complete_candidate_pool(models, route_name=route_name)
        ]
        for route_name, models in route_candidates.items()
    }
    candidate_pool_versions: dict[str, str] = {}
    for route_name, models in route_candidates.items():
        missing_embeddings = [
            str(model.get("model_id") or model.get("model") or "")
            for model in models
            if not isinstance(model.get(PROFILE_EMBEDDING_FIELD), dict)
        ]
        if missing_embeddings:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "candidate_profile_embeddings_required",
                    "route_name": route_name,
                    "models": missing_embeddings,
                },
            )
        candidate_pool_versions[route_name] = candidate_pool_version(route_name, models)
    route_policy_routes = {
        route_name: [model["model_id"] for model in models]
        for route_name, models in route_candidates.items()
    }
    normalized_route_definitions = normalize_route_definitions(route_definitions)

    ensure_user_exists(user_id)
    persist_budget_metadata_from_setup(user_id=user_id, route_id=route_id, metadata=metadata)
    candidate_pools = require_mongo_collection("LEROUTER_ROUTE_CANDIDATE_POOL_COLLECTION", "route_candidate_pools")
    route_policies = require_mongo_collection("LEROUTER_ROUTE_POLICY_COLLECTION", "route_policies")

    for route_name, models in route_candidates.items():
        candidate_pools.update_one(
            {"userId": user_id, "routeId": route_id, "routeName": route_name},
            {
                "$set": {
                    "userId": user_id,
                    "routeId": route_id,
                    "routeName": route_name,
                    "models": models,
                    "poolVersion": candidate_pool_versions[route_name],
                    "metadata": metadata,
                    "updatedAt": now_dt,
                },
                "$setOnInsert": {
                    "id": str(uuid.uuid4()),
                    "createdAt": now_dt,
                },
            },
            upsert=True,
        )

    route_policies.update_one(
        {"routeId": route_id, "userId": user_id},
        {
            "$set": {
                "routeId": route_id,
                "userId": user_id,
                "routes": route_policy_routes,
                "candidatePoolVersions": candidate_pool_versions,
                "routeDefinitions": normalized_route_definitions,
                "metadata": metadata,
                "updatedAt": now_dt,
            },
            "$setOnInsert": {
                "id": str(uuid.uuid4()),
                "createdAt": now_dt,
            },
        },
        upsert=True,
    )
    candidate_pools.delete_many(
        {
            "userId": user_id,
            "routeId": route_id,
            "routeName": {"$nin": sorted(route_candidates)},
        }
    )


def save_catalog_wide_policy(
    *,
    user_id: str | None,
    route_id: str,
    catalog: list[dict[str, Any]],
    catalog_version: str,
    metadata: dict[str, Any],
) -> None:
    catalog = validate_hydrated_model_catalog(catalog)
    if not catalog_version or any(
        not isinstance(model.get(PROFILE_EMBEDDING_FIELD), dict) for model in catalog
    ):
        raise HTTPException(status_code=409, detail="Catalog-wide policy requires embedded model profiles")
    now = datetime.now(timezone.utc)
    policy_metadata = {
        **metadata,
        "routing_strategy": CATALOG_WIDE_ROUTING_STRATEGY,
    }
    ensure_user_exists(user_id)
    persist_budget_metadata_from_setup(user_id=user_id, route_id=route_id, metadata=policy_metadata)
    route_policies = require_mongo_collection("LEROUTER_ROUTE_POLICY_COLLECTION", "route_policies")
    route_policies.update_one(
        {"routeId": route_id, "userId": user_id},
        {
            "$set": {
                "routeId": route_id,
                "userId": user_id,
                "routes": {CATALOG_ROUTE_NAME: [model["model_id"] for model in catalog]},
                "candidatePoolVersions": {},
                "routeDefinitions": {},
                "modelCatalog": catalog,
                "catalogVersion": catalog_version,
                "routingStrategy": CATALOG_WIDE_ROUTING_STRATEGY,
                "metadata": policy_metadata,
                "updatedAt": now,
            },
            "$setOnInsert": {"id": str(uuid.uuid4()), "createdAt": now},
        },
        upsert=True,
    )
    candidate_pools = require_mongo_collection(
        "LEROUTER_ROUTE_CANDIDATE_POOL_COLLECTION",
        "route_candidate_pools",
    )
    candidate_pools.delete_many({"userId": user_id, "routeId": route_id})


def load_candidate_pool(
    *,
    user_id: str | None,
    route_id: str,
    route_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    candidate_pools = require_mongo_collection(
        "LEROUTER_ROUTE_CANDIDATE_POOL_COLLECTION",
        "route_candidate_pools",
    )
    try:
        row = candidate_pools.find_one(
            {"routeId": route_id, "routeName": route_name, "userId": user_id},
            sort=[("updatedAt", -1)],
        )
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "route_candidate_pool_store_unavailable",
                "message": "Mongo candidate pool storage could not be queried",
                "route_id": route_id,
                "route_name": route_name,
            },
        ) from error

    if row:
        models = row.get("models")
        if isinstance(models, str):
            try:
                models = json.loads(models)
            except json.JSONDecodeError:
                models = []
        if isinstance(models, list) and models:
            explicit_models = validate_complete_candidate_pool(models, route_name=route_name)
            return explicit_models[:route_candidate_limit(limit)]

    raise HTTPException(
        status_code=404,
        detail={
            "error": "route_candidate_pool_missing",
            "message": "No explicit Mongo candidate pool is stored for this route",
            "route_id": route_id,
            "route_name": route_name,
        },
    )


def load_candidate_pool_snapshot(
    *,
    user_id: str | None,
    route_id: str,
    limit: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    candidate_pools = require_mongo_collection(
        "LEROUTER_ROUTE_CANDIDATE_POOL_COLLECTION",
        "route_candidate_pools",
    )
    try:
        rows = candidate_pools.find(
            {"routeId": route_id, "userId": user_id},
            {
                "_id": 0,
                "routeName": 1,
                "models": {"$slice": MAX_ROUTE_CANDIDATES},
                "poolVersion": 1,
                "updatedAt": 1,
            },
        ).sort([("routeName", 1), ("updatedAt", -1)])
        latest_by_route: dict[str, dict[str, Any]] = {}
        for row in rows:
            route_name = str(row.get("routeName") or "").strip()
            if route_name and route_name not in latest_by_route:
                latest_by_route[route_name] = row
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "route_candidate_pool_store_unavailable",
                "message": "Mongo candidate pool storage could not be queried",
                "route_id": route_id,
            },
        ) from error

    pools: dict[str, list[dict[str, Any]]] = {}
    versions: dict[str, str] = {}
    for route_name, row in latest_by_route.items():
        models = row.get("models")
        if isinstance(models, str):
            try:
                models = json.loads(models)
            except json.JSONDecodeError as error:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "invalid_candidate_pool_json", "route_name": route_name},
                ) from error
        validated = validate_complete_candidate_pool(models, route_name=route_name)
        stored_version = str(row.get("poolVersion") or "").strip()
        actual_version = candidate_pool_version(route_name, validated)
        if not stored_version or stored_version != actual_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "candidate_pool_version_mismatch",
                    "route_name": route_name,
                    "stored_version": stored_version or None,
                    "actual_version": actual_version,
                },
            )
        pools[route_name] = validated
        versions[route_name] = stored_version
    return pools, versions


def load_versioned_candidate_pool(
    *,
    user_id: str | None,
    route_id: str,
    route_name: str,
    expected_pool_version: str,
    limit: int,
) -> list[dict[str, Any]]:
    candidate_pools = require_mongo_collection(
        "LEROUTER_ROUTE_CANDIDATE_POOL_COLLECTION",
        "route_candidate_pools",
    )
    try:
        row = candidate_pools.find_one(
            {"routeId": route_id, "routeName": route_name, "userId": user_id},
            {
                "_id": 0,
                "routeName": 1,
                "models": {"$slice": MAX_ROUTE_CANDIDATES},
                "poolVersion": 1,
            },
            sort=[("updatedAt", -1)],
        )
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "route_candidate_pool_store_unavailable",
                "message": "Mongo candidate pool storage could not be queried",
                "route_id": route_id,
                "route_name": route_name,
            },
        ) from error
    if not row:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "route_candidate_pool_missing",
                "route_id": route_id,
                "route_name": route_name,
            },
        )
    models = validate_complete_candidate_pool(row.get("models"), route_name=route_name)
    stored_version = str(row.get("poolVersion") or "").strip()
    actual_version = candidate_pool_version(route_name, models)
    if (
        not expected_pool_version
        or stored_version != expected_pool_version
        or actual_version != expected_pool_version
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "candidate_pool_version_mismatch",
                "route_name": route_name,
                "expected_version": expected_pool_version or None,
                "stored_version": stored_version or None,
                "actual_version": actual_version,
            },
        )
    return models[:route_candidate_limit(limit)]


def load_candidate_pools_by_route(
    *,
    user_id: str | None,
    route_id: str,
    route_names: list[str],
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    global CANDIDATE_POOL_INDEX_READY
    candidate_pools = require_mongo_collection(
        "LEROUTER_ROUTE_CANDIDATE_POOL_COLLECTION",
        "route_candidate_pools",
    )
    if not CANDIDATE_POOL_INDEX_READY:
        candidate_pools.create_index(
            [
                ("userId", 1),
                ("routeId", 1),
                ("routeName", 1),
                ("updatedAt", -1),
            ],
            name="candidate_pool_route_lookup",
        )
        CANDIDATE_POOL_INDEX_READY = True
    query = {
        "routeId": route_id,
        "routeName": {"$in": route_names},
        "userId": user_id,
    }
    try:
        rows = candidate_pools.find(
            query,
            {
                "routeName": 1,
                "models": {"$slice": route_candidate_limit(limit)},
                "updatedAt": 1,
            },
        ).sort(
            [("routeName", 1), ("updatedAt", -1)]
        )
        latest_by_route: dict[str, dict[str, Any]] = {}
        for row in rows:
            route_name = str(row.get("routeName") or "").strip()
            if route_name in route_names and route_name not in latest_by_route:
                latest_by_route[route_name] = row
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "route_candidate_pool_store_unavailable",
                "message": "Mongo candidate pool storage could not be queried",
                "route_id": route_id,
            },
        ) from error

    pools: dict[str, list[dict[str, Any]]] = {}
    for route_name in route_names:
        row = latest_by_route.get(route_name)
        models = row.get("models") if isinstance(row, dict) else None
        if isinstance(models, str):
            try:
                models = json.loads(models)
            except json.JSONDecodeError:
                models = []
        if not isinstance(models, list) or not models:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "route_candidate_pool_missing",
                    "message": "No explicit Mongo candidate pool is stored for this route",
                    "route_id": route_id,
                    "route_name": route_name,
                },
            )
        pools[route_name] = validate_complete_candidate_pool(
            models,
            route_name=route_name,
        )[:route_candidate_limit(limit)]
    return pools


def e5_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    missing = candidate_completeness_issues(candidate)
    if missing:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "incomplete_e5_candidate",
                "model_id": candidate.get("model_id"),
                "missing": missing,
            },
        )
    gemma_profile_missing = []
    forces_value = candidate.get("forces")
    if (
        not isinstance(forces_value, list)
        or not forces_value
        or any(
            not isinstance(item, str) or not item.strip()
            for item in forces_value
        )
    ):
        gemma_profile_missing.append("forces")
    if finite_profile_number(candidate.get("context_window"), positive=True) is None:
        gemma_profile_missing.append("context_window")
    model_cost = candidate.get("model_cost")
    if not isinstance(model_cost, dict):
        gemma_profile_missing.append("model_cost")
    else:
        for price_key in ("input_usd_per_million", "output_usd_per_million"):
            price_value = finite_profile_number(model_cost.get(price_key))
            if price_value is None or price_value < 0:
                gemma_profile_missing.append(f"model_cost.{price_key}")
    if not isinstance(candidate.get("benchmark_results"), dict):
        gemma_profile_missing.append("benchmark_results")
    if gemma_profile_missing:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "incomplete_gemma_profile",
                "model_id": candidate.get("model_id"),
                "missing": gemma_profile_missing,
            },
        )
    strengths = [str(item) for item in candidate["strengths"]]
    forces = [item.strip() for item in forces_value]
    weaknesses = [str(item) for item in candidate.get("weaknesses") or []]
    input_price = float(candidate["input_price_per_million"])
    output_price = float(candidate["output_price_per_million"])
    return {
        "model": candidate["model_id"],
        "model_id": candidate["model_id"],
        "canonical_model_id": candidate.get("canonical_model_id") or canonical_model_identity(candidate),
        "provider": candidate.get("provider") or candidate.get("execution_provider"),
        "execution_provider": candidate.get("execution_provider") or candidate.get("provider"),
        "native_model_id": candidate.get("native_model_id") or candidate.get("provider_native_model_id"),
        "openrouter_native_model_id": candidate.get("openrouter_native_model_id"),
        "forces": forces,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "context_window": int(candidate["context_window"]),
        "model_cost": dict(model_cost),
        "benchmark_results": dict(candidate.get("benchmark_results") or {}),
        "quality_calibration": candidate["quality_calibration"],
        "context_k_tokens": float(candidate["context_k_tokens"]),
        "latency_ms": float(candidate["latency_ms"]),
        "expected_cost_usd": (input_price + output_price) / 1_000,
        # The Modal router receives the inferred route schema, so it can turn
        # these prices into the same task-specific cost feature seen in E5
        # training instead of relying on this static compatibility value.
        "input_price_per_million": input_price,
        "output_price_per_million": output_price,
        "supports_tools": candidate["supports_tools"],
        "supports_json": candidate["supports_json"],
    }


async def call_modal_e5_router(
    *,
    route_schema: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not USE_MODAL_MODELS:
        raise HTTPException(status_code=503, detail="Modal routing models are disabled")
    if not E5_ROUTER_URL:
        raise HTTPException(status_code=503, detail="LEROUTER_E5_ROUTER_URL is required")

    body = {
        "route_schema": route_schema,
        "candidates": [e5_candidate(candidate) for candidate in candidates],
        "top_k": len(candidates),
    }

    return await post_json_async(
        E5_ROUTER_URL,
        internal_service_headers(),
        body,
    )


async def biencoder_rank(
    *,
    messages: list[dict[str, str]],
    route_name: str,
    route_schema: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    modal_result = await call_modal_e5_router(route_schema=route_schema, candidates=candidates)
    if not isinstance(modal_result, dict):
        raise HTTPException(status_code=502, detail="Remote E5 router returned a non-object response")
    return ranked_candidates_from_modal(candidates=candidates, modal_result=modal_result, source="modal_e5")


def first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def parse_required_number(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "budget_scoring_input_required",
                "message": f"{field_name} is required for budget-aware scoring.",
                "field": field_name,
            },
        ) from error

    if number != number:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "budget_scoring_input_required",
                "message": f"{field_name} must be a valid number.",
                "field": field_name,
            },
        )
    return number


def parse_optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def normalize_spend_accounting_mode(
    value: Any,
    *,
    status_code: int,
) -> str:
    normalized = str(value or SPEND_ACCOUNTING_ROUTING_FEE).strip().lower()
    if normalized not in SPEND_ACCOUNTING_MODES:
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": "invalid_spend_accounting_mode",
                "message": "spendAccountingMode must be routing_fee or provider_spend",
                "spend_accounting_mode": normalized,
            },
        )
    return normalized


def budget_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "budgetUsd": parse_optional_number(first_present(row, "budgetUsd", "budget_usd")),
        "budgetRemainingUsd": parse_optional_number(
            first_present(row, "budgetRemainingUsd", "remainingBudgetUsd", "remaining_budget_usd")
        ),
        "remainingWeight": parse_optional_number(
            first_present(
                row,
                "remainingWeight",
                "remaining_weight",
                "weightRemaining",
                "weight_remaining",
                "remainingTimelineWeight",
                "remaining_timeline_weight",
            )
        ),
        "totalPredictedWeight": parse_optional_number(
            first_present(
                row,
                "totalPredictedWeight",
                "total_predicted_weight",
                "timelineWeight",
                "timeline_weight",
            )
        ),
        "medianWeightedTokens": parse_optional_number(
            first_present(row, "medianWeightedTokens", "median_weighted_tokens")
        ),
        "averageRequestsPerPeriod": parse_optional_number(
            first_present(
                row,
                "averageRequestsPerPeriod",
                "average_requests_per_period",
                "requestCountLastTimestamp",
                "request_count_last_timestamp",
                "numberOfRequestsLastTimestamp",
                "number_of_request_last_timestamp",
            )
        ),
        "outputTokenWeight": parse_optional_number(
            first_present(row, "outputTokenWeight", "output_token_weight", "k_out")
        ),
        "requestWeightBeta": parse_optional_number(
            first_present(row, "requestWeightBeta", "request_weight_beta", "beta")
        ),
        "requestDifficultyAlpha": parse_optional_number(
            first_present(row, "requestDifficultyAlpha", "request_difficulty_alpha")
        ),
        "requestWeightMin": parse_optional_number(
            first_present(row, "requestWeightMin", "request_weight_min", "r_min")
        ),
        "requestWeightCapMultiplier": parse_optional_number(
            first_present(row, "requestWeightCapMultiplier", "request_weight_cap_multiplier", "cap_multiplier")
        ),
        "difficultyWeightAlpha": parse_optional_number(
            first_present(row, "difficultyWeightAlpha", "difficulty_weight_alpha", "alpha")
        ),
        "budgetShadowPrice": parse_optional_number(
            first_present(row, "budgetShadowPrice", "budget_shadow_price")
        ),
        "budgetControllerLearningRate": parse_optional_number(
            first_present(row, "budgetControllerLearningRate", "budget_controller_learning_rate")
        ),
        "budgetShadowPriceMin": parse_optional_number(
            first_present(row, "budgetShadowPriceMin", "budget_shadow_price_min")
        ),
        "budgetShadowPriceMax": parse_optional_number(
            first_present(row, "budgetShadowPriceMax", "budget_shadow_price_max")
        ),
        "spendAccountingMode": first_present(row, "spendAccountingMode", "spend_accounting_mode"),
    }


def budget_metadata_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    budget = metadata.get("budget") if isinstance(metadata, dict) else None
    if not isinstance(budget, dict):
        return {}

    field_map = {
        "budgetUsd": first_present(budget, "budgetUsd", "budget_usd", "amount_usd"),
        "budgetRemainingUsd": first_present(
            budget,
            "budgetRemainingUsd",
            "remainingBudgetUsd",
            "remaining_budget_usd",
            "remaining_usd",
        ),
        "remainingWeight": first_present(
            budget,
            "remainingWeight",
            "remaining_weight",
            "weightRemaining",
            "weight_remaining",
            "remainingTimelineWeight",
            "remaining_timeline_weight",
        ),
        "totalPredictedWeight": first_present(
            budget,
            "totalPredictedWeight",
            "total_predicted_weight",
            "timelineWeight",
            "timeline_weight",
        ),
        "medianWeightedTokens": first_present(budget, "medianWeightedTokens", "median_weighted_tokens"),
        "averageRequestsPerPeriod": first_present(
            budget,
            "averageRequestsPerPeriod",
            "average_requests_per_period",
            "requestCountLastTimestamp",
            "request_count_last_timestamp",
            "numberOfRequestsLastTimestamp",
            "number_of_request_last_timestamp",
        ),
        "outputTokenWeight": first_present(budget, "outputTokenWeight", "output_token_weight", "k_out"),
        "requestWeightBeta": first_present(budget, "requestWeightBeta", "request_weight_beta", "beta"),
        "requestDifficultyAlpha": first_present(
            budget,
            "requestDifficultyAlpha",
            "request_difficulty_alpha",
            "difficultyWeightAlpha",
            "difficulty_weight_alpha",
            "alpha",
        ),
        "requestWeightMin": first_present(budget, "requestWeightMin", "request_weight_min", "r_min"),
        "requestWeightCapMultiplier": first_present(
            budget,
            "requestWeightCapMultiplier",
            "request_weight_cap_multiplier",
            "cap_multiplier",
        ),
        "budgetShadowPrice": first_present(budget, "budgetShadowPrice", "budget_shadow_price"),
        "budgetControllerLearningRate": first_present(
            budget,
            "budgetControllerLearningRate",
            "budget_controller_learning_rate",
        ),
        "budgetShadowPriceMin": first_present(budget, "budgetShadowPriceMin", "budget_shadow_price_min"),
        "budgetShadowPriceMax": first_present(budget, "budgetShadowPriceMax", "budget_shadow_price_max"),
    }
    fields: dict[str, Any] = {
        key: number
        for key, value in field_map.items()
        if (number := parse_optional_number(value)) is not None
    }
    if not fields:
        return {}
    fields.setdefault("budgetShadowPrice", BUDGET_SHADOW_PRICE_INITIAL)
    fields.setdefault("budgetControllerLearningRate", BUDGET_CONTROLLER_LEARNING_RATE)
    fields.setdefault("budgetShadowPriceMin", BUDGET_SHADOW_PRICE_MIN)
    fields.setdefault("budgetShadowPriceMax", BUDGET_SHADOW_PRICE_MAX)
    if (
        not all(
            math.isfinite(fields[key])
            for key in (
                "budgetShadowPrice",
                "budgetControllerLearningRate",
                "budgetShadowPriceMin",
                "budgetShadowPriceMax",
            )
        )
        or fields["budgetShadowPriceMin"] < 0
        or fields["budgetShadowPriceMax"] < fields["budgetShadowPriceMin"]
        or not fields["budgetShadowPriceMin"] <= fields["budgetShadowPrice"] <= fields["budgetShadowPriceMax"]
        or fields["budgetControllerLearningRate"] < 0
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_budget_controller",
                "message": "Budget shadow-price controller values are inconsistent",
            },
        )
    fields["spendAccountingMode"] = normalize_spend_accounting_mode(
        first_present(budget, "spendAccountingMode", "spend_accounting_mode"),
        status_code=422,
    )
    return fields


def sorted_numbers(values: list[float]) -> list[float]:
    return sorted(value for value in values if value == value)


def median_number(values: list[float]) -> float | None:
    numbers = sorted_numbers(values)
    if not numbers:
        return None
    midpoint = len(numbers) // 2
    if len(numbers) % 2:
        return numbers[midpoint]
    return (numbers[midpoint - 1] + numbers[midpoint]) / 2


def budget_cycle_days(cycle: Any) -> float:
    normalized = str(cycle or "monthly").strip().lower()
    return {
        "weekly": 7.0,
        "week": 7.0,
        "monthly": 30.0,
        "month": 30.0,
        "quarterly": 91.0,
        "quarter": 91.0,
        "yearly": 365.0,
        "annual": 365.0,
        "year": 365.0,
    }.get(normalized, 30.0)


def history_sample_timestamp(sample: dict[str, Any]) -> float | None:
    value = first_present(sample, "timestamp", "created_at", "createdAt", "started_at", "startedAt")
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 10_000_000_000 else number
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def history_sample_text(sample: dict[str, Any]) -> str:
    messages = sample.get("messages")
    if isinstance(messages, list):
        return messages_text(normalize_messages({"messages": messages}))
    return str(first_present(sample, "content", "prompt", "input", "query", "text") or "")


def history_sample_messages(sample: dict[str, Any]) -> list[dict[str, Any]]:
    messages = sample.get("messages")
    if isinstance(messages, list) and messages:
        return normalize_messages({"messages": messages})
    return [{"role": "user", "content": history_sample_text(sample)}]


def history_input_tokens(sample: dict[str, Any], text: str) -> float:
    explicit = parse_optional_number(
        first_present(sample, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens", "token_count", "tokenCount")
    )
    if explicit is not None and explicit > 0:
        return explicit
    return max(1.0, len(text) / 4.0)


def history_output_tokens(sample: dict[str, Any], route_schema: dict[str, Any]) -> float:
    explicit = parse_optional_number(
        first_present(sample, "output_tokens", "outputTokens", "completion_tokens", "completionTokens")
    )
    if explicit is not None and explicit > 0:
        return explicit
    return predicted_output_tokens(route_schema, {})


def history_request_count(sample: dict[str, Any]) -> float:
    count = parse_required_number(
        first_present(sample, "request_count", "requestCount", "api_call_count", "apiCallCount"),
        "history request count",
    )
    if count <= 0:
        raise HTTPException(status_code=422, detail="history request count must be greater than zero")
    return count


def normalize_history_samples(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    budget = metadata.get("budget") if isinstance(metadata, dict) else None
    if not isinstance(budget, dict):
        return []
    samples = first_present(budget, "historyQueries", "history_queries", "historySamples", "history_samples")
    if not isinstance(samples, list):
        return []
    normalized = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        text = history_sample_text(sample).strip()
        if not text:
            continue
        normalized.append({**sample, "content": text[:6000]})
    return normalized


async def compute_budget_fields_from_history(
    *,
    route_id: str,
    routes: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    budget = metadata.get("budget") if isinstance(metadata, dict) else None
    if not isinstance(budget, dict):
        return metadata

    samples = normalize_history_samples(metadata)
    if not samples:
        return metadata

    route_names = [
        str(route_name).strip()
        for route_name in routes.keys()
        if str(route_name).strip()
    ]
    if len(route_names) < 2:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "budget_history_routes_required",
                "message": "At least two routes are required before ArchRouter can classify history for W.",
            },
        )

    output_token_weight = as_float(
        first_present(budget, "outputTokenWeight", "output_token_weight", "k_out"),
        REQUEST_OUTPUT_TOKEN_WEIGHT_K,
    )
    request_size_beta = as_float(
        first_present(budget, "requestWeightBeta", "request_weight_beta", "beta"),
        REQUEST_SIZE_BETA,
    )
    request_difficulty_alpha = as_float(
        first_present(
            budget,
            "requestDifficultyAlpha",
            "request_difficulty_alpha",
            "difficultyWeightAlpha",
            "difficulty_weight_alpha",
            "alpha",
        ),
        REQUEST_DIFFICULTY_ALPHA,
    )
    cycle_days = as_float(first_present(budget, "cycleDays", "cycle_days"), budget_cycle_days(budget.get("cycle")))

    async def classify_sample(sample: dict[str, Any]) -> dict[str, Any]:
        messages = history_sample_messages(sample)
        text = messages_text(messages)
        lfm_result = await call_lfm2_metadata(task=text)
        lfm_metadata = lfm_result["metadata"]
        difficulty = parse_required_number(lfm_metadata.get("x"), "lfm2_metadata.difficulty")
        route_schema = {"difficulty": difficulty}
        input_tokens = history_input_tokens(sample, text)
        output_tokens = history_output_tokens(sample, route_schema)
        weighted_tokens = input_tokens + max(0.0, output_token_weight) * output_tokens
        return {
            "source": "lfm2_metadata",
            "model": lfm_result["model"],
            "adapter_run": lfm_result["adapter_run"],
            "difficulty": route_difficulty(route_schema),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "weighted_tokens": weighted_tokens,
            "request_count": history_request_count(sample),
            "timestamp": history_sample_timestamp(sample),
        }

    semaphore = asyncio.Semaphore(SETUP_HISTORY_LFM_CONCURRENCY)

    async def bounded_classify(sample: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await classify_sample(sample)

    classified_samples = list(await asyncio.gather(*(bounded_classify(sample) for sample in samples)))
    weighted_tokens_values = [sample["weighted_tokens"] for sample in classified_samples]

    median_weighted_tokens = median_number(weighted_tokens_values)
    if median_weighted_tokens is None or median_weighted_tokens <= 0:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "budget_history_weight_required",
                "message": "History queries did not produce positive weighted token samples for W.",
            },
        )

    request_weights = []
    for sample in classified_samples:
        size_factor = sample["weighted_tokens"] / median_weighted_tokens
        request_weights.append(
            (max(0.0, size_factor) ** request_size_beta)
            * math.exp(request_difficulty_alpha * sample["difficulty"])
            * sample["request_count"]
        )

    timestamps = [
        sample["timestamp"]
        for sample in classified_samples
        if sample.get("timestamp") is not None
    ]
    observed_days = as_float(first_present(budget, "historyPeriodDays", "history_period_days"), 0.0)
    if observed_days <= 0 and len(timestamps) >= 2:
        observed_days = max(1.0, (max(timestamps) - min(timestamps)) / 86400.0)
    if observed_days <= 0:
        observed_days = cycle_days
    period_scale = cycle_days / observed_days
    observed_request_count = sum(sample["request_count"] for sample in classified_samples)
    total_observed_difficulty = sum(
        sample["difficulty"] * sample["request_count"]
        for sample in classified_samples
    )
    average_difficulty = total_observed_difficulty / observed_request_count
    average_requests = max(1.0, observed_request_count * period_scale)
    total_predicted_weight = max(0.000001, sum(request_weights) * period_scale)
    existing_remaining_weight = parse_optional_number(
        first_present(budget, "remainingWeight", "remaining_weight", "weightRemaining", "weight_remaining")
    )
    remaining_weight = (
        min(existing_remaining_weight, total_predicted_weight)
        if existing_remaining_weight is not None and existing_remaining_weight > 0
        else total_predicted_weight
    )
    controller_fields: dict[str, float] = {}
    for output_key, input_keys in (
        ("budget_shadow_price", ("budgetShadowPrice", "budget_shadow_price")),
        (
            "budget_controller_learning_rate",
            ("budgetControllerLearningRate", "budget_controller_learning_rate"),
        ),
        ("budget_shadow_price_min", ("budgetShadowPriceMin", "budget_shadow_price_min")),
        ("budget_shadow_price_max", ("budgetShadowPriceMax", "budget_shadow_price_max")),
    ):
        value = first_present(budget, *input_keys)
        if value is not None:
            controller_fields[output_key] = as_float(value)

    next_budget = {
        **budget,
        "total_predicted_weight": total_predicted_weight,
        "remaining_weight": remaining_weight,
        "median_weighted_tokens": median_weighted_tokens,
        "average_requests_per_period": average_requests,
        "output_token_weight": output_token_weight,
        "request_weight_beta": request_size_beta,
        "request_difficulty_alpha": request_difficulty_alpha,
        "request_weight_min": as_float(first_present(budget, "requestWeightMin", "request_weight_min"), REQUEST_WEIGHT_MIN),
        "request_weight_cap_multiplier": as_float(
            first_present(budget, "requestWeightCapMultiplier", "request_weight_cap_multiplier"),
            REQUEST_WEIGHT_CAP_MULTIPLIER,
        ),
        **controller_fields,
        "total_observed_difficulty": total_observed_difficulty,
        "average_observed_difficulty": average_difficulty,
        "w_source": "lfm2_metadata_history",
        "w_observed_sample_count": len(classified_samples),
        "w_observed_request_count": observed_request_count,
        "w_observed_period_days": observed_days,
    }
    next_budget.pop("historyQueries", None)
    next_budget.pop("history_queries", None)
    next_budget.pop("historySamples", None)
    next_budget.pop("history_samples", None)
    next_budget.pop("budgetMalusGamma", None)
    next_budget.pop("budget_malus_gamma", None)
    next_budget.pop("gamma", None)
    return {
        **metadata,
        "budget": next_budget,
        "budget_history_classification": {
            "source": "lfm2_metadata_history",
            "model": LFM_METADATA_MODEL_ID,
            "adapter_run": LFM_METADATA_ADAPTER_RUN,
            "sample_count": len(classified_samples),
            "request_count": observed_request_count,
            "total_difficulty": total_observed_difficulty,
            "average_difficulty": average_difficulty,
            "observed_period_days": observed_days,
            "cycle_days": cycle_days,
            "period_scale": period_scale,
        },
    }


def persist_budget_metadata_from_setup(
    *,
    user_id: str | None,
    route_id: str,
    metadata: dict[str, Any],
) -> None:
    if not user_id:
        raise HTTPException(status_code=422, detail="setup budget persistence requires user_id")

    fields = budget_metadata_fields(metadata)
    if not fields:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "setup_budget_metadata_required",
                "message": "Setup must compute budget and request-weight fields before saving route pools",
                "route_id": route_id,
            },
        )

    try:
        user_budgets = require_mongo_collection("LEROUTER_USER_BUDGET_COLLECTION", "user_budget_states")
        query = {"loginInfo.userId": user_id, "routeId": route_id}
        existing = user_budgets.find_one(query)
        budget_payload = metadata.get("budget") if isinstance(metadata.get("budget"), dict) else {}
        for field_name, aliases in (
            ("budgetShadowPrice", ("budgetShadowPrice", "budget_shadow_price")),
            (
                "budgetControllerLearningRate",
                ("budgetControllerLearningRate", "budget_controller_learning_rate"),
            ),
            ("budgetShadowPriceMin", ("budgetShadowPriceMin", "budget_shadow_price_min")),
            ("budgetShadowPriceMax", ("budgetShadowPriceMax", "budget_shadow_price_max")),
        ):
            if first_present(budget_payload, *aliases) is not None or not isinstance(existing, dict):
                continue
            persisted_value = parse_optional_number(first_present(existing, *aliases))
            if persisted_value is not None:
                fields[field_name] = persisted_value
        fields = budget_metadata_fields({"budget": fields})
        if "budgetRemainingUsd" not in fields and fields.get("budgetUsd") is not None:
            existing_remaining = (
                parse_optional_number(
                    first_present(
                        existing,
                        "budgetRemainingUsd",
                        "remainingBudgetUsd",
                        "remaining_budget_usd",
                        "remaining_usd",
                    )
                )
                if isinstance(existing, dict)
                else None
            )
            existing_spend = (
                parse_optional_number(first_present(existing, "totalSpendUsd", "total_spend_usd")) or 0.0
                if isinstance(existing, dict)
                else 0.0
            )
            budget_usd = fields["budgetUsd"]
            if existing_remaining is None:
                fields["budgetRemainingUsd"] = budget_usd
            elif existing_remaining <= 0 and existing_spend < budget_usd:
                fields["budgetRemainingUsd"] = max(0.0, budget_usd - existing_spend)

        # The user_budget_states collection validator requires the budget
        # window fields on every document. Older setup payloads only carried
        # the dollar budget, so derive the window length from the configured
        # cycle and preserve any existing elapsed-day value.
        cycle_days = parse_optional_number(
            first_present(budget_payload, "cycleDays", "cycle_days")
        ) or budget_cycle_days(budget_payload.get("cycle"))
        existing_timestamp_days = (
            parse_optional_number(first_present(existing, "timeStampDays", "timestamp_days"))
            if isinstance(existing, dict)
            else None
        )
        existing_spend_days = (
            parse_optional_number(first_present(existing, "timeSpendDays", "time_spend_days"))
            if isinstance(existing, dict)
            else None
        )
        fields.setdefault("timeStampDays", existing_timestamp_days or cycle_days)
        fields.setdefault("timeSpendDays", existing_spend_days or 0.0)

        now = datetime.now(timezone.utc)
        update_fields = {
            "loginInfo.userId": user_id,
            "routeId": route_id,
            "updatedAt": now,
            **fields,
        }
        budget_usd = fields.get("budgetUsd", 0.0)
        budget_remaining_usd = fields.get("budgetRemainingUsd", budget_usd)
        set_on_insert = {
            "createdAt": now,
            "budgetUsd": budget_usd,
            "budgetRemainingUsd": budget_remaining_usd,
            "totalRequests": 0,
            "totalSpendUsd": 0,
            "successfulRequests": 0,
            "failedRequests": 0,
            "successRate": 0,
            "providerCompletions": {},
            "labCompletions": {},
        }
        for key in update_fields:
            set_on_insert.pop(key, None)

        user_budgets.update_one(
            query,
            {
                "$set": update_fields,
                "$setOnInsert": set_on_insert,
                "$unset": {
                    "budgetMalusGamma": "",
                    "budget_malus_gamma": "",
                    "gamma": "",
                },
            },
            upsert=True,
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "setup_budget_persistence_failed",
                "message": "Exact route budget state could not be persisted",
                "route_id": route_id,
            },
        ) from error


def normalize_budget_state(raw_budget: dict[str, Any], route_id: str) -> dict[str, Any]:
    remaining_budget = parse_required_number(
        first_present(
            raw_budget,
            "budgetRemainingUsd",
            "remainingBudgetUsd",
            "remaining_budget_usd",
            "remaining_usd",
        ),
        "budgetRemainingUsd",
    )
    remaining_weight = parse_required_number(
        first_present(
            raw_budget,
            "remainingWeight",
            "remaining_weight",
            "weightRemaining",
            "weight_remaining",
            "remainingTimelineWeight",
            "remaining_timeline_weight",
        ),
        "remainingWeight",
    )
    if remaining_weight < 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "budget_weight_required",
                "message": "remainingWeight must be non-negative for budget-aware scoring.",
                "route_id": route_id,
                "field": "remainingWeight",
            },
        )
    total_predicted_weight = parse_required_number(
        first_present(
            raw_budget,
            "totalPredictedWeight",
            "total_predicted_weight",
            "timelineWeight",
            "timeline_weight",
        ),
        "totalPredictedWeight",
    )
    if total_predicted_weight <= 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "budget_weight_required",
                "message": "totalPredictedWeight must be greater than zero for budget-aware scoring.",
                "route_id": route_id,
                "field": "totalPredictedWeight",
            },
        )
    median_weighted_tokens = parse_required_number(
        first_present(raw_budget, "medianWeightedTokens", "median_weighted_tokens"),
        "medianWeightedTokens",
    )
    if median_weighted_tokens <= 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "budget_weight_required",
                "message": "medianWeightedTokens must be greater than zero for budget-aware scoring.",
                "route_id": route_id,
                "field": "medianWeightedTokens",
            },
        )
    average_requests = parse_required_number(
        first_present(
            raw_budget,
            "averageRequestsPerPeriod",
            "average_requests_per_period",
            "requestCountLastTimestamp",
            "request_count_last_timestamp",
            "numberOfRequestsLastTimestamp",
            "number_of_request_last_timestamp",
        ),
        "averageRequestsPerPeriod",
    )
    if average_requests <= 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "budget_request_count_required",
                "message": "averageRequestsPerPeriod must be greater than zero for budget-aware scoring.",
                "route_id": route_id,
                "field": "averageRequestsPerPeriod",
            },
        )

    shadow_price = parse_required_number(
        first_present(raw_budget, "budgetShadowPrice", "budget_shadow_price"),
        "budgetShadowPrice",
    )
    controller_learning_rate = parse_required_number(
        first_present(raw_budget, "budgetControllerLearningRate", "budget_controller_learning_rate"),
        "budgetControllerLearningRate",
    )
    shadow_price_min = parse_required_number(
        first_present(raw_budget, "budgetShadowPriceMin", "budget_shadow_price_min"),
        "budgetShadowPriceMin",
    )
    shadow_price_max = parse_required_number(
        first_present(raw_budget, "budgetShadowPriceMax", "budget_shadow_price_max"),
        "budgetShadowPriceMax",
    )
    if (
        not all(
            math.isfinite(value)
            for value in (
                shadow_price,
                controller_learning_rate,
                shadow_price_min,
                shadow_price_max,
            )
        )
        or shadow_price_min < 0
        or shadow_price_max < shadow_price_min
        or not shadow_price_min <= shadow_price <= shadow_price_max
        or controller_learning_rate < 0
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "invalid_budget_controller",
                "message": "Budget shadow-price controller values are inconsistent",
                "route_id": route_id,
            },
        )

    return {
        "budgetUsd": parse_optional_number(first_present(raw_budget, "budgetUsd", "amount_usd", "budget_usd")),
        "budgetRemainingUsd": max(0.0, remaining_budget),
        "remainingWeight": remaining_weight,
        "totalPredictedWeight": total_predicted_weight,
        "medianWeightedTokens": median_weighted_tokens,
        "averageRequestsPerPeriod": average_requests,
        "outputTokenWeight": parse_optional_number(
            first_present(raw_budget, "outputTokenWeight", "output_token_weight", "k_out")
        ),
        "requestWeightBeta": parse_optional_number(
            first_present(raw_budget, "requestWeightBeta", "request_weight_beta", "beta")
        ),
        "requestDifficultyAlpha": parse_optional_number(
            first_present(raw_budget, "requestDifficultyAlpha", "request_difficulty_alpha")
        ),
        "requestWeightMin": parse_optional_number(
            first_present(raw_budget, "requestWeightMin", "request_weight_min", "r_min")
        ),
        "requestWeightCapMultiplier": parse_optional_number(
            first_present(raw_budget, "requestWeightCapMultiplier", "request_weight_cap_multiplier", "cap_multiplier")
        ),
        "difficultyWeightAlpha": parse_optional_number(
            first_present(raw_budget, "difficultyWeightAlpha", "difficulty_weight_alpha", "alpha")
        ),
        "budgetShadowPrice": shadow_price,
        "budgetControllerLearningRate": controller_learning_rate,
        "budgetShadowPriceMin": shadow_price_min,
        "budgetShadowPriceMax": shadow_price_max,
        "spendAccountingMode": normalize_spend_accounting_mode(
            first_present(raw_budget, "spendAccountingMode", "spend_accounting_mode"),
            status_code=409,
        ),
    }


def route_difficulty(route_schema: dict[str, Any]) -> float:
    difficulty = parse_required_number(route_schema.get("difficulty"), "route_schema.difficulty")
    return max(0.0, difficulty)


def predicted_output_tokens(route_schema: dict[str, Any], request_options: dict[str, Any]) -> float:
    expected_tokens = parse_optional_number(route_schema.get("expected_output_tokens"))
    expected_k_tokens = parse_optional_number(route_schema.get("expected_output_k_tokens"))
    prediction = (
        expected_tokens
        if expected_tokens is not None
        else expected_k_tokens * 1000.0
        if expected_k_tokens is not None
        else None
    )
    if prediction is not None:
        return max(1.0, caller_bounded_output_prediction(prediction, request_options))

    raise HTTPException(
        status_code=409,
        detail={
            "error": "length_prediction_required",
            "message": "Budget-aware scoring requires a model-predicted output length.",
            "field": "expected_output_tokens",
        },
    )


def estimate_request_weight(
    *,
    messages: list[dict[str, Any]],
    route_schema: dict[str, Any],
    request_options: dict[str, Any],
    median_weighted_tokens: float,
    output_token_weight: float,
    request_size_beta: float,
    request_difficulty_alpha: float,
    request_weight_min: float,
    request_weight_max: float,
) -> dict[str, float]:
    input_tokens = max(1.0, len(messages_text(messages)) / 4.0)
    output_tokens = predicted_output_tokens(route_schema, request_options)
    difficulty = route_difficulty(route_schema)
    weighted_tokens = input_tokens + (max(0.0, output_token_weight) * output_tokens)
    size_factor = weighted_tokens / median_weighted_tokens
    unclipped_request_weight = (max(0.0, size_factor) ** request_size_beta) * math.exp(
        request_difficulty_alpha * difficulty
    )
    request_weight = clamp(
        unclipped_request_weight,
        min(request_weight_min, request_weight_max),
        max(request_weight_min, request_weight_max),
    )
    return {
        "difficulty": difficulty,
        "request_difficulty_alpha": request_difficulty_alpha,
        "request_size_beta": request_size_beta,
        "output_token_weight": output_token_weight,
        "input_length_tokens": input_tokens,
        "output_length_prediction_tokens": output_tokens,
        "weighted_tokens": weighted_tokens,
        "median_weighted_tokens": median_weighted_tokens,
        "size_factor": size_factor,
        "unclipped_request_weight": unclipped_request_weight,
        "request_weight_min": request_weight_min,
        "request_weight_max": request_weight_max,
        "request_weight": request_weight,
    }


def expected_model_price_usd(
    *,
    model: dict[str, Any],
    input_tokens: float,
    output_tokens: float,
) -> float:
    input_price = max(0.0, as_float(model.get("input_price_per_million")))
    output_price = max(0.0, as_float(model.get("output_price_per_million")))
    return (input_tokens / 1_000_000.0 * input_price) + (output_tokens / 1_000_000.0 * output_price)


def calculate_adaptive_budget_malus(
    *,
    remaining_budget: float,
    remaining_weight: float,
    request_weight: float,
    expected_provider_price_usd: float,
    expected_budget_debit_usd: float,
    shadow_price: float,
) -> dict[str, Any]:
    planning_weight = max(remaining_weight, request_weight)
    budget_per_weight = remaining_budget / planning_weight
    request_budget = request_weight * budget_per_weight
    budget_utilization = expected_budget_debit_usd / max(request_budget, 1e-12)
    budget_malus = shadow_price * budget_utilization
    return {
        "budget_per_weight": budget_per_weight,
        "planning_weight": planning_weight,
        "request_budget_usd": request_budget,
        "expected_price_usd": expected_provider_price_usd,
        "expected_budget_debit_usd": expected_budget_debit_usd,
        "budget_utilization": budget_utilization,
        "budget_shadow_price": shadow_price,
        "budget_controller_version": BUDGET_CONTROLLER_VERSION,
        "budget_malus": budget_malus,
    }


def calculate_budget_controller_update(
    *,
    budget_state: dict[str, Any],
    budget_debit_usd: float,
    request_weight: float,
    allocated_request_weight: float | None = None,
) -> dict[str, Any]:
    remaining_budget = parse_required_number(
        first_present(budget_state, "budgetRemainingUsd", "remaining_budget_usd"),
        "budgetRemainingUsd",
    )
    remaining_weight = parse_required_number(
        first_present(budget_state, "remainingWeight", "remaining_weight"),
        "remainingWeight",
    )
    shadow_price = parse_required_number(
        first_present(budget_state, "budgetShadowPrice", "budget_shadow_price"),
        "budgetShadowPrice",
    )
    learning_rate = parse_required_number(
        first_present(budget_state, "budgetControllerLearningRate", "budget_controller_learning_rate"),
        "budgetControllerLearningRate",
    )
    shadow_price_min = parse_required_number(
        first_present(budget_state, "budgetShadowPriceMin", "budget_shadow_price_min"),
        "budgetShadowPriceMin",
    )
    shadow_price_max = parse_required_number(
        first_present(budget_state, "budgetShadowPriceMax", "budget_shadow_price_max"),
        "budgetShadowPriceMax",
    )
    allocation_weight = (
        request_weight
        if allocated_request_weight is None
        else allocated_request_weight
    )
    values = (
        remaining_budget,
        remaining_weight,
        shadow_price,
        learning_rate,
        shadow_price_min,
        shadow_price_max,
        budget_debit_usd,
        request_weight,
        allocation_weight,
    )
    if (
        not all(math.isfinite(value) for value in values)
        or remaining_budget < 0
        or remaining_weight < 0
        or budget_debit_usd < 0
        or request_weight <= 0
        or allocation_weight < 0
        or allocation_weight > request_weight
        or learning_rate < 0
        or shadow_price_min < 0
        or shadow_price_max < shadow_price_min
        or not shadow_price_min <= shadow_price <= shadow_price_max
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "invalid_budget_controller",
                "message": "Budget controller update requires finite, consistent state",
            },
        )

    planning_weight = max(remaining_weight, request_weight)
    allocated_spend_usd = allocation_weight * (remaining_budget / planning_weight)
    controller_error_usd = budget_debit_usd - allocated_spend_usd
    next_shadow_price = clamp(
        shadow_price + learning_rate * controller_error_usd,
        shadow_price_min,
        shadow_price_max,
    )
    return {
        "controller_version": BUDGET_CONTROLLER_VERSION,
        "previous_shadow_price": shadow_price,
        "learning_rate": learning_rate,
        "allocated_spend_usd": allocated_spend_usd,
        "allocated_request_weight": allocation_weight,
        "actual_spend_usd": budget_debit_usd,
        "controller_error_usd": controller_error_usd,
        "next_shadow_price": next_shadow_price,
    }


def raise_budget_required(route_id: str) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "error": "budget_required",
            "message": "A user budget must be configured before routing requests.",
            "route_id": route_id,
        },
    )


def load_user_budget(user_id: str, route_id: str) -> dict[str, float]:
    try:
        user_budgets = require_mongo_collection("LEROUTER_USER_BUDGET_COLLECTION", "user_budget_states")
        row = user_budgets.find_one(
            {"loginInfo.userId": user_id, "routeId": route_id},
            sort=[("updatedAt", -1)],
        )
    except Exception as error:
        print(f"LEROUTER_BUDGET_MONGO_LOAD_FAILED {error.__class__.__name__}: {error}", flush=True)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "budget_store_unavailable",
                "message": "LeRouter could not load the required user budget state.",
                "route_id": route_id,
            },
        ) from error

    if not row:
        raise_budget_required(route_id)

    return budget_from_row(row)


def budget_aware_scoring(
    *,
    user_id: str,
    route_id: str,
    messages: list[dict[str, Any]],
    route_schema: dict[str, Any],
    request_options: dict[str, Any],
    candidates: list[dict[str, Any]],
    switch_cost: dict[str, Any],
    budget_override: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if budget_override:
        budget = normalize_budget_state(budget_override, route_id)
    else:
        budget = normalize_budget_state(load_user_budget(user_id, route_id), route_id)

    scored = []
    penalties = switch_cost.get("penalties", {})
    output_token_weight = as_float(budget.get("outputTokenWeight"), REQUEST_OUTPUT_TOKEN_WEIGHT_K)
    request_size_beta = as_float(budget.get("requestWeightBeta"), REQUEST_SIZE_BETA)
    request_difficulty_alpha = as_float(
        budget.get("requestDifficultyAlpha"),
        as_float(budget.get("difficultyWeightAlpha"), REQUEST_DIFFICULTY_ALPHA),
    )
    request_weight_min = max(0.0, as_float(budget.get("requestWeightMin"), REQUEST_WEIGHT_MIN))
    request_weight_cap_multiplier = max(
        0.0,
        as_float(budget.get("requestWeightCapMultiplier"), REQUEST_WEIGHT_CAP_MULTIPLIER),
    )
    request_weight_max = (
        as_float(budget["totalPredictedWeight"])
        / as_float(budget["averageRequestsPerPeriod"])
        * request_weight_cap_multiplier
    )
    shadow_price = as_float(budget["budgetShadowPrice"])
    spend_accounting_mode = str(budget["spendAccountingMode"])
    request_weight_result = estimate_request_weight(
        messages=messages,
        route_schema=route_schema,
        request_options=request_options,
        median_weighted_tokens=as_float(budget["medianWeightedTokens"]),
        output_token_weight=output_token_weight,
        request_size_beta=request_size_beta,
        request_difficulty_alpha=request_difficulty_alpha,
        request_weight_min=request_weight_min,
        request_weight_max=request_weight_max,
    )
    request_weight = as_float(request_weight_result["request_weight"])
    for model in candidates:
        model_id = model["model_id"]
        base_score = as_float(model.get("biencoder_score"), 0.0)
        expected_price_usd = expected_model_price_usd(
            model=model,
            input_tokens=request_weight_result["input_length_tokens"],
            output_tokens=request_weight_result["output_length_prediction_tokens"],
        )
        expected_budget_debit_usd = (
            expected_price_usd
            if spend_accounting_mode == SPEND_ACCOUNTING_PROVIDER_SPEND
            else routing_fee_usd(expected_price_usd)
        )
        budget_result = {
            "remaining_budget_usd": budget["budgetRemainingUsd"],
            "remaining_weight": budget["remainingWeight"],
            "total_predicted_weight": budget.get("totalPredictedWeight"),
            "average_requests_per_period": budget.get("averageRequestsPerPeriod"),
            "request_weight_cap_multiplier": request_weight_cap_multiplier,
            "spend_accounting_mode": spend_accounting_mode,
            **request_weight_result,
            **calculate_adaptive_budget_malus(
                remaining_budget=budget["budgetRemainingUsd"],
                remaining_weight=budget["remainingWeight"],
                request_weight=request_weight_result["request_weight"],
                expected_provider_price_usd=expected_price_usd,
                expected_budget_debit_usd=expected_budget_debit_usd,
                shadow_price=shadow_price,
            ),
        }
        quality_utility = base_score
        final_grade = quality_utility - as_float(budget_result["budget_malus"])
        switch_cost_penalty = as_float(penalties.get(model_id))
        cache_bonus = 0.0
        final_score = final_grade - switch_cost_penalty
        enriched = dict(model)
        enriched["quality_utility"] = quality_utility
        enriched["budget_result"] = budget_result
        enriched["request_weight"] = as_float(budget_result["request_weight"])
        enriched["request_budget_usd"] = as_float(budget_result["request_budget_usd"])
        enriched["expected_price_usd"] = as_float(budget_result["expected_price_usd"])
        enriched["budget_malus"] = as_float(budget_result["budget_malus"])
        enriched["switch_cost_penalty"] = switch_cost_penalty
        enriched["cache_stickiness_bonus"] = cache_bonus
        switch_details = switch_cost.get("details", {}).get(model_id) if isinstance(switch_cost.get("details"), dict) else None
        if isinstance(switch_details, dict):
            enriched["cacheable_input_tokens"] = switch_details.get("cacheable_input_tokens")
            enriched["cached_input_price_difference_per_million"] = switch_details.get(
                "cached_input_price_difference_per_million"
            )
            enriched["cache_stickiness_bonus_multiplier"] = switch_details.get("cache_stickiness_bonus_multiplier")
            enriched["prompt_cache_loss_usd"] = switch_details.get("prompt_cache_loss_usd")
            enriched["continued_model_cache_savings_usd"] = switch_details.get(
                "continued_model_cache_savings_usd"
            )
            enriched["cache_pricing_available"] = switch_details.get("cache_pricing_available")
        enriched["final_score"] = final_score
        scored.append(enriched)

    scored.sort(key=lambda model: model["final_score"], reverse=True)
    return scored


def provider_from_model_id(model_id: str | None) -> str | None:
    if not model_id or "/" not in model_id:
        return None
    normalized = str(model_id).strip().lower()
    prefix, tail = normalized.split("/", 1)
    owner = prefix.lstrip("~")
    if normalized.endswith(":free") or prefix.startswith("~"):
        return "openrouter"
    if owner == "openrouter" and tail.startswith(("openai/", "openai-codex/", "gpt/")):
        if tail.split("/", 1)[-1].startswith("gpt-oss"):
            return "openrouter"
        return "openai"
    if owner == "openrouter":
        return "openrouter"
    if prefix in {"anthropic", "claude"}:
        return "anthropic"
    if prefix == "gemini" or (prefix == "google" and tail.startswith("gemini-")):
        return "gemini"
    if prefix in {"openai", "openai-codex", "gpt"}:
        if tail.startswith("gpt-oss"):
            return "openrouter"
        return "openai"
    if prefix == "together":
        return "together"
    return None


OPENROUTER_OWNER_EXECUTION_PROVIDERS = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "google": "gemini",
    "gemini": "gemini",
    "openai": "openai",
    "openai-codex": "openai",
    "gpt": "openai",
    "mistral": "mistral",
    "mistralai": "mistral",
    "deepseek": "deepseek",
    "x-ai": "xai",
    "xai": "xai",
    "groq": "groq",
    "together": "together",
}


def direct_provider_from_model_owner(model: dict[str, Any], model_id: str) -> str | None:
    owner = str(model.get("model_owner") or "").strip().lower().lstrip("~")
    if owner:
        direct_provider = OPENROUTER_OWNER_EXECUTION_PROVIDERS.get(owner)
        if direct_provider:
            return direct_provider

    identifiers = [
        model_id,
        str(model.get("provider_native_model_id") or ""),
        str(model.get("native_model_id") or ""),
    ]
    for identifier in identifiers:
        identifier = identifier.strip().lower()
        if "/" not in identifier:
            continue
        prefix, tail = identifier.split("/", 1)
        prefix = prefix.lstrip("~")
        candidate_owner = tail.split("/", 1)[0].lstrip("~") if prefix == "openrouter" and "/" in tail else prefix
        direct_provider = OPENROUTER_OWNER_EXECUTION_PROVIDERS.get(candidate_owner)
        if direct_provider:
            return direct_provider
    return None


def executable_provider_for_model(model: dict[str, Any]) -> str:
    model_id = str(model.get("model_id") or model.get("id") or model.get("model") or "")
    execution_provider = str(model.get("execution_provider") or "").strip().lower()
    if execution_provider:
        return execution_provider
    provider = str(model.get("provider") or "").strip().lower()
    if provider:
        return provider
    direct_provider = direct_provider_from_model_owner(model, model_id)
    if direct_provider:
        return direct_provider
    id_provider = provider_from_model_id(model_id)
    if id_provider == "openai":
        return "openai"
    if id_provider:
        return id_provider
    return "together" if model.get("is_open_source") else "unknown"


def provider_api_key(provider: str) -> str:
    provider = str(provider or "").strip().lower()
    env_names = {
        "together": ("TOGETHER_API_KEY", "TOGETHER_AI_API_KEY"),
        "openrouter": ("OPENROUTER_API_KEY",),
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY", "ANTROPIC_API_KEY"),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "mistral": ("MISTRAL_API_KEY",),
        "groq": ("GROQ_API_KEY",),
        "deepseek": ("DEEPSEEK_API_KEY",),
        "xai": ("XAI_API_KEY",),
    }
    return next(
        (
            str(os.environ.get(env_name) or "").strip()
            for env_name in env_names.get(provider, ())
            if str(os.environ.get(env_name) or "").strip()
        ),
        "",
    )


def openrouter_native_model_id(model: dict[str, Any]) -> str:
    explicit = str(
        model.get("openrouter_native_model_id")
        or model.get("openrouter_model_id")
        or ""
    ).strip()
    if explicit.lower().startswith("openrouter/"):
        explicit = explicit.split("/", 1)[1]
    if explicit:
        return explicit

    provider = executable_provider_for_model(model)
    if provider != "openrouter":
        return ""
    model_id = str(model.get("model_id") or model.get("id") or model.get("model") or "").strip()
    native_model_id = provider_native_model_id(model_id, "openrouter", model)
    if native_model_id.lower().startswith("openrouter/"):
        native_model_id = native_model_id.split("/", 1)[1]
    return native_model_id


def resolved_execution_provider(model: dict[str, Any]) -> tuple[str, str]:
    requested_provider = executable_provider_for_model(model)
    model_id = str(model.get("model_id") or model.get("id") or model.get("model") or "").strip()
    requested_native_model_id = provider_native_model_id(model_id, requested_provider, model)
    if provider_api_key(requested_provider):
        return requested_provider, requested_native_model_id

    openrouter_model_id = openrouter_native_model_id(model)
    if requested_provider != "openrouter" and provider_api_key("openrouter") and openrouter_model_id:
        return "openrouter", openrouter_model_id
    return requested_provider, requested_native_model_id


ANTHROPIC_MODEL_ALIASES = {
    # Legacy shorthand kept only so old route policies do not hard-fail.
    # New catalog entries should store provider-native Anthropic model IDs.
    "claude-sonnet-4": "claude-sonnet-4-6",
}


def provider_native_model_id(model_id: str, provider: str, model: dict[str, Any] | None = None) -> str:
    provider = str(provider or "").strip().lower()
    if model:
        explicit = str(model.get("provider_native_model_id") or model.get("native_model_id") or "").strip()
        if explicit:
            return explicit
    raw_model_id = str(model_id).strip()
    if provider == "openrouter":
        return raw_model_id.split("/", 1)[1] if raw_model_id.lower().startswith("openrouter/") else raw_model_id
    if provider == "nvidia":
        return raw_model_id
    if provider == "together":
        return raw_model_id.split("/", 1)[1] if raw_model_id.lower().startswith("together/") else raw_model_id
    if provider == "openai":
        lowered = raw_model_id.lower()
        if lowered.startswith("openrouter/openai/"):
            return raw_model_id.split("/", 2)[2]
        if lowered.startswith("openai/"):
            return raw_model_id.split("/", 1)[1]
    if provider in {"anthropic", "gemini", "mistral", "deepseek", "xai", "groq"} and raw_model_id.lower().startswith("openrouter/"):
        parts = raw_model_id.split("/", 2)
        if len(parts) == 3:
            raw_model_id = parts[2]
    native_model_id = raw_model_id.split("/", 1)[-1]
    if provider == "anthropic":
        return ANTHROPIC_MODEL_ALIASES.get(native_model_id, native_model_id)
    return native_model_id


def provider_max_output_tokens(provider: str, native_model_id: str) -> int | None:
    provider = str(provider or "").strip().lower()
    if provider == "anthropic":
        return 64000
    return None


def provider_bound_request_options(
    request_options: dict[str, Any],
    *,
    provider: str,
    native_model_id: str,
) -> dict[str, Any]:
    options = dict(request_options)
    normalized_native = str(native_model_id or "").strip().lower()
    if provider == "openai" and normalized_native.startswith(("gpt-5", "o1", "o3", "o4")):
        if options.get("max_tokens") is not None:
            options["max_completion_tokens"] = options.pop("max_tokens")
        options.pop("temperature", None)
    max_output_tokens = provider_max_output_tokens(provider, native_model_id)
    if max_output_tokens is None or options.get("max_tokens") is None:
        return options
    requested = parse_optional_number(options.get("max_tokens"))
    if requested is None:
        return options
    options["max_tokens"] = min(int(requested), max_output_tokens)
    return options


def post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout_seconds: int | None = 90,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    safe_headers = {
        "Accept": "application/json",
        "User-Agent": "LeRouter/1.0 (+https://lerouter.ai)",
        **headers,
    }
    request = urllib.request.Request(url, data=data, headers=safe_headers, method="POST")
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else None
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=context) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise HTTPException(status_code=error.code, detail=body) from error
    except urllib.error.URLError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def shared_http_client():
    if httpx is None:
        raise HTTPException(status_code=500, detail="httpx is required for pooled routing HTTP calls")
    loop = asyncio.get_running_loop()
    for owner_loop, existing_client in list(HTTP_CLIENTS_BY_LOOP.items()):
        if owner_loop.is_closed() or existing_client.is_closed:
            HTTP_CLIENTS_BY_LOOP.pop(owner_loop, None)
    client = HTTP_CLIENTS_BY_LOOP.get(loop)
    if client is None or client.is_closed:
        verify: str | bool = certifi.where() if certifi else True
        client = httpx.AsyncClient(
            verify=verify,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            headers={"User-Agent": "LeRouter/1.0 (+https://lerouter.ai)"},
        )
        HTTP_CLIENTS_BY_LOOP[loop] = client
    return client


async def post_json_async(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    timeout_seconds: int | None = 90,
    follow_redirects: bool = False,
) -> dict[str, Any]:
    client = shared_http_client()
    safe_headers = {"Accept": "application/json", **headers}
    try:
        response = await client.post(
            url,
            headers=safe_headers,
            json=payload,
            timeout=timeout_seconds,
            follow_redirects=follow_redirects,
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        raise HTTPException(status_code=error.response.status_code, detail=error.response.text) from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def openai_compatible_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compatible = []
    for message in messages:
        item = {
            "role": message.get("role") or "user",
            "content": message.get("content") if message.get("content") is not None else "",
        }
        for key in ("tool_calls", "tool_call_id", "name"):
            if message.get(key) is not None:
                item[key] = message[key]
        compatible.append(item)
    return compatible


def text_only_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    converted = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message_content_text(message.get("content"))
        if message.get("tool_calls"):
            content = f"{content}\ntool_calls={json.dumps(message.get('tool_calls'), ensure_ascii=True)}".strip()
        if content:
            converted.append(
                {
                    "role": role if role in {"system", "user", "assistant"} else "user",
                    "content": content,
                }
            )
    return converted or [{"role": "user", "content": messages_text(messages)}]


def openai_tools_to_anthropic_tools(tools: Any) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        return []
    converted = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(function, dict) or not function.get("name"):
            continue
        converted.append(
            {
                "name": str(function["name"]),
                "description": str(function.get("description") or ""),
                "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return converted


def openai_tool_choice_to_anthropic(tool_choice: Any) -> dict[str, Any] | None:
    if tool_choice in (None, "none"):
        return None
    if tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "required":
        return {"type": "any"}
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function")
        if isinstance(function, dict) and function.get("name"):
            return {"type": "tool", "name": str(function["name"])}
    return None


def compact_http_error_detail(error: HTTPException) -> str:
    detail = error.detail
    if isinstance(detail, dict):
        code = detail.get("error")
        attempts = detail.get("attempts")
        suffix = f" ({code})" if code else ""
        if isinstance(attempts, list) and attempts:
            last = attempts[-1] if isinstance(attempts[-1], dict) else {}
            model_id = last.get("model_id") or "unknown_model"
            provider = last.get("provider") or "unknown_provider"
            status = last.get("status") or "unknown_status"
            return f"{status} from {provider}/{model_id}{suffix}"
        return str(code or detail)[:240]
    return str(detail)[:240]


def anthropic_content_blocks_from_openai(message: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = []
    text = message_content_text(message.get("content"))
    if text:
        blocks.append({"type": "text", "text": text})
    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function") or {}
        if not isinstance(function, dict) or not function.get("name"):
            continue
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError:
            arguments = {"arguments": raw_arguments}
        blocks.append(
            {
                "type": "tool_use",
                "id": str(tool_call.get("id") or f"toolu_{len(blocks)}"),
                "name": str(function["name"]),
                "input": arguments if isinstance(arguments, dict) else {"value": arguments},
            }
        )
    return blocks or [{"type": "text", "text": ""}]


def anthropic_messages_from_openai(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    system_messages = []
    converted = []
    for message in messages:
        role = str(message.get("role") or "user")
        if role == "system":
            text = message_content_text(message.get("content"))
            if text:
                system_messages.append(text)
            continue
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(message.get("tool_call_id") or ""),
                            "content": message_content_text(message.get("content")),
                        }
                    ],
                }
            )
            continue
        converted.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": anthropic_content_blocks_from_openai(message),
            }
        )
    return ("\n".join(system_messages) if system_messages else None, converted)


async def execute_provider_request(
    *,
    model: dict[str, Any],
    messages: list[dict[str, str]],
    request_options: dict[str, Any],
) -> dict[str, Any]:
    model_id = model["model_id"]
    requested_provider = executable_provider_for_model(model)
    provider, native_model_id = resolved_execution_provider(model)
    provider_options = provider_bound_request_options(
        request_options,
        provider=provider,
        native_model_id=native_model_id,
    )

    if model.get("executable") is False:
        return {"status": "unsupported_provider", "provider": provider, "reason": "model_profile_not_executable"}

    if provider == "openrouter":
        api_key = provider_api_key("openrouter")
        if not api_key:
            return {"status": "skipped_missing_api_key", "provider": "openrouter"}
        response = post_json(
            "https://openrouter.ai/api/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            {
                "model": native_model_id,
                "messages": openai_compatible_messages(messages),
                **provider_options,
            },
        )
        if isinstance(response, dict):
            response["_lerouter_provider"] = "openrouter"
            response["_lerouter_requested_provider"] = requested_provider
        return response

    if provider == "together":
        api_key = provider_api_key("together")
        if not api_key:
            return {"status": "skipped_missing_api_key", "provider": "together"}
        return post_json(
            "https://api.together.xyz/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            {
                "model": native_model_id,
                "messages": openai_compatible_messages(messages),
                **provider_options,
            },
        )

    if provider == "openai":
        api_key = provider_api_key("openai")
        if not api_key:
            return {"status": "skipped_missing_api_key", "provider": "openai"}
        return post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            {
                "model": native_model_id,
                "messages": openai_compatible_messages(messages),
                **provider_options,
            },
        )

    if provider == "anthropic":
        api_key = provider_api_key("anthropic")
        if not api_key:
            return {"status": "skipped_missing_api_key", "provider": "anthropic"}
        system_message, anthropic_messages = anthropic_messages_from_openai(messages)
        payload = {
            "model": native_model_id,
            "messages": anthropic_messages,
            "system": system_message,
            "max_tokens": provider_options.get("max_tokens", 1024),
        }
        anthropic_tools = openai_tools_to_anthropic_tools(provider_options.get("tools"))
        if anthropic_tools:
            payload["tools"] = anthropic_tools
        anthropic_tool_choice = openai_tool_choice_to_anthropic(provider_options.get("tool_choice"))
        if anthropic_tool_choice:
            payload["tool_choice"] = anthropic_tool_choice
        return post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            {key: value for key, value in payload.items() if value is not None},
        )

    if provider == "gemini":
        api_key = provider_api_key("gemini")
        if not api_key:
            return {"status": "skipped_missing_api_key", "provider": "gemini"}
        text = messages_text(messages)
        model_name = native_model_id
        return post_json(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}",
            {"Content-Type": "application/json"},
            {"contents": [{"parts": [{"text": text}]}]},
        )

    openai_compatible_providers = {
        "mistral": {
            "api_key": os.environ.get("MISTRAL_API_KEY"),
            "url": "https://api.mistral.ai/v1/chat/completions",
        },
        "groq": {
            "api_key": os.environ.get("GROQ_API_KEY"),
            "url": "https://api.groq.com/openai/v1/chat/completions",
        },
        "deepseek": {
            "api_key": os.environ.get("DEEPSEEK_API_KEY"),
            "url": "https://api.deepseek.com/v1/chat/completions",
        },
        "xai": {
            "api_key": os.environ.get("XAI_API_KEY"),
            "url": "https://api.x.ai/v1/chat/completions",
        },
    }
    if provider in openai_compatible_providers:
        config = openai_compatible_providers[provider]
        api_key = config["api_key"]
        if not api_key:
            return {"status": "skipped_missing_api_key", "provider": provider}
        return post_json(
            config["url"],
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            {
                "model": native_model_id,
                "messages": openai_compatible_messages(messages),
                **provider_options,
            },
        )

    return {"status": "unsupported_provider", "provider": provider}


def provider_response_succeeded(response: dict[str, Any]) -> bool:
    status = response.get("status")
    if status in {
        "skipped_missing_api_key",
        "unsupported_provider",
        "provider_error",
        "not_executed",
    }:
        return False
    return bool(response)


def estimated_request_context_input_tokens(
    messages: list[dict[str, Any]],
    request_options: dict[str, Any],
) -> float:
    context_payload: dict[str, Any] = {"messages": messages}
    for key in ("tools", "tool_choice", "response_format"):
        if request_options.get(key) is not None:
            context_payload[key] = request_options[key]
    serialized = json.dumps(
        context_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return max(1.0, len(serialized) / 4.0)


def request_compatible_candidates(
    candidates: list[dict[str, Any]],
    request_options: dict[str, Any],
    *,
    estimated_input_tokens: float,
    predicted_output_token_count: float,
) -> list[dict[str, Any]]:
    requires_tools = bool(request_options.get("tools"))
    response_format = request_options.get("response_format")
    requires_json = (
        isinstance(response_format, dict)
        and str(response_format.get("type") or "").strip().lower() in {"json_object", "json_schema"}
    )
    input_tokens = finite_profile_number(estimated_input_tokens)
    output_tokens = finite_profile_number(predicted_output_token_count)
    if input_tokens is None or input_tokens < 0 or output_tokens is None or output_tokens < 0:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "invalid_context_compatibility_estimate",
                "message": "Context compatibility requires finite non-negative input and output token estimates",
            },
        )
    required_context_tokens = input_tokens + output_tokens

    return [
        candidate
        for candidate in candidates
        if (not requires_tools or candidate.get("supports_tools") is True)
        and (not requires_json or candidate.get("supports_json") is True)
        and (
            (context_window := finite_profile_number(
                first_present(candidate, "context_window", "model_context_window"),
                positive=True,
            ))
            is not None
            and required_context_tokens <= context_window
        )
    ]


def normalized_response_succeeded(response: dict[str, Any], raw_response: dict[str, Any]) -> bool:
    if not provider_response_succeeded(raw_response):
        return False
    return bool(str(response.get("content") or "").strip() or response.get("tool_calls"))


def anthropic_tool_use_to_openai_tool_calls(blocks: Any) -> list[dict[str, Any]]:
    tool_calls = []
    if not isinstance(blocks, list):
        return tool_calls
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_calls.append(
            {
                "id": str(block.get("id") or f"call_{index}"),
                "type": "function",
                "function": {
                    "name": str(block.get("name") or ""),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=True),
                },
            }
        )
    return tool_calls


def openai_finish_reason(provider: str, finish_reason: Any) -> str | None:
    if finish_reason is None:
        return None
    reason = str(finish_reason)
    if provider == "anthropic":
        return {
            "end_turn": "stop",
            "max_tokens": "length",
            "tool_use": "tool_calls",
            "stop_sequence": "stop",
        }.get(reason, reason)
    if provider == "gemini":
        return {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
        }.get(reason, reason.lower())
    return reason


def normalize_provider_response(
    *,
    model: dict[str, Any],
    raw_response: dict[str, Any],
) -> dict[str, Any]:
    provider = executable_provider_for_model(model)
    if isinstance(raw_response, dict) and raw_response.get("_lerouter_provider"):
        provider = str(raw_response["_lerouter_provider"])
    content = ""
    tool_calls = []
    finish_reason = None
    usage = raw_response.get("usage") if isinstance(raw_response, dict) else None

    choices = raw_response.get("choices") if isinstance(raw_response, dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0] or {}
        message = first.get("message") or {}
        content = message.get("content") or first.get("text") or ""
        tool_calls = message.get("tool_calls") or []
        finish_reason = first.get("finish_reason")

    if not content and provider == "anthropic":
        blocks = raw_response.get("content") if isinstance(raw_response, dict) else []
        if isinstance(blocks, list):
            content = "".join(
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
            tool_calls = anthropic_tool_use_to_openai_tool_calls(blocks)
        finish_reason = raw_response.get("stop_reason")

    if not content and provider == "gemini":
        candidates = raw_response.get("candidates") if isinstance(raw_response, dict) else []
        if isinstance(candidates, list) and candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            content = "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
            finish_reason = candidates[0].get("finishReason")
        usage = raw_response.get("usageMetadata") or usage

    return {
        "content": content,
        "tool_calls": tool_calls,
        "model": model.get("model_id"),
        "provider": provider,
        "usage": usage or {},
        "finish_reason": openai_finish_reason(provider, finish_reason),
        "raw": raw_response,
    }


async def execute_selected_candidate(
    *,
    scored_candidates: list[dict[str, Any]],
    messages: list[dict[str, str]],
    request_options: dict[str, Any],
    execute: bool,
) -> dict[str, Any]:
    if not scored_candidates:
        raise HTTPException(status_code=404, detail="No candidate models available for this route.")

    selected_model = scored_candidates[0]
    attempts: list[dict[str, Any]] = []

    if not execute:
        return {
            "model": selected_model,
            "raw_response": {"status": "not_executed"},
            "normalized_response": normalize_provider_response(
                model=selected_model,
                raw_response={"status": "not_executed"},
            ),
            "attempts": attempts,
        }

    if request_options.get("stream"):
        raise HTTPException(
            status_code=400,
            detail="stream=true is not supported by LeRouter yet. Send stream=false for Hermes integration.",
        )

    try:
        raw_response = await execute_provider_request(
            model=selected_model,
            messages=messages,
            request_options=request_options,
        )
    except HTTPException as error:
        resolved_provider, _ = resolved_execution_provider(selected_model)
        attempt = {
            "model_id": selected_model.get("model_id"),
            "provider": resolved_provider,
            "status": "provider_error",
            "ok": False,
            "error": error.detail,
        }
        raise HTTPException(
            status_code=502,
            detail={
                "error": "selected_model_execution_failed",
                "message": "The selected model failed; LeRouter did not attempt another candidate",
                "selected_model_id": selected_model.get("model_id"),
                "attempts": [attempt],
            },
        ) from error

    normalized = normalize_provider_response(model=selected_model, raw_response=raw_response)
    ok = normalized_response_succeeded(normalized, raw_response)
    attempt = {
        "model_id": selected_model.get("model_id"),
        "provider": normalized.get("provider") or selected_model.get("provider"),
        "status": raw_response.get("status", "executed" if ok else "empty_response"),
        "ok": ok,
    }
    attempts.append(attempt)
    if not ok:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "selected_model_execution_failed",
                "message": "The selected model returned no usable response; LeRouter did not attempt another candidate",
                "selected_model_id": selected_model.get("model_id"),
                "attempts": attempts,
            },
        )

    return {
        "model": selected_model,
        "raw_response": raw_response,
        "normalized_response": normalized,
        "attempts": attempts,
    }


def estimate_spend_usd(model: dict[str, Any], messages: list[dict[str, str]], response: dict[str, Any]) -> float:
    prompt_chars = len(messages_text(messages))
    estimated_input_tokens = max(1, prompt_chars / 4)
    estimated_output_tokens = 0

    usage = response.get("usage") if isinstance(response, dict) else None
    if isinstance(usage, dict):
        estimated_input_tokens = as_float(
            usage.get("prompt_tokens") or usage.get("input_tokens"),
            estimated_input_tokens,
        )
        estimated_output_tokens = as_float(
            usage.get("completion_tokens") or usage.get("output_tokens"),
            estimated_output_tokens,
        )

    input_cost = estimated_input_tokens / 1_000_000 * as_float(model.get("input_price_per_million"))
    output_cost = estimated_output_tokens / 1_000_000 * as_float(model.get("output_price_per_million"))
    return round(input_cost + output_cost, 8)


def mongo_counter_key(value: str, field: str) -> str:
    key = str(value or "").strip()
    if not key or "." in key or key.startswith("$"):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_counter_key", "field": field},
        )
    return key


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if any(marker in normalized_key for marker in ("authorization", "api_key", "secret", "token", "password")):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if not isinstance(value, str):
        return value
    sensitive_key = r"(?:authorization|api[_-]?key|secret|token|password)"
    value = re.sub(
        rf"(?i)(['\"]?{sensitive_key}['\"]?\s*[:=]\s*)(['\"])(.*?)\2",
        rf"\1\2<redacted>\2",
        value,
    )
    value = re.sub(
        rf"(?i)(\b{sensitive_key}\b\s*[:=]\s*)(?!['\"])[^,\s}}\]]+",
        rf"\1<redacted>",
        value,
    )
    text = re.sub(r'(?i)\bBearer\s+[^\s"\']+', 'Bearer <redacted>', value)
    text = re.sub(
        r'(?i)\b(?:sk-|lr_live_|tgp_)[A-Za-z0-9._~+/=-]+',
        '<redacted>',
        text,
    )
    secret_values = {
        str(secret).strip()
        for secret in (
            AGENT_TOKEN,
            os.environ.get("LEROUTER_INTERNAL_SERVICE_TOKEN"),
            os.environ.get("MONGODB_URI"),
            os.environ.get("OPENAI_API_KEY"),
            os.environ.get("ANTHROPIC_API_KEY"),
            os.environ.get("TOGETHER_API_KEY"),
            os.environ.get("TOGETHER_AI_API_KEY"),
            os.environ.get("OPENROUTER_API_KEY"),
        )
        if secret and len(str(secret).strip()) >= 6
    }
    for secret in sorted(secret_values, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return text


def usage_model_profile(model: dict[str, Any], provider: str) -> dict[str, Any]:
    keys = (
        "model_id",
        "provider_native_model_id",
        "native_model_id",
        "profile_model",
        "profile_alias",
        "profile_hydrated",
        "strengths",
        "weaknesses",
        "context_k_tokens",
        "latency_ms",
        "model_size",
        "input_price_per_million",
        "output_price_per_million",
        "input_cache_read_usd_per_million",
        "input_cache_write_usd_per_million",
        "supports_tools",
        "supports_json",
        "supports_reasoning_effort",
    )
    profile = {key: model[key] for key in keys if model.get(key) is not None}
    for cache_key, field_name in (
        ("read", "input_cache_read_usd_per_million"),
        ("write", "input_cache_write_usd_per_million"),
    ):
        cache_price = model_cache_price_per_million(model, cache_key)
        if cache_price > 0:
            profile[field_name] = cache_price
    profile["provider"] = provider
    return profile


def usage_token_count(usage: dict[str, Any], aliases: tuple[str, ...], field: str) -> float:
    supplied: list[float] = []
    for alias in aliases:
        if alias not in usage or usage[alias] is None:
            continue
        value = finite_profile_number(usage[alias])
        if value is None or value < 0:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_usage_token_count", "field": alias},
            )
        supplied.append(value)
    if not supplied:
        raise HTTPException(
            status_code=422,
            detail={"error": "usage_token_count_required", "field": field},
        )
    if any(not math.isclose(value, supplied[0], rel_tol=0.0, abs_tol=1e-12) for value in supplied[1:]):
        raise HTTPException(
            status_code=422,
            detail={"error": "conflicting_usage_token_counts", "field": field},
        )
    return supplied[0]


def verified_usage_spend_usd(
    *,
    model_profile: dict[str, Any],
    metadata: dict[str, Any],
    success: bool,
    supplied_spend_usd: float,
) -> float:
    usage = metadata.get("usage")
    if not isinstance(usage, dict) or not usage:
        if success:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "usage_token_counts_required",
                    "message": "Successful usage accounting requires explicit input and output token counts",
                },
            )
        input_tokens = 0.0
        output_tokens = 0.0
    else:
        input_tokens = usage_token_count(
            usage,
            ("prompt_tokens", "input_tokens", "promptTokens", "inputTokens"),
            "input_tokens",
        )
        output_tokens = usage_token_count(
            usage,
            ("completion_tokens", "output_tokens", "completionTokens", "outputTokens"),
            "output_tokens",
        )

    input_price = finite_profile_number(model_profile.get("input_price_per_million"))
    output_price = finite_profile_number(model_profile.get("output_price_per_million"))
    if input_price is None or input_price < 0 or output_price is None or output_price < 0:
        raise HTTPException(
            status_code=502,
            detail={"error": "signed_model_prices_invalid"},
        )
    verified_spend = round(
        input_tokens / 1_000_000.0 * input_price
        + output_tokens / 1_000_000.0 * output_price,
        8,
    )
    supplied_spend = finite_profile_number(supplied_spend_usd)
    if supplied_spend is None or supplied_spend < 0 or round(supplied_spend, 8) != verified_spend:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "usage_spend_mismatch",
                "verified_spend_usd": verified_spend,
            },
        )
    return verified_spend


def increment_object_field(expression: Any, field: str) -> dict[str, Any]:
    source = {"$ifNull": [expression, {}]}
    return {
        "$setField": {
            "field": {"$literal": field},
            "input": source,
            "value": {
                "$add": [
                    {
                        "$ifNull": [
                            {"$getField": {"field": {"$literal": field}, "input": source}},
                            0,
                        ]
                    },
                    1,
                ]
            },
        }
    }


def usage_budget_update_pipeline(
    *,
    budget_debit_usd: float,
    request_weight: float,
    request_weight_debit: float,
    controller_update: dict[str, Any],
    provider: str,
    model_lab: str | None,
    success: bool,
    now: datetime,
) -> list[dict[str, Any]]:
    provider_completions = increment_object_field("$providerCompletions", provider)
    lab_completions = (
        increment_object_field("$labCompletions", model_lab)
        if model_lab
        else {"$ifNull": ["$labCompletions", {}]}
    )
    return [
        {
            "$set": {
                "budgetRemainingUsd": {
                    "$subtract": ["$budgetRemainingUsd", budget_debit_usd]
                },
                "remainingWeight": {
                    "$max": [
                        0.0,
                        {"$subtract": ["$remainingWeight", request_weight_debit]},
                    ]
                },
                "budgetShadowPrice": controller_update["next_shadow_price"],
                "budgetControllerVersion": controller_update["controller_version"],
                "lastBudgetControllerAllocatedSpendUsd": controller_update["allocated_spend_usd"],
                "lastBudgetControllerActualSpendUsd": controller_update["actual_spend_usd"],
                "lastBudgetControllerErrorUsd": controller_update["controller_error_usd"],
                "budgetControllerUpdates": {
                    "$add": [{"$ifNull": ["$budgetControllerUpdates", 0]}, 1]
                },
                "totalRequests": {"$add": [{"$ifNull": ["$totalRequests", 0]}, 1]},
                "totalSpendUsd": {
                    "$add": [{"$ifNull": ["$totalSpendUsd", 0.0]}, budget_debit_usd]
                },
                "successfulRequests": {
                    "$add": [{"$ifNull": ["$successfulRequests", 0]}, 1 if success else 0]
                },
                "failedRequests": {
                    "$add": [{"$ifNull": ["$failedRequests", 0]}, 0 if success else 1]
                },
                "providerCompletions": provider_completions,
                "labCompletions": lab_completions,
                "timeSpendDays": {"$max": [{"$ifNull": ["$timeSpendDays", 0]}, 1]},
                "updatedAt": now,
            }
        },
        {
            "$set": {
                "successRate": {
                    "$cond": [
                        {"$gt": ["$totalRequests", 0]},
                        {"$divide": ["$successfulRequests", "$totalRequests"]},
                        0.0,
                    ]
                }
            }
        },
    ]


def ensure_usage_log_index(usage_logs: Any) -> None:
    global USAGE_LOG_INDEX_READY
    if USAGE_LOG_INDEX_READY:
        return
    usage_logs.create_index(
        [("userId", 1), ("routeId", 1), ("routingCallId", 1)],
        unique=True,
        name="unique_route_routing_call",
        partialFilterExpression={"routingCallId": {"$type": "string"}},
    )
    usage_logs.create_index(
        [("userId", 1), ("routeId", 1), ("sessionId", 1), ("createdAt", -1)],
        name="route_session_history",
        partialFilterExpression={"sessionId": {"$type": "string"}},
    )
    USAGE_LOG_INDEX_READY = True


def mongo_error_code(error: BaseException) -> int | None:
    code = getattr(error, "code", None)
    if code is None:
        details = getattr(error, "details", None)
        code = details.get("code") if isinstance(details, dict) else None
    if code is None or isinstance(code, bool):
        return None
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def is_retryable_usage_log_transaction_error(error: BaseException) -> bool:
    if not isinstance(error, PyMongoError):
        return False
    has_error_label = getattr(error, "has_error_label", None)
    if callable(has_error_label) and has_error_label("TransientTransactionError"):
        return True
    return mongo_error_code(error) in MONGO_WRITE_CONFLICT_CODES


def usage_log_transaction_retry_delay(attempt: int) -> float:
    if attempt <= 0:
        raise ValueError("usage log transaction attempt must be positive")
    base_delay = max(0.0, float(USAGE_LOG_TRANSACTION_RETRY_DELAY_SECONDS))
    if base_delay == 0:
        return 0.0
    exponential_delay = min(
        float(USAGE_LOG_TRANSACTION_RETRY_MAX_DELAY_SECONDS),
        base_delay * (2 ** (attempt - 1)),
    )
    jitter = random.uniform(
        0.0,
        min(exponential_delay, float(USAGE_LOG_TRANSACTION_RETRY_MAX_JITTER_SECONDS)),
    )
    return exponential_delay + jitter


def require_idempotent_usage_match(
    existing: dict[str, Any],
    *,
    route_name: str,
    model_id: str,
    provider: str,
    success: bool,
    provider_spend_usd: float,
    routing_fee_usd: float,
    spend_accounting_mode: str,
    accounted_spend_usd: float,
    request_weight: float,
    request_weight_debit: float,
    update_counters: bool,
) -> dict[str, Any]:
    mismatches: list[str] = []
    for field, expected in (
        ("routeName", route_name),
        ("modelId", model_id),
        ("provider", provider),
        ("success", success),
        ("accountingCountersUpdated", update_counters),
    ):
        if existing.get(field) != expected:
            mismatches.append(field)
    existing_routing_fee = finite_profile_number(existing.get("spendUsd"))
    if existing_routing_fee is None or not math.isclose(
        existing_routing_fee,
        routing_fee_usd,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        mismatches.append("spendUsd")
    existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
    existing_billing = (
        existing_metadata.get("billing")
        if isinstance(existing_metadata.get("billing"), dict)
        else {}
    )
    existing_provider_spend = finite_profile_number(
        first_present(existing, "providerSpendUsd")
        if existing.get("providerSpendUsd") is not None
        else existing_billing.get("finalRequestSpendUsd")
    )
    if existing_provider_spend is None or not math.isclose(
        existing_provider_spend,
        provider_spend_usd,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        mismatches.append("providerSpendUsd")
    if existing.get("spendAccountingMode") != spend_accounting_mode:
        mismatches.append("spendAccountingMode")
    existing_accounted_spend = finite_profile_number(existing.get("accountedSpendUsd"))
    if existing_accounted_spend is None or not math.isclose(
        existing_accounted_spend,
        accounted_spend_usd,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        mismatches.append("accountedSpendUsd")
    expected_weight = request_weight if update_counters else None
    expected_weight_debit = request_weight_debit if update_counters else None
    existing_weight = parse_optional_number(existing.get("requestWeight"))
    existing_weight_debit = parse_optional_number(existing.get("requestWeightDebit"))
    if expected_weight is None:
        if existing.get("requestWeight") is not None:
            mismatches.append("requestWeight")
    elif existing_weight is None or not math.isclose(
        existing_weight,
        expected_weight,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        mismatches.append("requestWeight")
    if expected_weight_debit is None:
        if existing.get("requestWeightDebit") is not None:
            mismatches.append("requestWeightDebit")
    elif existing_weight_debit is None or not math.isclose(
        existing_weight_debit,
        expected_weight_debit,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        mismatches.append("requestWeightDebit")
    if mismatches:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "routing_call_id_conflict",
                "message": "routing_call_id was already used with different accounting facts",
                "mismatched_fields": sorted(set(mismatches)),
            },
        )
    return existing


def write_usage_log(
    *,
    user_id: str,
    route_id: str,
    route_name: str,
    model: dict[str, Any],
    provider: str,
    success: bool,
    spend_usd: float,
    metadata: dict[str, Any],
    update_counters: bool = True,
    request_weight_debit: float | None = None,
) -> dict[str, Any]:
    write_started = time.perf_counter()
    mongo_stage_timings_ms: dict[str, float] = {}

    def timed_mongo(name: str, operation: Any) -> Any:
        operation_started = time.perf_counter()
        result = operation()
        elapsed = (time.perf_counter() - operation_started) * 1000
        mongo_stage_timings_ms[name] = round(
            mongo_stage_timings_ms.get(name, 0.0) + elapsed,
            2,
        )
        return result

    now = datetime.now(timezone.utc)
    model_id = str(model.get("model_id") or "").strip()
    if not model_id:
        raise HTTPException(status_code=422, detail="usage log requires model_id")
    provider = mongo_counter_key(provider, "provider")
    model_lab = model_company_from_id(model_id)
    if model_lab:
        model_lab = mongo_counter_key(model_lab, "model_lab")
    routing_call_id = str(metadata.get("routing_call_id") or "").strip()
    if not routing_call_id:
        raise HTTPException(status_code=422, detail="usage log requires routing_call_id")
    request_weight = as_float(model.get("request_weight"), -1.0)
    if update_counters and request_weight <= 0:
        raise HTTPException(status_code=422, detail="usage log requires positive request_weight")
    if request_weight_debit is None:
        request_weight_debit = request_weight
    request_weight_debit = as_float(request_weight_debit, -1.0)
    if update_counters and (
        request_weight_debit < 0 or request_weight_debit > request_weight
    ):
        raise HTTPException(
            status_code=422,
            detail="usage log requires request_weight_debit between zero and request_weight",
        )

    final_request_spend_usd = finite_profile_number(spend_usd)
    if final_request_spend_usd is None or final_request_spend_usd < 0:
        raise HTTPException(status_code=422, detail="usage log requires finite nonnegative spend_usd")
    routing_fee_spend_usd = routing_fee_usd(final_request_spend_usd)
    metadata = {
        **metadata,
        "modelLab": metadata.get("modelLab") or model_lab,
        "request_weight": request_weight if update_counters else None,
        "billing": {
            **(metadata.get("billing") if isinstance(metadata.get("billing"), dict) else {}),
            "finalRequestSpendUsd": final_request_spend_usd,
            "routingFeeUsd": routing_fee_spend_usd,
            "routingFeeRate": ROUTING_FEE_RATE,
        },
    }

    database = mongo_database()
    if database is None or ReturnDocument is None:
        raise HTTPException(status_code=503, detail="Mongo transaction support is required for usage logging")
    user_budgets = require_mongo_collection("LEROUTER_USER_BUDGET_COLLECTION", "user_budget_states")
    users = require_mongo_collection("LEROUTER_USER_COLLECTION", "user")
    usage_logs = require_mongo_collection("LEROUTER_ROUTE_USAGE_COLLECTION", "route_usage_logs")
    ensure_usage_log_index(usage_logs)
    idempotency_query = {
        "userId": user_id,
        "routeId": route_id,
        "routingCallId": routing_call_id,
    }
    budget_identity_query = {
        "loginInfo.userId": user_id,
        "routeId": route_id,
    }

    def budget_accounting_facts(session: Any = None) -> tuple[dict[str, Any], str, float]:
        budget_state = timed_mongo(
            "budget_state_load",
            lambda: user_budgets.find_one(budget_identity_query, session=session),
        )
        if not budget_state:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "route_budget_state_missing",
                    "route_id": route_id,
                    "message": "Exact user+route budget state is required for usage logging",
                },
            )
        spend_accounting_mode = normalize_spend_accounting_mode(
            first_present(budget_state, "spendAccountingMode", "spend_accounting_mode"),
            status_code=409,
        )
        budget_debit_usd = (
            final_request_spend_usd
            if spend_accounting_mode == SPEND_ACCOUNTING_PROVIDER_SPEND
            else routing_fee_spend_usd
        )
        return budget_state, spend_accounting_mode, budget_debit_usd

    def persist(session: Any) -> dict[str, Any]:
        budget_state, spend_accounting_mode, budget_debit_usd = budget_accounting_facts(session)
        existing = timed_mongo(
            "idempotency_load",
            lambda: usage_logs.find_one(idempotency_query, session=session),
        )
        if existing:
            return require_idempotent_usage_match(
                existing,
                route_name=route_name,
                model_id=model_id,
                provider=provider,
                success=success,
                provider_spend_usd=final_request_spend_usd,
                routing_fee_usd=routing_fee_spend_usd,
                spend_accounting_mode=spend_accounting_mode,
                accounted_spend_usd=budget_debit_usd,
                request_weight=request_weight,
                request_weight_debit=request_weight_debit,
                update_counters=update_counters,
            )
        controller_update = (
            calculate_budget_controller_update(
                budget_state=budget_state,
                budget_debit_usd=budget_debit_usd,
                request_weight=request_weight,
                allocated_request_weight=request_weight_debit,
            )
            if update_counters
            else None
        )
        budget_query = {
            **budget_identity_query,
            "budgetRemainingUsd": {
                "$type": "number",
                **({"$gte": budget_debit_usd} if update_counters else {}),
            },
            "remainingWeight": {
                "$type": "number",
            },
        }
        if update_counters:
            if controller_update is None:
                raise AssertionError("Budget controller update is required when counters are updated")
            budget = timed_mongo(
                "budget_update",
                lambda: user_budgets.find_one_and_update(
                    budget_query,
                    usage_budget_update_pipeline(
                        budget_debit_usd=budget_debit_usd,
                        request_weight=request_weight,
                        request_weight_debit=request_weight_debit,
                        controller_update=controller_update,
                        provider=provider,
                        model_lab=model_lab,
                        success=success,
                        now=now,
                    ),
                    return_document=ReturnDocument.AFTER,
                    session=session,
                ),
            )
        else:
            budget = timed_mongo(
                "budget_update",
                lambda: user_budgets.find_one(budget_query, session=session),
            )
        if not budget:
            exact_budget = user_budgets.find_one(
                {"loginInfo.userId": user_id, "routeId": route_id},
                session=session,
            )
            if exact_budget and update_counters:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "route_budget_exhausted",
                        "route_id": route_id,
                        "message": "Exact user+route dollar budget is insufficient",
                    },
                )
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "route_budget_state_missing",
                    "route_id": route_id,
                    "message": "Exact user+route budget state is required for usage logging",
                },
            )

        if update_counters:
            aggregate_increments = {
                "totalRequests": 1,
                "totalSpendUsd": routing_fee_spend_usd,
                "successfulRequests": 1 if success else 0,
                "failedRequests": 0 if success else 1,
                f"providerCompletions.{provider}": 1,
            }
            if model_lab:
                aggregate_increments[f"labCompletions.{model_lab}"] = 1
            user_result = timed_mongo(
                "user_aggregate_update",
                lambda: users.update_one(
                    {"id": user_id},
                    {
                        "$inc": aggregate_increments,
                        "$set": {"lastRequestAt": now, "updatedAt": now},
                    },
                    session=session,
                ),
            )
            if getattr(user_result, "matched_count", 0) != 1:
                raise HTTPException(status_code=409, detail="Exact usage user state is missing")

        document = {
            "id": str(uuid.uuid4()),
            **idempotency_query,
            "sessionId": str(metadata.get("session_id") or "").strip() or None,
            "routeName": route_name,
            "provider": provider,
            "modelId": model_id,
            "modelProfile": usage_model_profile(model, provider),
            "success": success,
            "spendUsd": routing_fee_spend_usd,
            "providerSpendUsd": final_request_spend_usd,
            "routingFeeUsd": routing_fee_spend_usd,
            "accountedSpendUsd": budget_debit_usd,
            "spendAccountingMode": spend_accounting_mode,
            "budgetRemainingUsd": budget.get("budgetRemainingUsd"),
            "remainingWeight": budget.get("remainingWeight"),
            "budgetShadowPrice": budget.get("budgetShadowPrice"),
            "requestWeight": request_weight if update_counters else None,
            "requestWeightDebit": request_weight_debit if update_counters else None,
            "accountingCountersUpdated": update_counters,
            "metadata": {
                **metadata,
                "billing": {
                    **metadata["billing"],
                    "accountedSpendUsd": budget_debit_usd,
                    "spendAccountingMode": spend_accounting_mode,
                },
                "budget_controller": controller_update if update_counters else None,
            },
            "createdAt": now,
        }
        timed_mongo(
            "usage_insert",
            lambda: usage_logs.insert_one(document, session=session),
        )
        return document

    for attempt in range(1, USAGE_LOG_TRANSACTION_MAX_ATTEMPTS + 1):
        try:
            with database.client.start_session() as session:
                with session.start_transaction():
                    result = persist(session)
            mongo_stage_timings_ms["total"] = round(
                (time.perf_counter() - write_started) * 1000,
                2,
            )
            result["_stage_timings_ms"] = dict(mongo_stage_timings_ms)
            return result
        except HTTPException:
            raise
        except DuplicateKeyError:
            existing = usage_logs.find_one(idempotency_query)
            if existing:
                _, spend_accounting_mode, budget_debit_usd = budget_accounting_facts()
                return require_idempotent_usage_match(
                    existing,
                    route_name=route_name,
                    model_id=model_id,
                    provider=provider,
                    success=success,
                    provider_spend_usd=final_request_spend_usd,
                    routing_fee_usd=routing_fee_spend_usd,
                    spend_accounting_mode=spend_accounting_mode,
                    accounted_spend_usd=budget_debit_usd,
                    request_weight=request_weight,
                    request_weight_debit=request_weight_debit,
                    update_counters=update_counters,
                )
            raise HTTPException(status_code=409, detail="Duplicate routing_call_id without a stored usage log")
        except Exception as error:
            if (
                attempt < USAGE_LOG_TRANSACTION_MAX_ATTEMPTS
                and is_retryable_usage_log_transaction_error(error)
            ):
                time.sleep(usage_log_transaction_retry_delay(attempt))
                continue
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "usage_log_transaction_failed",
                    "message": str(redact_sensitive(str(error)))[:500],
                    "transaction_attempts": attempt,
                },
            ) from error

    raise AssertionError("usage log transaction retry loop terminated without a result")


def is_sensitive_usage_metadata_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return (
        normalized in {
            "accounting_token",
            "authorization",
            "api_key",
            "password",
            "secret",
            "token",
        }
        or normalized.endswith(("_api_key", "_password", "_secret", "_token"))
    )


def public_usage_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: public_usage_metadata(item)
            for key, item in value.items()
            if not is_sensitive_usage_metadata_key(key)
        }
    if isinstance(value, list):
        return [public_usage_metadata(item) for item in value]
    return redact_sensitive(value)


def usage_log_response_from_document(document: dict[str, Any]) -> dict[str, Any]:
    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
    billing = metadata.get("billing") if isinstance(metadata.get("billing"), dict) else {}
    provider_spend_usd = finite_profile_number(billing.get("finalRequestSpendUsd"))
    routing_fee_spend_usd = finite_profile_number(document.get("spendUsd"))
    created_at = document.get("createdAt")
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at_iso = created_at.astimezone(timezone.utc).isoformat()
    else:
        created_at_iso = ""

    required_strings = {
        "route_id": document.get("routeId"),
        "route_name": document.get("routeName"),
        "model_id": document.get("modelId"),
        "provider": document.get("provider"),
        "inference_mode": metadata.get("inference_mode"),
        "session_id": metadata.get("session_id"),
    }
    invalid_fields = [
        field
        for field, value in required_strings.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if not isinstance(metadata.get("routing_call_id"), str) or not metadata["routing_call_id"].strip():
        invalid_fields.append("metadata.routing_call_id")
    if (
        not created_at_iso
        or provider_spend_usd is None
        or provider_spend_usd < 0
        or routing_fee_spend_usd is None
        or routing_fee_spend_usd < 0
        or not isinstance(document.get("success"), bool)
        or required_strings["inference_mode"] not in INFERENCE_MODES
        or invalid_fields
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "persisted_usage_log_incomplete",
                "message": "Persisted usage log is missing authoritative response fields",
                "invalid_fields": sorted(set(invalid_fields)),
            },
        )

    return {
        "ts": created_at_iso,
        **{field: str(value) for field, value in required_strings.items()},
        "success": document["success"],
        "spend_usd": provider_spend_usd,
        "routing_fee_usd": routing_fee_spend_usd,
        "accounted": True,
        "metadata": public_usage_metadata(metadata),
    }


def write_request_started_log(
    *,
    user_id: str,
    route_id: str,
    metadata: dict[str, Any],
) -> None:
    return


def parse_schedule_interval(schedule: str) -> timedelta:
    text = str(schedule or "24h").strip().lower()
    unit = text[-1]
    try:
        value = int(text[:-1])
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail="schedule must use a simple interval like 15m, 1h, 24h, or 7d.",
        ) from error

    if value <= 0:
        raise HTTPException(status_code=400, detail="schedule interval must be positive.")
    if unit == "m":
        return timedelta(minutes=value)
    if unit == "h":
        return timedelta(hours=value)
    if unit == "d":
        return timedelta(days=value)
    raise HTTPException(
        status_code=400,
        detail="schedule must end with m, h, or d.",
    )


def next_run_at(schedule: str, *, from_time: datetime | None = None) -> str:
    from_time = from_time or datetime.now(timezone.utc)
    return (from_time + parse_schedule_interval(schedule)).isoformat()


def save_route_update_job(body: RouteUpdateJobRequest) -> dict[str, Any]:
    catalog = validate_hydrated_model_catalog(body.model_catalog)
    now = utc_now()
    next_run = next_run_at(body.schedule)
    route_update_jobs = require_mongo_collection("LEROUTER_ROUTE_UPDATE_JOB_COLLECTION", "route_update_jobs")
    existing = route_update_jobs.find_one({
        "userId": body.user_id,
        "routeId": body.route_id,
    })
    job_id = str((existing or {}).get("id") or uuid.uuid4())
    row = {
        "id": job_id,
        "userId": body.user_id,
        "routeId": body.route_id,
        "routes": body.routes,
        "modelCatalog": catalog,
        "schedule": body.schedule,
        "candidatesPerRoute": body.candidates_per_route,
        "enabled": body.enabled,
        "nextRunAt": next_run,
        "metadata": body.metadata,
        "createdAt": (existing or {}).get("createdAt") or now,
        "updatedAt": now,
    }
    ensure_user_exists(body.user_id)
    route_update_jobs.update_one(
        {"userId": body.user_id, "routeId": body.route_id},
        {"$set": dict(row)},
        upsert=True,
    )
    write_json_file(route_update_job_file_path(job_id), row)

    return {
        "id": job_id,
        "user_id": body.user_id,
        "route_id": body.route_id,
        "schedule": body.schedule,
        "enabled": body.enabled,
        "nextRunAt": next_run,
    }


async def run_route_update_job(row: dict[str, Any]) -> dict[str, Any]:
    routes = row.get("routes")
    if isinstance(routes, str):
        routes = json.loads(routes)
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        metadata = json.loads(metadata or "{}")
    metadata = metadata if isinstance(metadata, dict) else {}
    catalog = validate_hydrated_model_catalog(row.get("modelCatalog"))
    route_candidates = await select_route_candidates(
        routes=routes,
        catalog=catalog,
        candidates_per_route=int(row.get("candidatesPerRoute") or 5),
        metadata=metadata,
    )
    route_candidates, _candidate_pool_versions = await precompute_candidate_pool_embeddings(
        route_candidates,
        user_id=str(row.get("userId") or ""),
        route_id=str(row.get("routeId") or ""),
    )
    save_candidate_pool(
        user_id=row.get("userId"),
        route_id=row.get("routeId"),
        route_candidates=route_candidates,
        metadata={**metadata, "source": "route_update_job", "job_id": row.get("id")},
        route_definitions=routes,
    )

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    next_run = next_run_at(row.get("schedule"), from_time=now_dt)
    row.update({"lastRunAt": now, "nextRunAt": next_run, "updatedAt": now})
    route_update_jobs = require_mongo_collection("LEROUTER_ROUTE_UPDATE_JOB_COLLECTION", "route_update_jobs")
    update_result = route_update_jobs.update_one(
        {"id": row.get("id")},
        {
            "$set": {
                "lastRunAt": now,
                "nextRunAt": next_run,
                "updatedAt": now,
            },
        },
    )
    if getattr(update_result, "matched_count", 0) != 1:
        raise HTTPException(
            status_code=409,
            detail={"error": "route_update_job_state_missing", "job_id": row.get("id")},
        )
    if row.get("id"):
        write_json_file(route_update_job_file_path(str(row.get("id"))), row)

    return {
        "id": row.get("id"),
        "route_id": row.get("routeId"),
        "updated_routes": sorted(route_candidates.keys()),
        "nextRunAt": next_run,
    }


ROUTE_UPDATE_CATALOG_ERROR_CODES = frozenset({
    "model_catalog_required",
    "insufficient_model_catalog",
    "invalid_hydrated_model_catalog",
})


def is_invalid_route_update_catalog_error(error: HTTPException) -> bool:
    detail = error.detail if isinstance(error.detail, dict) else {}
    return (
        error.status_code == 422
        and detail.get("error") in ROUTE_UPDATE_CATALOG_ERROR_CODES
    )


def disable_invalid_route_update_job(
    row: dict[str, Any],
    error: HTTPException,
) -> dict[str, Any]:
    job_id = str(row.get("id") or "").strip()
    if not job_id:
        raise HTTPException(
            status_code=500,
            detail={"error": "route_update_job_id_missing"},
        )
    detail = dict(error.detail) if isinstance(error.detail, dict) else {
        "error": "invalid_route_update_job",
        "message": str(error.detail),
    }
    now = utc_now()
    failed_state = {
        "enabled": False,
        "status": "failed",
        "nextRunAt": None,
        "lastFailureAt": now,
        "lastError": detail,
        "updatedAt": now,
    }
    row.update(failed_state)
    write_json_file(route_update_job_file_path(job_id), row)

    mongo_state = "updated"
    try:
        route_update_jobs = require_mongo_collection(
            "LEROUTER_ROUTE_UPDATE_JOB_COLLECTION",
            "route_update_jobs",
        )
        update_result = route_update_jobs.update_one(
            {"id": job_id},
            {"$set": failed_state},
        )
        if getattr(update_result, "matched_count", 0) != 1:
            mongo_state = "missing"
            print(
                f"LEROUTER_ROUTE_UPDATE_JOB_DISABLE_MONGO_MISSING {job_id}",
                flush=True,
            )
    except Exception as store_error:
        mongo_state = "unavailable"
        print(
            f"LEROUTER_ROUTE_UPDATE_JOB_DISABLE_MONGO_FAILED {job_id}: "
            f"{store_error.__class__.__name__}",
            flush=True,
        )

    return {
        "id": job_id,
        "route_id": row.get("routeId"),
        "status": "failed",
        "enabled": False,
        "nextRunAt": None,
        "error": detail,
        "mongo_state": mongo_state,
    }


async def run_due_route_update_jobs(limit: int = 20) -> list[dict[str, Any]]:
    now = utc_now()
    rows = []
    for path in sorted(ROUTE_UPDATE_JOB_STORE_DIR.glob("*.json")):
        row = read_json_file(path)
        if not row:
            continue
        if row.get("enabled") not in {True, 1, "true", "True"}:
            continue
        next_run = row.get("nextRunAt")
        if next_run and str(next_run) > now:
            continue
        rows.append(row)
    rows = sorted(rows, key=lambda item: str(item.get("nextRunAt") or ""))[:limit]

    results = []
    for row in rows:
        try:
            results.append(await run_route_update_job(row))
        except HTTPException as error:
            if not is_invalid_route_update_catalog_error(error):
                raise
            results.append(disable_invalid_route_update_job(row, error))
    return results


def model_dump_compat(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def compact_job_doc(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    public = dict(row)
    public.pop("_id", None)
    return public


def candidate_selection_jobs_collection():
    try:
        return mongo_collection("LEROUTER_CANDIDATE_SELECTION_JOB_COLLECTION", "candidate_selection_jobs")
    except Exception as error:
        print(f"LEROUTER_SETUP_JOB_MONGO_UNAVAILABLE {error.__class__.__name__}: {error}", flush=True)
        return None


def insert_candidate_selection_job(job: dict[str, Any]) -> None:
    write_json_file(job_file_path(job["id"]), job)
    collection = candidate_selection_jobs_collection()
    if collection is not None:
        try:
            collection.insert_one(dict(job))
            return
        except Exception as error:
            print(f"LEROUTER_SETUP_JOB_MONGO_INSERT_FAILED {error.__class__.__name__}: {error}", flush=True)


def load_candidate_selection_job(job_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    row = None
    row = read_json_file(job_file_path(job_id))
    if row and user_id and row.get("userId") != user_id:
        row = None
    if row is not None:
        return compact_job_doc(row)
    collection = candidate_selection_jobs_collection()
    if collection is not None:
        query: dict[str, Any] = {"id": job_id}
        if user_id:
            query["userId"] = user_id
        try:
            row = collection.find_one(query)
        except Exception as error:
            print(f"LEROUTER_SETUP_JOB_MONGO_LOAD_FAILED {error.__class__.__name__}: {error}", flush=True)
            row = None
    return compact_job_doc(row)


def update_candidate_selection_job(job_id: str, updates: dict[str, Any]) -> None:
    row = read_json_file(job_file_path(job_id)) or {"id": job_id}
    row.update(updates)
    write_json_file(job_file_path(job_id), row)
    collection = candidate_selection_jobs_collection()
    if collection is not None:
        try:
            collection.update_one({"id": job_id}, {"$set": updates})
            return
        except Exception as error:
            print(f"LEROUTER_SETUP_JOB_MONGO_UPDATE_FAILED {error.__class__.__name__}: {error}", flush=True)


def job_error_payload(error: Exception) -> dict[str, Any]:
    detail = getattr(error, "detail", None)
    text = redact_sensitive(str(error))
    if detail is not None:
        try:
            detail = json.loads(detail) if isinstance(detail, str) else detail
        except json.JSONDecodeError:
            pass
    detail = redact_sensitive(detail)
    return {
        "type": error.__class__.__name__,
        "message": text[:1000],
        "detail": detail,
    }


def require_setup_inputs(body: CandidateModelsRequest) -> None:
    if not body.routes:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "routes_required",
                "message": "routes must be created by the user/agent and submitted explicitly; default routes are not allowed",
            },
        )
    route_names = [
        str(route_name).strip()
        for route_name in body.routes.keys()
        if str(route_name).strip()
    ]
    if len(route_names) < MIN_USER_ROUTES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "insufficient_routes",
                "message": f"between {MIN_USER_ROUTES} and {MAX_USER_ROUTES} real user routes are required so ArchRouter can classify the user's recurring work patterns",
                "route_count": len(route_names),
                "min_routes": MIN_USER_ROUTES,
                "max_routes": MAX_USER_ROUTES,
            },
        )
    if len(route_names) > MAX_USER_ROUTES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "too_many_routes",
                "message": f"between {MIN_USER_ROUTES} and {MAX_USER_ROUTES} real user routes are required; split only stable recurring user work patterns into routes",
                "route_count": len(route_names),
                "min_routes": MIN_USER_ROUTES,
                "max_routes": MAX_USER_ROUTES,
            },
        )
    smoke_routes = [
        route_name
        for route_name in route_names
        if "smoke" in route_name.lower() or route_name.lower().startswith("lerouter_setup_job")
    ]
    if smoke_routes:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "non_user_routes_rejected",
                "message": "setup routes must describe recurring user or agent work, not smoke/setup placeholders",
                "routes": smoke_routes,
            },
        )
    require_explicit_model_catalog(body.model_catalog)


def create_candidate_selection_job_record(body: RouteSetupRequest) -> dict[str, Any]:
    now = utc_now()
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "status": "queued",
        "userId": body.user_id,
        "routeId": body.route_id,
        "routes": body.routes,
        "candidatesPerRoute": body.candidates_per_route,
        "payload": model_dump_compat(body),
        "result": None,
        "error": None,
        "createdAt": now,
        "updatedAt": now,
        "startedAt": None,
        "finishedAt": None,
    }
    insert_candidate_selection_job(job)
    return compact_job_doc(job) or job


async def process_candidate_selection_job(job_id: str, initial_job: dict[str, Any] | None = None) -> dict[str, Any]:
    row = initial_job or load_candidate_selection_job(job_id)
    if not row:
        raise RuntimeError(f"candidate selection job not found: {job_id}")
    if row.get("status") == "succeeded":
        return compact_job_doc(row) or row

    now = utc_now()
    update_candidate_selection_job(
        job_id,
        {
            "status": "running",
            "startedAt": row.get("startedAt") or now,
            "updatedAt": now,
            "error": None,
        },
    )

    try:
        body = RouteSetupRequest(**(row.get("payload") or {}))
        require_setup_inputs(body)
        body.metadata = await compute_budget_fields_from_history(
            route_id=body.route_id,
            routes=body.routes,
            metadata=body.metadata,
        )
        catalog, profile_rejections = hydrate_model_catalog(body.model_catalog)
        requested_candidate_count = route_candidate_limit(body.candidates_per_route)
        if len(catalog) < requested_candidate_count:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "insufficient_hydrated_catalog_for_requested_pool",
                    "message": (
                        "Mongo profile hydration removed models required to build the requested "
                        "candidate pool."
                    ),
                    "requested_candidate_count": requested_candidate_count,
                    "hydrated_model_count": len(catalog),
                    "profile_rejections": profile_rejections,
                },
            )
        inference_mode = normalize_inference_mode(body.metadata.get("inference_mode"), "user_managed")
        routing_strategy = str(body.metadata.get("routing_strategy") or "route_pool").strip().lower()
        if routing_strategy == CATALOG_WIDE_ROUTING_STRATEGY:
            catalog, catalog_version = await precompute_catalog_embeddings(catalog)
            route_candidates = {CATALOG_ROUTE_NAME: catalog}
            save_catalog_wide_policy(
                user_id=body.user_id,
                route_id=body.route_id,
                catalog=catalog,
                catalog_version=catalog_version,
                metadata={
                    **body.metadata,
                    "source": "agent_catalog_wide_setup_job",
                    "candidate_selection_job_id": job_id,
                },
            )
        elif routing_strategy == "route_pool":
            route_candidates = await select_route_candidates(
                routes=body.routes,
                catalog=catalog,
                candidates_per_route=body.candidates_per_route,
                metadata={**body.metadata, "candidate_selection_job_id": job_id},
            )
            route_candidates, _candidate_pool_versions = await precompute_candidate_pool_embeddings(
                route_candidates,
                user_id=str(body.user_id or ""),
                route_id=str(body.route_id or ""),
            )
            save_candidate_pool(
                user_id=body.user_id,
                route_id=body.route_id,
                route_candidates=route_candidates,
                metadata={
                    **body.metadata,
                    "source": "agent_candidate_optimizer_job",
                    "candidate_selection_job_id": job_id,
                },
                route_definitions=body.routes,
            )
        else:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_routing_strategy", "routing_strategy": routing_strategy},
            )
        route_update_job = None
        if body.update_schedule:
            route_update_job = save_route_update_job(
                RouteUpdateJobRequest(
                    user_id=body.user_id,
                    route_id=body.route_id,
                    routes=body.routes,
                    model_catalog=catalog,
                    schedule=body.update_schedule,
                    candidates_per_route=body.candidates_per_route,
                    enabled=True,
                    metadata={
                        **body.metadata,
                        "source": "agent_setup_job",
                        "candidate_selection_job_id": job_id,
                    },
                )
            )
        result = {
            "route_id": body.route_id,
            "user_id": body.user_id,
            "saved": True,
            "metadata": body.metadata,
            "catalog_summary": setup_catalog_summary(
                catalog=catalog,
                route_candidates=route_candidates,
                inference_mode=inference_mode,
            ),
            "candidate_pools": route_candidates,
            "hydrated_model_catalog": catalog,
            "profile_rejections": profile_rejections,
            "route_update_job": route_update_job,
        }
        finished = utc_now()
        update_candidate_selection_job(
            job_id,
            {
                "status": "succeeded",
                "result": result,
                "error": None,
                "updatedAt": finished,
                "finishedAt": finished,
            },
        )
        updated = load_candidate_selection_job(job_id)
        return compact_job_doc(updated) or {"id": job_id, "status": "succeeded", "result": result}
    except Exception as error:
        finished = utc_now()
        update_candidate_selection_job(
            job_id,
            {
                "status": "failed",
                "error": job_error_payload(error),
                "updatedAt": finished,
                "finishedAt": finished,
            },
        )
        raise


def openai_compatible_response(
    *,
    request_model: str,
    route_result: dict[str, Any],
) -> dict[str, Any]:
    response = route_result.get("response") or {}
    content = response.get("content", "")
    tool_calls = response.get("tool_calls") or []
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls

    lerouter_metadata = {
        "route_id": route_result.get("route_id"),
        "route_name": route_result.get("route_name"),
        "routing_strategy": route_result.get("routing_strategy", "route_pool"),
        "selected_model": response.get("model"),
        "provider": response.get("provider"),
        "estimated_spend_usd": route_result.get("estimated_spend_usd"),
        "provider_attempts": route_result.get("provider_attempts", []),
    }
    if route_result.get("latency_ms") is not None:
        lerouter_metadata["latency_ms"] = route_result.get("latency_ms")

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request_model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": openai_finish_reason(
                    str(response.get("provider") or ""),
                    response.get("finish_reason"),
                ) or "stop",
            }
        ],
        "usage": response.get("usage") or {},
        "lerouter": lerouter_metadata,
    }


def route_selection_response(route_result: dict[str, Any]) -> dict[str, Any]:
    user_id = str(route_result.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "selection_user_identity_missing",
                "message": "The routed selection has no authoritative user identity",
            },
        )
    best_model = route_result.get("best_model") or {}
    model_id = best_model.get("model_id")
    provider = executable_provider_for_model(best_model) if best_model else "unknown"
    pipeline = route_result.get("pipeline") or {}
    ranked_candidates = pipeline.get("euristique_budget_manager")
    if not isinstance(ranked_candidates, list):
        ranked_candidates = []
    routing_call_id = str(route_result.get("routing_call_id") or "").strip()
    workflow_execution = route_result.get("workflow_execution")
    workflow_execution = workflow_execution if isinstance(workflow_execution, dict) else None
    if not ranked_candidates:
        ranked_candidates = [best_model]
    ranked_model_ids = [
        str(candidate.get("model_id") or "").strip()
        for candidate in ranked_candidates
        if isinstance(candidate, dict)
    ]
    if not ranked_model_ids or ranked_model_ids[0] != str(model_id or "").strip():
        raise HTTPException(
            status_code=502,
            detail={
                "error": "selected_model_ranking_mismatch",
                "message": "The selected model must be rank 1 in the budget-aware candidate order",
                "selected_model_id": model_id,
                "ranked_model_ids": ranked_model_ids,
            },
        )

    execution_candidates: list[dict[str, Any]] = []
    for index, candidate in enumerate(ranked_candidates[:1]):
        if not isinstance(candidate, dict):
            continue
        candidate_model_id = str(candidate.get("model_id") or "").strip()
        candidate_provider = executable_provider_for_model(candidate)
        candidate_routing_call_id = routing_call_id
        candidate_accounting_token = issue_accounting_token(
            user_id=str(route_result.get("user_id") or ""),
            route_id=str(route_result.get("route_id") or ""),
            route_name=str(route_result.get("route_name") or ""),
            model_id=candidate_model_id,
            provider=candidate_provider,
            model_profile=candidate,
            request_weight=candidate.get("request_weight"),
            routing_call_id=candidate_routing_call_id,
            workflow_execution=workflow_execution,
        )
        execution_candidates.append(
            {
                "rank": index + 1,
                "user_id": user_id,
                "routing_call_id": candidate_routing_call_id,
                "accounting_token": candidate_accounting_token,
                "selected_model": candidate,
                "selected_model_id": candidate_model_id,
                "native_model_id": provider_native_model_id(
                    candidate_model_id,
                    candidate_provider,
                    candidate,
                ),
                "provider": candidate_provider,
                "workflow_execution": workflow_execution,
                "max_tokens": workflow_execution.get("max_output_tokens") if workflow_execution else None,
            }
        )
    if not execution_candidates:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "execution_candidate_claims_missing",
                "message": "The ranked selection produced no signed execution candidates",
            },
        )
    primary_execution = execution_candidates[0]
    route_worker = pipeline.get("route_worker") if isinstance(pipeline.get("route_worker"), dict) else {}
    e5_evidence = route_worker.get("e5") if isinstance(route_worker.get("e5"), dict) else {}
    all_ranked_candidates = pipeline.get("catalog_wide_budget_manager")
    if not isinstance(all_ranked_candidates, list):
        all_ranked_candidates = ranked_candidates
    return {
        "ok": True,
        "user_id": user_id,
        "inference_mode": "user_managed",
        "execution_owner": "user",
        "route_id": route_result.get("route_id"),
        "route_name": route_result.get("route_name"),
        "routing_strategy": route_result.get("routing_strategy", "route_pool"),
        "routing_call_id": primary_execution["routing_call_id"],
        "accounting_token": primary_execution["accounting_token"],
        "selected_model": best_model,
        "selected_model_id": model_id,
        "native_model_id": provider_native_model_id(model_id, provider, best_model) if model_id else None,
        "provider": provider,
        "is_open_source": bool(best_model.get("is_open_source")),
        "estimated_spend_usd": route_result.get("estimated_spend_usd"),
        "workflow_execution": workflow_execution,
        "max_tokens": workflow_execution.get("max_output_tokens") if workflow_execution else None,
        "ranked_candidates": ranked_candidates,
        "all_ranked_candidates": all_ranked_candidates,
        "e5": {
            "model_run_id": e5_evidence.get("model_run_id"),
            "code_version": e5_evidence.get("code_version"),
            "selected_model": e5_evidence.get("selected_model"),
            "ranked": e5_evidence.get("ranked"),
        },
        "execution_candidates": execution_candidates,
        "provider_attempts": route_result.get("provider_attempts", []),
        "pipeline": pipeline,
        "latency_ms": route_result.get("latency_ms"),
        "stage_timings_ms": route_result.get("stage_timings_ms"),
    }


def sse_data(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def openai_stream_chunk(
    *,
    completion_id: str,
    request_model: str,
    delta: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    lerouter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": request_model,
        "choices": [
            {
                "index": 0,
                "delta": delta or {},
                "finish_reason": finish_reason,
            }
        ],
    }
    if lerouter:
        chunk["lerouter"] = lerouter
    return chunk


async def stream_openai_compatible_response(
    *,
    request_model: str,
    route_task: asyncio.Task,
    heartbeat_seconds: float = 2.0,
) -> AsyncIterator[str]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    started = time.perf_counter()
    status_index = 0
    status_steps = [
        "request_received",
        "archrouter_classification",
        "candidate_pool_lookup",
        "biencoder_ranking",
        "budget_aware_scoring",
        "provider_request",
    ]

    yield sse_data(
        openai_stream_chunk(
            completion_id=completion_id,
            request_model=request_model,
            delta={"role": "assistant"},
            lerouter={"status": "request_received", "elapsed_ms": 0},
        )
    )

    while not route_task.done():
        await asyncio.sleep(heartbeat_seconds)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        status = status_steps[min(status_index, len(status_steps) - 1)]
        status_index += 1
        yield sse_data(
            openai_stream_chunk(
                completion_id=completion_id,
                request_model=request_model,
                lerouter={"status": status, "elapsed_ms": elapsed_ms},
            )
        )

    try:
        route_result = route_task.result()
    except HTTPException as error:
        error_text = (
            "LeRouter could not complete this request. "
            f"Provider routing failed with HTTP {error.status_code}: {compact_http_error_detail(error)}."
        )
        yield sse_data(
            openai_stream_chunk(
                completion_id=completion_id,
                request_model=request_model,
                delta={"content": error_text},
                lerouter={
                    "status": "error",
                    "status_code": error.status_code,
                    "detail": error.detail,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
        )
        yield sse_data(
            openai_stream_chunk(
                completion_id=completion_id,
                request_model=request_model,
                finish_reason="stop",
                lerouter={"status": "done"},
            )
        )
        yield "data: [DONE]\n\n"
        return
    except Exception as error:
        yield sse_data(
            openai_stream_chunk(
                completion_id=completion_id,
                request_model=request_model,
                delta={"content": "LeRouter could not complete this request because an internal routing error occurred."},
                lerouter={
                    "status": "error",
                    "detail": error.__class__.__name__,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
        )
        yield sse_data(
            openai_stream_chunk(
                completion_id=completion_id,
                request_model=request_model,
                finish_reason="stop",
                lerouter={"status": "done"},
            )
        )
        yield "data: [DONE]\n\n"
        return

    response = route_result.get("response") or {}
    content = response.get("content", "")
    tool_calls = response.get("tool_calls") or []
    final_delta: dict[str, Any] = {}
    if content:
        final_delta["content"] = content
    if tool_calls:
        final_delta["tool_calls"] = tool_calls

    if final_delta:
        lerouter_metadata = {
            "status": "completed",
            "route_id": route_result.get("route_id"),
            "route_name": route_result.get("route_name"),
            "selected_model": response.get("model"),
            "provider": response.get("provider"),
            "estimated_spend_usd": route_result.get("estimated_spend_usd"),
            "provider_attempts": route_result.get("provider_attempts", []),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        if route_result.get("latency_ms") is not None:
            lerouter_metadata["latency_ms"] = route_result.get("latency_ms")
        yield sse_data(
            openai_stream_chunk(
                completion_id=completion_id,
                request_model=request_model,
                delta=final_delta,
                lerouter=lerouter_metadata,
            )
        )

    yield sse_data(
        openai_stream_chunk(
            completion_id=completion_id,
            request_model=request_model,
            finish_reason=openai_finish_reason(
                str(response.get("provider") or ""),
                response.get("finish_reason"),
            ) or "stop",
            lerouter={"status": "done"},
        )
    )
    yield "data: [DONE]\n\n"


class CandidateModelsRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    user_id: str | None = Field(default=None)
    route_id: str = Field(default="default")
    routes: dict[str, Any] = Field(default_factory=dict)
    model_catalog: list[dict[str, Any]] | None = Field(default=None)
    candidates_per_route: int = Field(default=DEFAULT_ROUTE_CANDIDATES, ge=MIN_ROUTE_CANDIDATES, le=MAX_ROUTE_CANDIDATES)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteRequest(BaseModel):
    user_id: str = Field(default="agent")
    route_id: str = Field(default="default")
    messages: list[dict[str, Any]] | None = Field(default=None)
    prompt: str | None = Field(default=None)
    input: str | None = Field(default=None)
    tools: list[dict[str, Any]] | None = Field(default=None)
    tool_choice: Any | None = Field(default=None)
    response_format: dict[str, Any] | None = Field(default=None)
    temperature: float | None = Field(default=None)
    max_tokens: int | None = Field(default=None)
    stream: bool = Field(default=False)
    previous_query_data: dict[str, Any] | None = Field(default=None)
    budget: dict[str, Any] | None = Field(default=None)
    provider_options: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    max_candidates: int = Field(default=DEFAULT_ROUTE_CANDIDATES, ge=MIN_ROUTE_CANDIDATES, le=MAX_ROUTE_CANDIDATES)
    execute: bool = Field(default=True)
    inference_mode: str | None = Field(default=None)
    workflow_run_id: str | None = Field(default=None)
    budget_scope_id: str | None = Field(default=None)
    workflow_events: list[dict[str, Any]] = Field(default_factory=list)


class RouteSetupRequest(CandidateModelsRequest):
    update_schedule: str | None = Field(default=None)


class RouteUpdateJobRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    user_id: str | None = Field(default=None)
    route_id: str = Field(default="default")
    routes: dict[str, Any] = Field(default_factory=dict)
    model_catalog: list[dict[str, Any]] | None = Field(default=None)
    schedule: str = Field(default="24h")
    candidates_per_route: int = Field(default=DEFAULT_ROUTE_CANDIDATES, ge=MIN_ROUTE_CANDIDATES, le=MAX_ROUTE_CANDIDATES)
    enabled: bool = Field(default=True)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpenAIChatCompletionRequest(BaseModel):
    model: str = Field(default="lerouter")
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = Field(default=None)
    tool_choice: Any | None = Field(default=None)
    response_format: dict[str, Any] | None = Field(default=None)
    temperature: float | None = Field(default=None)
    max_tokens: int | None = Field(default=None)
    stream: bool = Field(default=False)
    user: str | None = Field(default=None)
    metadata: dict[str, Any] | None = Field(default=None)


class RouteUsageLogRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    user_id: str | None = Field(default=None)
    route_id: str = Field(default="default")
    route_name: str | None = Field(default=None)
    model_id: str | None = Field(default=None)
    provider: str | None = Field(default=None)
    accounting_token: str = Field(min_length=1)
    inference_mode: str | None = Field(default=None)
    success: bool = Field(default=True)
    spend_usd: float = Field(default=0.0, ge=0)
    request_weight: float | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    update_counters: bool = Field(default=True)


class WorkflowRunCreateRequest(BaseModel):
    route_id: str = Field(default="default")
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=4000)
    max_usd: float = Field(gt=0)


class WorkflowScopeCreateRequest(BaseModel):
    parent_scope_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    goal: str = Field(min_length=1, max_length=4000)
    max_usd: float | None = Field(default=None, gt=0)
    max_percent: float | None = Field(default=None, gt=0, le=1)


class WorkflowFinishRequest(BaseModel):
    status: str = Field(pattern="^(completed|failed)$")


class WorkflowReservationRequest(BaseModel):
    accounting_token: str = Field(min_length=1)


api = FastAPI(title="LeRouter Modal API", version="0.1.0")


@api.middleware("http")
async def log_unhandled_request_errors(request: Any, call_next: Any) -> Any:
    try:
        return await call_next(request)
    except Exception as error:
        print(
            "LEROUTER_UNHANDLED_REQUEST_ERROR "
            + json.dumps(
                {
                    "method": getattr(request, "method", None),
                    "path": getattr(getattr(request, "url", None), "path", None),
                    "error_type": error.__class__.__name__,
                    "error": redact_sensitive(str(error)),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        raise


@api.on_event("startup")
async def startup() -> None:
    init_database()


@api.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "code_version": API_CODE_VERSION, "db_path": str(get_db_path())}


@api.get("/v1/models")
async def openai_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "lerouter",
                "object": "model",
                "created": 0,
                "owned_by": "lerouter",
            }
        ],
    }


@api.get("/v1/models/{model_id}")
async def openai_model(model_id: str) -> dict[str, Any]:
    if model_id != "lerouter":
        raise HTTPException(status_code=404, detail="model not found")
    return {
        "id": "lerouter",
        "object": "model",
        "created": 0,
        "owned_by": "lerouter",
    }


@api.get("/agent/hermes-manifest")
async def hermes_manifest() -> dict[str, Any]:
    return {
        "provider": {
            "name": "LeRouter",
            "base_url": os.environ.get("LEROUTER_PUBLIC_BASE_URL", ""),
            "default_mode": "user_managed",
            "supported_inference_modes": ["user_managed", "router_managed"],
            "selection_path": "/lerouter/select",
            "usage_log_path": "/lerouter/usage-log",
            "openai_compatible_base_path": "/v1",
            "chat_completions_path": "/v1/chat/completions",
            "model": "lerouter",
            "auth": "Authorization: Bearer <LEROUTER_AGENT_TOKEN>",
            "user_managed_execution": "Hermes calls /lerouter/select, executes selected_model_id/native_model_id through its native adapter, then calls /lerouter/usage-log.",
            "router_managed_execution": "Hermes calls /v1/chat/completions or /lerouter/route with execute=true and LeRouter executes inference.",
        },
        "setup": {
            "routes_path": "/agent/setup",
            "setup_jobs_path": "/agent/setup-jobs",
            "route_update_jobs_path": "/agent/route-update-jobs",
            "run_due_jobs_path": "/agent/route-update-jobs/run-due",
            "schedule_format": "simple intervals: 15m, 1h, 24h, 7d",
            "user_managed_requirement": "Hermes must send only models it can execute with the user's configured provider keys, then show setup.catalog_summary.routable_models to the user.",
        },
        "routing_components": {
            "routing_worker": routing_worker_url(),
            "archrouter": archrouter_url(),
            "biencoder": E5_ROUTER_URL,
            "model_selector": MODEL_SELECTOR_URL,
        },
    }


@api.post("/agent/candidate-models")
async def agent_candidate_models(
    body: CandidateModelsRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    auth_context = require_agent_access(authorization)
    init_database()
    require_setup_inputs(body)

    catalog, profile_rejections = hydrate_model_catalog(body.model_catalog)
    inference_mode = normalize_inference_mode(body.metadata.get("inference_mode"), "user_managed")
    user_id = auth_user_id(auth_context, body.user_id)
    route_id = auth_route_id(auth_context, body.route_id)
    route_candidates = await select_route_candidates(
        routes=body.routes,
        catalog=catalog,
        candidates_per_route=body.candidates_per_route,
        metadata=body.metadata,
    )
    route_candidates, candidate_pool_versions = await precompute_candidate_pool_embeddings(
        route_candidates,
        user_id=str(user_id or ""),
        route_id=str(route_id or ""),
    )
    save_candidate_pool(
        user_id=user_id,
        route_id=route_id,
        route_candidates=route_candidates,
        metadata={**body.metadata, "source": "agent_candidate_optimizer"},
        route_definitions=body.routes,
    )

    return {
        "route_id": route_id,
        "user_id": user_id,
        "saved": True,
        "catalog_summary": setup_catalog_summary(
            catalog=catalog,
            route_candidates=route_candidates,
            inference_mode=inference_mode,
        ),
        "candidate_pools": route_candidates,
        "candidate_pool_versions": candidate_pool_versions,
        "hydrated_model_catalog": catalog,
        "profile_rejections": profile_rejections,
    }


@api.post("/agent/setup")
async def agent_setup(
    body: RouteSetupRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    auth_context = require_agent_access(authorization)
    user_id = auth_user_id(auth_context, body.user_id)
    route_id = auth_route_id(auth_context, body.route_id)
    body.user_id = user_id
    body.route_id = route_id
    require_setup_inputs(body)
    body.metadata = await compute_budget_fields_from_history(
        route_id=body.route_id,
        routes=body.routes,
        metadata=body.metadata,
    )
    setup_result = await agent_candidate_models(body, authorization)
    hydrated_catalog = setup_result.get("hydrated_model_catalog")
    if not isinstance(hydrated_catalog, list) or not hydrated_catalog:
        raise HTTPException(status_code=502, detail="candidate setup returned no hydrated model catalog")
    job = None
    if body.update_schedule:
        job = save_route_update_job(
            RouteUpdateJobRequest(
                user_id=user_id,
                route_id=route_id,
                routes=body.routes,
                model_catalog=hydrated_catalog,
                schedule=body.update_schedule,
                candidates_per_route=body.candidates_per_route,
                enabled=True,
                metadata={
                    **body.metadata,
                    "source": "agent_setup",
                },
            )
        )

    return {
        "ok": True,
        "provider": {
            "base_path": "/v1",
            "chat_completions_path": "/v1/chat/completions",
            "model": "lerouter",
        },
        "setup": setup_result,
        "route_update_job": job,
    }


@api.post("/agent/setup-jobs")
async def create_agent_setup_job(
    body: RouteSetupRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    auth_context = require_agent_access(authorization)
    body.user_id = auth_user_id(auth_context, body.user_id)
    body.route_id = auth_route_id(auth_context, body.route_id)
    require_setup_inputs(body)
    body.metadata = {
        **body.metadata,
        "source": body.metadata.get("source") or "agent_setup_job",
        "candidate_pool_source": "modal_model_selector",
    }
    job = create_candidate_selection_job_record(body)
    runner = globals().get("run_candidate_selection_job")
    if runner is None or not hasattr(runner, "spawn"):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "setup_job_runner_unavailable",
                "message": "Modal setup job runner is unavailable in this environment",
                "job_id": job["id"],
            },
        )
    update_candidate_selection_job(job["id"], {"status": "dispatching", "updatedAt": utc_now()})
    job = load_candidate_selection_job(job["id"], body.user_id) or job
    runner.spawn(job)
    return {
        "ok": True,
        "job": job,
        "status_path": f"/agent/setup-jobs/{job['id']}",
    }


@api.get("/agent/setup-jobs/{job_id}")
async def get_agent_setup_job(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    auth_context = require_agent_access(authorization)
    row = load_candidate_selection_job(job_id, auth_context.get("user_id"))
    if not row:
        raise HTTPException(status_code=404, detail="setup job not found")
    return {"job": row}


@api.post("/agent/setup-jobs/{job_id}/run")
async def run_agent_setup_job_endpoint(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    auth_context = require_agent_access(authorization)
    if not load_candidate_selection_job(job_id, auth_context.get("user_id")):
        raise HTTPException(status_code=404, detail="setup job not found")
    return {"job": await process_candidate_selection_job(job_id)}


@api.post("/agent/route-update-jobs")
async def create_route_update_job(
    body: RouteUpdateJobRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    auth_context = require_agent_access(authorization)
    body.user_id = auth_user_id(auth_context, body.user_id)
    body.route_id = auth_route_id(auth_context, body.route_id)
    init_database()
    hydrated_catalog, profile_rejections = hydrate_model_catalog(body.model_catalog)
    body.model_catalog = hydrated_catalog
    return {
        "job": save_route_update_job(body),
        "hydrated_model_catalog": hydrated_catalog,
        "profile_rejections": profile_rejections,
    }


@api.post("/agent/route-update-jobs/run-due")
async def run_due_route_update_jobs_endpoint(
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    require_agent_access(authorization)
    init_database()
    return {"jobs": await run_due_route_update_jobs()}


@api.post("/agent/route-update-jobs/{job_id}/run")
async def run_route_update_job_endpoint(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    auth_context = require_agent_access(authorization)
    init_database()
    row = read_json_file(route_update_job_file_path(job_id))
    if row and auth_context.get("user_id") and row.get("userId") != auth_context["user_id"]:
        row = None
    if row is None:
        try:
            route_update_jobs = require_mongo_collection("LEROUTER_ROUTE_UPDATE_JOB_COLLECTION", "route_update_jobs")
            query = {"id": job_id}
            if auth_context.get("user_id"):
                query["userId"] = auth_context["user_id"]
            row = route_update_jobs.find_one(query)
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail={"error": "route_update_job_store_unavailable"},
            ) from error
    if not row:
        raise HTTPException(status_code=404, detail="route update job not found")
    return {"job": await run_route_update_job(row)}


def required_workflow_identity(auth_context: dict[str, Any], route_id: str) -> tuple[str, str]:
    user_id = auth_user_id(auth_context)
    if not user_id:
        raise HTTPException(status_code=422, detail={"error": "workflow_user_identity_required"})
    return user_id, auth_route_id(auth_context, route_id)


@api.post("/lerouter/workflow-runs")
async def create_workflow_run_endpoint(body: WorkflowRunCreateRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user_id, route_id = required_workflow_identity(require_agent_access(authorization), body.route_id)
    init_database()
    runs, scopes, _ = workflow_collections()
    now = datetime.now(timezone.utc)
    run_id = f"wrun_{uuid.uuid4().hex}"
    root_scope_id = f"wscope_{uuid.uuid4().hex}"
    database = mongo_database()
    with database.client.start_session() as session:
        with session.start_transaction():
            runs.insert_one({"id": run_id, "userId": user_id, "routeId": route_id, "name": body.name, "goal": body.goal, "rootScopeId": root_scope_id, "status": "active", "createdAt": now, "updatedAt": now}, session=session)
            scopes.insert_one({"id": root_scope_id, "userId": user_id, "routeId": route_id, "runId": run_id, "parentScopeId": None, "name": body.name, "goal": body.goal, "maxUsd": float(body.max_usd), "spentUsd": 0.0, "reservedUsd": 0.0, "status": "active", "createdAt": now, "updatedAt": now}, session=session)
    return {"workflow_run_id": run_id, "budget_scope_id": root_scope_id, "route_id": route_id, "max_usd": body.max_usd, "status": "active"}


@api.post("/lerouter/workflow-runs/{run_id}/scopes")
async def create_workflow_scope_endpoint(run_id: str, body: WorkflowScopeCreateRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if (body.max_usd is None) == (body.max_percent is None):
        raise HTTPException(status_code=422, detail={"error": "workflow_scope_budget_choice_required"})
    user_id, route_id = required_workflow_identity(require_agent_access(authorization), "default")
    _, chain = workflow_scope_chain(user_id=user_id, route_id=route_id, run_id=run_id, scope_id=body.parent_scope_id)
    parent = chain[-1]
    maximum = float(body.max_usd) if body.max_usd is not None else float(parent["maxUsd"]) * float(body.max_percent)
    if maximum > float(parent["maxUsd"]) + 1e-12:
        raise HTTPException(status_code=422, detail={"error": "workflow_child_budget_exceeds_parent"})
    _, scopes, _ = workflow_collections()
    now = datetime.now(timezone.utc)
    scope_id = f"wscope_{uuid.uuid4().hex}"
    scopes.insert_one({"id": scope_id, "userId": user_id, "routeId": route_id, "runId": run_id, "parentScopeId": parent["id"], "name": body.name, "goal": body.goal, "maxUsd": maximum, "spentUsd": 0.0, "reservedUsd": 0.0, "status": "active", "createdAt": now, "updatedAt": now})
    return {"workflow_run_id": run_id, "budget_scope_id": scope_id, "parent_scope_id": parent["id"], "max_usd": maximum}


@api.get("/lerouter/workflow-runs/{run_id}")
async def get_workflow_run_endpoint(run_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user_id, route_id = required_workflow_identity(require_agent_access(authorization), "default")
    runs, scopes, reservations = workflow_collections()
    run = runs.find_one({"userId": user_id, "routeId": route_id, "id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(status_code=404, detail={"error": "workflow_run_missing"})
    scope_rows = list(scopes.find({"userId": user_id, "routeId": route_id, "runId": run_id}, {"_id": 0}).sort("createdAt", 1))
    reservation_rows = list(reservations.find({"userId": user_id, "routeId": route_id, "runId": run_id}, {"_id": 0}).sort("createdAt", 1))
    return {"run": run, "scopes": scope_rows, "reservations": reservation_rows}


def finish_workflow_scope(*, user_id: str, route_id: str, run_id: str, scope_id: str, status: str) -> None:
    _, scopes, reservations = workflow_collections()
    unsettled = reservations.find_one({"userId": user_id, "routeId": route_id, "runId": run_id, "scopeIds": scope_id, "status": {"$in": ["reserved", "started"]}})
    if unsettled:
        raise HTTPException(status_code=409, detail={"error": "workflow_scope_has_unsettled_reservations", "reservation_id": unsettled["id"]})
    now = datetime.now(timezone.utc)
    result = scopes.update_one({"userId": user_id, "routeId": route_id, "runId": run_id, "id": scope_id, "status": "active"}, {"$set": {"status": status, "finishedAt": now, "updatedAt": now}})
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail={"error": "workflow_scope_not_active"})


@api.post("/lerouter/workflow-runs/{run_id}/scopes/{scope_id}/finish")
async def finish_workflow_scope_endpoint(run_id: str, scope_id: str, body: WorkflowFinishRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user_id, route_id = required_workflow_identity(require_agent_access(authorization), "default")
    finish_workflow_scope(user_id=user_id, route_id=route_id, run_id=run_id, scope_id=scope_id, status=body.status)
    return {"ok": True, "workflow_run_id": run_id, "budget_scope_id": scope_id, "status": body.status}


@api.post("/lerouter/workflow-runs/{run_id}/finish")
async def finish_workflow_run_endpoint(run_id: str, body: WorkflowFinishRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    user_id, route_id = required_workflow_identity(require_agent_access(authorization), "default")
    runs, scopes, reservations = workflow_collections()
    if reservations.find_one({"userId": user_id, "routeId": route_id, "runId": run_id, "status": {"$in": ["reserved", "started"]}}):
        raise HTTPException(status_code=409, detail={"error": "workflow_run_has_unsettled_reservations"})
    now = datetime.now(timezone.utc)
    scopes.update_many({"userId": user_id, "routeId": route_id, "runId": run_id, "status": "active"}, {"$set": {"status": body.status, "finishedAt": now, "updatedAt": now}})
    result = runs.update_one({"userId": user_id, "routeId": route_id, "id": run_id, "status": "active"}, {"$set": {"status": body.status, "finishedAt": now, "updatedAt": now}})
    if getattr(result, "matched_count", 0) != 1:
        raise HTTPException(status_code=409, detail={"error": "workflow_run_not_active"})
    return {"ok": True, "workflow_run_id": run_id, "status": body.status}


def require_claim_auth(claim: dict[str, Any], authorization: str | None) -> None:
    user_id, route_id = required_workflow_identity(require_agent_access(authorization), claim["route_id"])
    if not hmac.compare_digest(user_id, claim["user_id"]) or not hmac.compare_digest(route_id, claim["route_id"]):
        raise HTTPException(status_code=403, detail={"error": "workflow_claim_auth_mismatch"})


@api.post("/lerouter/workflow-reservations/start")
async def start_workflow_reservation_endpoint(body: WorkflowReservationRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    claim = verify_accounting_token(body.accounting_token)
    require_claim_auth(claim, authorization)
    reservation = workflow_reservation_from_claim(claim)
    if reservation.get("status") == "started":
        return {"ok": True, "reservation_id": reservation["id"], "status": "started"}
    _, _, reservations = workflow_collections()
    updated = reservations.find_one_and_update(
        {"_id": reservation["_id"], "status": "reserved"},
        {"$set": {"status": "started", "startedAt": datetime.now(timezone.utc), "updatedAt": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(status_code=409, detail={"error": "workflow_reservation_not_reserved"})
    return {"ok": True, "reservation_id": updated["id"], "status": "started"}


@api.post("/lerouter/workflow-reservations/cancel")
async def cancel_workflow_reservation_endpoint(body: WorkflowReservationRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    claim = verify_accounting_token(body.accounting_token)
    require_claim_auth(claim, authorization)
    reservation = finalize_workflow_reservation(claim=claim, spend_usd=0.0, outcome="cancelled")
    return {"ok": True, "reservation_id": reservation["id"], "status": reservation["status"]}


@api.post("/lerouter/workflow-reservations/fail")
async def fail_workflow_reservation_endpoint(body: WorkflowReservationRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    claim = verify_accounting_token(body.accounting_token)
    require_claim_auth(claim, authorization)
    reservation = finalize_workflow_reservation(claim=claim, spend_usd=float(claim["call_limit_usd"]), outcome="failed")
    return {"ok": True, "reservation_id": reservation["id"], "status": reservation["status"], "spend_usd": reservation["spendUsd"]}


@api.post("/lerouter/route")
async def lerouter_route(
    body: RouteRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = time.perf_counter()
    stage_started = started
    stage_timings_ms: dict[str, float] = {}

    def mark_stage(name: str) -> None:
        nonlocal stage_started
        now = time.perf_counter()
        stage_timings_ms[name] = round((now - stage_started) * 1000, 2)
        stage_started = now

    auth_context = require_agent_access(authorization)
    mark_stage("authentication")
    init_database()
    mark_stage("database_init")

    payload = body.model_dump()
    expected_inference_mode = "router_managed" if body.execute else "user_managed"
    inference_mode = normalize_inference_mode(body.inference_mode, expected_inference_mode)
    if inference_mode != expected_inference_mode:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "inference_mode_execution_mismatch",
                "inference_mode": inference_mode,
                "execute": body.execute,
                "expected_inference_mode": expected_inference_mode,
            },
        )
    if body.budget is not None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "request_budget_override_not_allowed",
                "message": "Routing uses only the exact persisted user+route budget state",
            },
        )
    if inference_mode == "user_managed":
        accounting_signing_secret()
    routing_call_id = f"lerouter-{uuid.uuid4().hex}"
    body.user_id = auth_user_id(auth_context, body.user_id) or body.user_id
    body.route_id = auth_route_id(auth_context, body.route_id)
    messages = normalize_messages(payload)
    request_options = provider_request_options(payload)
    if bool(body.workflow_run_id) != bool(body.budget_scope_id):
        raise HTTPException(status_code=422, detail={"error": "workflow_run_and_scope_required"})
    if body.workflow_run_id and (body.execute or inference_mode != "user_managed"):
        raise HTTPException(
            status_code=422,
            detail={"error": "workflow_budget_requires_user_managed_inference"},
        )
    mark_stage("request_normalization")

    async def timed_database_read(function: Any, **kwargs: Any) -> tuple[Any, float]:
        task_started = time.perf_counter()
        result = await asyncio.to_thread(function, **kwargs)
        return result, round((time.perf_counter() - task_started) * 1000, 2)

    route_policy, route_policy_load_ms = await timed_database_read(
        load_route_policy_document,
        user_id=body.user_id,
        route_id=body.route_id,
    )
    stage_timings_ms["route_policy_load"] = route_policy_load_ms
    stage_started = time.perf_counter()
    routing_strategy = str(
        (route_policy or {}).get("routingStrategy")
        or ((route_policy or {}).get("metadata") or {}).get("routing_strategy")
        or "route_pool"
    ).strip().lower()
    catalog_wide = routing_strategy == CATALOG_WIDE_ROUTING_STRATEGY
    routes = route_policy.get("routes") if isinstance(route_policy, dict) else {}
    route_names = list(routes.keys()) if isinstance(routes, dict) else []
    if not route_names:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "route_policy_missing",
                "message": "Route policy must include at least one route before routing can run",
                "route_id": body.route_id,
            },
        )
    catalog_models: list[dict[str, Any]] = []
    if catalog_wide:
        catalog_models = validate_hydrated_model_catalog(route_policy.get("modelCatalog"))
        missing_catalog_embeddings = [
            model.get("model_id")
            for model in catalog_models
            if not isinstance(model.get(PROFILE_EMBEDDING_FIELD), dict)
        ]
        if missing_catalog_embeddings:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "catalog_profile_embeddings_required",
                    "models": missing_catalog_embeddings,
                },
            )
    candidate_pool_versions_by_route = route_policy.get("candidatePoolVersions")
    candidate_pool_versions_by_route = (
        candidate_pool_versions_by_route
        if isinstance(candidate_pool_versions_by_route, dict)
        else {}
    )
    missing_candidate_routes = sorted(
        set(route_names) - set(candidate_pool_versions_by_route)
    )
    extra_candidate_routes = sorted(
        set(candidate_pool_versions_by_route) - set(route_names)
    )
    invalid_version_routes = sorted(
        route_name
        for route_name, version in candidate_pool_versions_by_route.items()
        if not isinstance(version, str) or not version.strip()
    )
    if not catalog_wide and (
        missing_candidate_routes or extra_candidate_routes or invalid_version_routes
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "candidate_pool_version_set_mismatch",
                "missing_routes": missing_candidate_routes,
                "extra_routes": extra_candidate_routes,
                "invalid_version_routes": invalid_version_routes,
            },
        )
    route_definitions = route_definitions_from_policy(route_policy)
    mark_stage("routing_config_validation")

    routing_task = routing_messages_text(messages)

    session_id = str(body.metadata.get("session_id") or "").strip() or None
    workflow_run: dict[str, Any] | None = None
    workflow_scopes: list[dict[str, Any]] = []
    workflow_prediction: dict[str, Any] | None = None
    request_length_prediction: dict[str, Any] | None = None
    worker_request_options = dict(request_options)
    if body.workflow_run_id:
        workflow_run, workflow_scopes = await asyncio.to_thread(
            workflow_scope_chain,
            user_id=body.user_id,
            route_id=body.route_id,
            run_id=body.workflow_run_id,
            scope_id=str(body.budget_scope_id),
        )
        workflow_prediction = await workflow_predictions(
            run=workflow_run,
            scopes=workflow_scopes,
            messages=messages,
            workflow_events=body.workflow_events,
        )
        workflow_prediction["predicted_output_tokens"] = caller_bounded_output_prediction(
            workflow_prediction["predicted_output_tokens"],
            request_options,
        )
        predicted_tokens = max(1, int(round(float(workflow_prediction["predicted_output_tokens"]))))
        mark_stage("workflow_prediction")
    else:
        request_length_prediction = await output_length_prediction(messages=messages)
        request_length_prediction["predicted_output_tokens"] = caller_bounded_output_prediction(
            request_length_prediction["predicted_output_tokens"],
            request_options,
        )
        predicted_tokens = max(
            1,
            int(round(float(request_length_prediction["predicted_output_tokens"]))),
        )
        mark_stage("output_length_prediction")

    async def timed_routing_worker() -> tuple[dict[str, Any], float]:
        task_started = time.perf_counter()
        if catalog_wide:
            result = await call_catalog_routing_worker(
                task=routing_task,
                catalog=catalog_models,
                request_options=worker_request_options,
                predicted_output_tokens=predicted_tokens,
            )
        else:
            result = await call_routing_worker(
                task=routing_task,
                user_id=body.user_id,
                route_id=body.route_id,
                route_names=route_names,
                route_definitions=route_definitions if isinstance(route_definitions, dict) else None,
                candidate_limit=body.max_candidates,
                candidate_pool_versions_by_route=candidate_pool_versions_by_route,
                request_options=worker_request_options,
                predicted_output_tokens=predicted_tokens,
            )
        return result, round((time.perf_counter() - task_started) * 1000, 2)

    parallel_started = time.perf_counter()
    (
        (route_worker_result, routing_worker_ms),
        (budget_state, budget_state_load_ms),
        (previous_usage, switch_history_load_ms),
    ) = await asyncio.gather(
        timed_routing_worker(),
        (
            asyncio.sleep(0, result=(None, 0.0))
            if workflow_run
            else timed_database_read(load_user_budget, user_id=body.user_id, route_id=body.route_id)
        ),
        timed_database_read(
            load_previous_route_usage,
            user_id=body.user_id,
            route_id=body.route_id,
            session_id=session_id,
        ),
    )
    stage_timings_ms["routing_worker"] = routing_worker_ms
    stage_timings_ms["budget_state_load"] = budget_state_load_ms
    stage_timings_ms["switch_history_load"] = switch_history_load_ms
    stage_timings_ms["routing_parallel_window"] = round(
        (time.perf_counter() - parallel_started) * 1000,
        2,
    )
    stage_started = time.perf_counter()
    route_schema = route_worker_result.get("route_schema") if isinstance(route_worker_result, dict) else None
    route_schema = route_schema if isinstance(route_schema, dict) else {}
    route_name = str(route_schema.get("route_id") or "").strip()
    if not route_name or route_name not in route_names:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "invalid_routing_worker_result",
                "message": "Routing worker returned no valid route_id from the configured route distribution",
                "route_name": route_name,
                "available_routes": route_names,
            },
        )
    if catalog_wide:
        if route_name != CATALOG_ROUTE_NAME or route_worker_result.get("archrouter") is not None:
            raise HTTPException(
                status_code=502,
                detail={"error": "invalid_catalog_wide_worker_result"},
            )
        candidates = validate_hydrated_model_catalog(route_worker_result.get("catalog_models"))
        expected_model_ids = [model["model_id"] for model in catalog_models]
        actual_model_ids = [model["model_id"] for model in candidates]
        if actual_model_ids != expected_model_ids:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "catalog_wide_candidate_set_mismatch",
                    "expected_model_ids": expected_model_ids,
                    "actual_model_ids": actual_model_ids,
                },
            )
        stage_timings_ms["catalog_model_load"] = 0.0
        stage_started = time.perf_counter()
        mark_stage("catalog_validation")
        arch_result = {
            "route_id": body.route_id,
            "route_name": CATALOG_ROUTE_NAME,
            "confidence": None,
            "route_schema": route_schema,
            "previous_query_data_injected": False,
            "previous_query_data_present": bool(body.previous_query_data),
            "source": "catalog_wide_no_archrouter",
            "modal": None,
        }
    else:
        candidate_pool_route_id = str(
            route_worker_result.get("candidate_pool_route_id") or ""
        ).strip()
        worker_pool_version = str(route_worker_result.get("candidate_pool_version") or "").strip()
        expected_pool_version = candidate_pool_versions_by_route.get(route_name)
        if candidate_pool_route_id != route_name or worker_pool_version != expected_pool_version:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "routing_worker_candidate_pool_mismatch",
                    "message": "Routing worker did not rank the selected route's exact candidate pool version",
                    "selected_route": route_name,
                    "candidate_pool_route_id": candidate_pool_route_id,
                    "expected_pool_version": expected_pool_version,
                    "worker_pool_version": worker_pool_version or None,
                },
            )
        worker_candidates = route_worker_result.get("candidate_pool")
        candidates = validate_complete_candidate_pool(
            worker_candidates,
            route_name=route_name,
        )
        worker_public_hash = str(
            route_worker_result.get("candidate_pool_public_hash") or ""
        ).strip()
        actual_public_hash = public_candidate_pool_hash(route_name, candidates)
        if not worker_public_hash or worker_public_hash != actual_public_hash:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "routing_worker_public_candidate_pool_mismatch",
                    "selected_route": route_name,
                    "worker_public_hash": worker_public_hash or None,
                    "actual_public_hash": actual_public_hash,
                },
            )
        stage_timings_ms["selected_candidate_pool_load"] = 0.0
        stage_started = time.perf_counter()
        mark_stage("candidate_pool_validation")
        arch_result = {
            "route_id": body.route_id,
            "route_name": route_name,
            "confidence": 0.9,
            "route_schema": route_schema,
            "previous_query_data_injected": False,
            "previous_query_data_present": bool(body.previous_query_data),
            "source": "routing_worker",
            "modal": route_worker_result.get("archrouter"),
        }
    if workflow_prediction:
        workflow_prediction = apply_workflow_weighted_target(
            scopes=workflow_scopes,
            messages=messages,
            request_options=request_options,
            route_schema=route_schema,
            predictions=workflow_prediction,
        )
        mark_stage("workflow_weighted_target")
    biencoder_payload = route_worker_result.get("e5") if isinstance(route_worker_result, dict) else None
    if not isinstance(biencoder_payload, dict):
        raise HTTPException(status_code=502, detail="Routing worker returned no E5 ranking payload")
    biencoder_result = ranked_candidates_from_modal(
        candidates=candidates,
        modal_result=biencoder_payload,
        source=(
            "routing_worker_catalog_wide_gemma4"
            if catalog_wide
            else "routing_worker_e5"
        ),
    )
    switch_result = await switch_cost_estimator(
        user_id=body.user_id,
        route_id=body.route_id,
        route_name=route_name,
        session_id=session_id,
        messages=messages,
        candidates=candidates,
        previous_usage=previous_usage,
        previous_usage_loaded=True,
    )
    mark_stage("switch_cost_compute")
    if workflow_prediction:
        scored_candidates = workflow_rank_candidates(
            candidates=biencoder_result,
            messages=messages,
            request_options=request_options,
            predictions=workflow_prediction,
        )
    else:
        scored_candidates = budget_aware_scoring(
            user_id=body.user_id,
            route_id=body.route_id,
            messages=messages,
            route_schema=arch_result.get("route_schema") or {},
            request_options=request_options,
            candidates=biencoder_result,
            switch_cost=switch_result,
            budget_override=budget_state,
        )
    all_scored_candidates = scored_candidates
    estimated_input_tokens = estimated_request_context_input_tokens(
        messages,
        request_options,
    )
    predicted_output_token_count = (
        float(workflow_prediction["predicted_output_tokens"])
        if workflow_prediction
        else predicted_output_tokens(arch_result.get("route_schema") or {}, request_options)
    )
    scored_candidates = request_compatible_candidates(
        scored_candidates,
        request_options,
        estimated_input_tokens=estimated_input_tokens,
        predicted_output_token_count=predicted_output_token_count,
    )
    mark_stage("budget_scoring")

    if not scored_candidates:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no_request_compatible_candidates",
                "message": "No ranked route candidate supports the request capabilities within its context window",
                "requires_tools": bool(request_options.get("tools")),
                "response_format": request_options.get("response_format"),
                "estimated_input_tokens": round(estimated_input_tokens, 4),
                "predicted_output_tokens": round(predicted_output_token_count, 4),
                "required_context_tokens": round(
                    estimated_input_tokens + predicted_output_token_count,
                    4,
                ),
                "candidate_context_windows": [
                    {
                        "model_id": str(candidate.get("model_id") or ""),
                        "context_window": finite_profile_number(
                            first_present(candidate, "context_window", "model_context_window"),
                            positive=True,
                        ),
                    }
                    for candidate in biencoder_result
                ],
                "route_name": route_name,
            },
        )

    workflow_execution = None
    if workflow_run and workflow_scopes:
        workflow_execution = await asyncio.to_thread(
            reserve_workflow_call,
            user_id=body.user_id,
            route_id=body.route_id,
            run_id=str(body.workflow_run_id),
            scope_chain=workflow_scopes,
            selected_model=scored_candidates[0],
            routing_call_id=routing_call_id,
        )
        mark_stage("workflow_reservation")

    execution = await execute_selected_candidate(
        scored_candidates=scored_candidates,
        messages=messages,
        request_options=request_options,
        execute=body.execute,
    )
    mark_stage("provider_execution")
    best_model = execution["model"]
    provider_response = execution["raw_response"]
    normalized_response = execution["normalized_response"]

    spend_usd = estimate_spend_usd(best_model, messages, provider_response)
    route_latency_ms = round((time.perf_counter() - started) * 1000, 2)
    stage_timings_ms["total"] = route_latency_ms
    if body.execute:
        write_usage_log(
            user_id=body.user_id,
            route_id=body.route_id,
            route_name=route_name,
            model=best_model,
            provider=normalized_response.get("provider") or "unknown",
            success=normalized_response_succeeded(normalized_response, provider_response),
            spend_usd=spend_usd,
            metadata={
                "archrouter": arch_result,
                "switch_cost": switch_result,
                "candidate_model_pool": compact_routing_models(candidates),
                "biencoder_ranked_candidates": compact_routing_models(biencoder_result),
                "budget_ranked_candidates": compact_routing_models(scored_candidates),
                "route_worker": route_worker_result,
                "stage_timings_ms": stage_timings_ms,
                "provider_status": provider_response.get("status", "executed"),
                "provider_attempts": execution["attempts"],
                "inference_mode": inference_mode,
                "execution_owner": "lerouter",
                "latency_ms": route_latency_ms,
                "router_latency_ms": route_latency_ms,
                "routing_call_id": routing_call_id,
                "session_id": str(body.metadata.get("session_id") or "").strip() or None,
            },
        )

    return {
        "user_id": body.user_id,
        "route_id": body.route_id,
        "routing_call_id": routing_call_id,
        "inference_mode": inference_mode,
        "execution_owner": "lerouter" if body.execute else "user",
        "route_name": route_name,
        "routing_strategy": routing_strategy,
        "best_model": routing_model_without_internal_fields(best_model),
        "pipeline": {
            "archrouter": arch_result,
            "routing_strategy": routing_strategy,
            "candidate_model_pool": routing_models_without_internal_fields(candidates),
            "biencoder": routing_models_without_internal_fields(biencoder_result),
            "switch_cost_estimator": switch_result,
            "euristique_budget_manager": routing_models_without_internal_fields(
                scored_candidates
            ),
            "catalog_wide_budget_manager": (
                routing_models_without_internal_fields(all_scored_candidates)
                if catalog_wide
                else None
            ),
            "route_worker": route_worker_result,
        },
        "response": normalized_response,
        "provider_response": provider_response,
        "provider_attempts": execution["attempts"],
        "estimated_spend_usd": spend_usd,
        "latency_ms": route_latency_ms,
        "stage_timings_ms": stage_timings_ms,
        "workflow_execution": workflow_execution,
    }


@api.post("/lerouter/select")
async def lerouter_select(
    body: RouteRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    provided_fields = getattr(body, "model_fields_set", None)
    if provided_fields is None:
        provided_fields = getattr(body, "__fields_set__", set())
    if "execute" in provided_fields and body.execute is not False:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "selection_execute_mismatch",
                "message": "/lerouter/select requires execute=false",
            },
        )
    if "inference_mode" in provided_fields:
        requested_mode = normalize_inference_mode(body.inference_mode, "user_managed")
        if requested_mode != "user_managed":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "selection_inference_mode_mismatch",
                    "expected_inference_mode": "user_managed",
                    "inference_mode": requested_mode,
                },
            )
    body.execute = False
    body.inference_mode = "user_managed"
    route_result = await lerouter_route(body, authorization)
    return route_selection_response(route_result)


@api.post("/lerouter/usage-log")
async def lerouter_usage_log(
    body: RouteUsageLogRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = time.perf_counter()
    stage_started = started
    stage_timings_ms: dict[str, float] = {}

    def mark_stage(name: str) -> None:
        nonlocal stage_started
        now = time.perf_counter()
        stage_timings_ms[name] = round((now - stage_started) * 1000, 2)
        stage_started = now

    claim = verify_accounting_token(body.accounting_token)
    mark_stage("accounting_claim_verification")
    user_id = body.user_id
    if not user_id:
        raise HTTPException(status_code=422, detail="usage log requires user_id")
    route_id = body.route_id
    inference_mode = normalize_inference_mode(body.inference_mode, "user_managed")
    if inference_mode != "user_managed":
        raise HTTPException(
            status_code=422,
            detail={
                "error": "usage_log_inference_mode_mismatch",
                "expected_inference_mode": "user_managed",
                "inference_mode": inference_mode,
            },
        )
    if body.update_counters is not True:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "usage_accounting_required",
                "message": "Signed user-managed usage must update accounting counters",
            },
        )
    identity_mismatches = {
        field: {"expected": claim[field], "actual": actual}
        for field, actual in (
            ("user_id", str(user_id)),
            ("route_id", str(route_id)),
            ("route_name", str(body.route_name or "").strip()),
            ("model_id", str(body.model_id or "").strip()),
        )
        if not hmac.compare_digest(str(claim[field]), actual)
    }
    if identity_mismatches:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "accounting_claim_mismatch",
                "fields": identity_mismatches,
            },
        )
    route_name = str(body.route_name or "").strip()
    if not route_name or not body.model_id:
        raise HTTPException(status_code=422, detail="usage log requires route_name and model_id")
    selected_model = dict(claim["model_profile"])
    provider = claim["provider"]
    if body.provider and body.provider.lower() != provider.lower():
        raise HTTPException(
            status_code=422,
            detail={
                "error": "usage_provider_mismatch",
                "expected_provider": provider,
                "provider": body.provider,
            },
        )
    request_weight = claim["request_weight"]
    supplied_weights = [
        value
        for value in (
            body.request_weight,
            parse_optional_number(body.metadata.get("request_weight")),
        )
        if value is not None
    ]
    if any(not math.isclose(float(value), request_weight, rel_tol=0.0, abs_tol=1e-12) for value in supplied_weights):
        raise HTTPException(
            status_code=422,
            detail={"error": "accounting_request_weight_mismatch"},
        )
    supplied_routing_call_id = str(body.metadata.get("routing_call_id") or "").strip()
    if supplied_routing_call_id and not hmac.compare_digest(
        supplied_routing_call_id,
        claim["routing_call_id"],
    ):
        raise HTTPException(
            status_code=422,
            detail={"error": "accounting_routing_call_id_mismatch"},
        )
    usage_metadata = dict(body.metadata)
    usage_metadata.pop("accounting_token", None)
    usage_metadata["routing_call_id"] = claim["routing_call_id"]
    usage_metadata["request_weight"] = request_weight
    verified_spend_usd = verified_usage_spend_usd(
        model_profile=selected_model,
        metadata=usage_metadata,
        success=body.success,
        supplied_spend_usd=body.spend_usd,
    )
    mark_stage("usage_validation")
    model = {
        **selected_model,
        "request_weight": request_weight,
        "budget_result": body.metadata.get("budget_result")
        if isinstance(body.metadata.get("budget_result"), dict)
        else {},
    }
    request_weight_debit = accounting_request_weight_debit(claim)
    is_signed_rank_two_retry = request_weight_debit == 0.0
    usage_metadata["attempt_rank"] = 2 if is_signed_rank_two_retry else 1
    if claim.get("v") == WORKFLOW_ACCOUNTING_TOKEN_VERSION:
        reservation = finalize_workflow_reservation(
            claim=claim,
            spend_usd=verified_spend_usd,
            outcome="settled",
        )
        workflow_logs = require_mongo_collection("LEROUTER_WORKFLOW_USAGE_COLLECTION", "workflow_route_usage_logs")
        now = datetime.now(timezone.utc)
        workflow_logs.update_one(
            {"userId": user_id, "routeId": route_id, "reservationId": claim["reservation_id"]},
            {"$setOnInsert": {"id": str(uuid.uuid4()), "userId": user_id, "routeId": route_id, "runId": claim["workflow_run_id"], "scopeId": claim["budget_scope_id"], "reservationId": claim["reservation_id"], "routingCallId": claim["routing_call_id"], "routeName": route_name, "modelId": claim["model_id"], "provider": provider, "success": body.success, "spendUsd": verified_spend_usd, "usage": usage_metadata.get("usage") or {}, "createdAt": now}},
            upsert=True,
        )
        mark_stage("workflow_accounting_transaction")
        stage_timings_ms["total"] = round((time.perf_counter() - started) * 1000, 2)
        return {
            "ok": True,
            "routing_call_id": claim["routing_call_id"],
            "workflow_run_id": claim["workflow_run_id"],
            "budget_scope_id": claim["budget_scope_id"],
            "reservation_id": reservation["id"],
            "status": reservation["status"],
            "spend_usd": reservation["spendUsd"],
            "stage_timings_ms": stage_timings_ms,
        }
    usage_document = write_usage_log(
        user_id=user_id,
        route_id=route_id,
        route_name=route_name,
        model=model,
        provider=provider,
        success=body.success,
        spend_usd=verified_spend_usd,
        metadata={
            **usage_metadata,
            "source": usage_metadata.get("source") or "hermes_native_adapter",
            "inference_mode": inference_mode,
            "execution_owner": "user" if inference_mode == "user_managed" else "lerouter",
        },
        update_counters=body.update_counters,
        # Both candidates implement one semantic request. Each attempt debits
        # its real dollar spend, but only rank 1 consumes the request-weight
        # allocation. The rank-2 identity is server-issued and HMAC-signed.
        request_weight_debit=request_weight_debit,
    )
    mark_stage("mongo_accounting_transaction")
    authoritative_usage_log = usage_log_response_from_document(usage_document)
    stage_timings_ms["total"] = round((time.perf_counter() - started) * 1000, 2)
    return {
        "ok": True,
        "routing_call_id": usage_document.get("routingCallId"),
        "budget_remaining_usd": usage_document.get("budgetRemainingUsd"),
        "remaining_weight": usage_document.get("remainingWeight"),
        "usage_log": authoritative_usage_log,
        "stage_timings_ms": stage_timings_ms,
        "mongo_stage_timings_ms": usage_document.get("_stage_timings_ms"),
    }


@api.post("/v1/chat/completions")
async def openai_chat_completions(
    body: OpenAIChatCompletionRequest,
    authorization: str | None = Header(default=None),
    x_lerouter_user_id: str | None = Header(default=None),
    x_lerouter_route_id: str | None = Header(default=None),
) -> Any:
    if not isinstance(x_lerouter_user_id, str):
        x_lerouter_user_id = None
    if not isinstance(x_lerouter_route_id, str):
        x_lerouter_route_id = None
    auth_context = require_agent_access(authorization)

    metadata = body.metadata or {}
    budget = metadata.get("budget") if isinstance(metadata.get("budget"), dict) else None
    user_id = auth_user_id(
        auth_context,
        x_lerouter_user_id or body.user or str(metadata.get("user_id") or "hermes"),
    )
    route_id = auth_route_id(
        auth_context,
        x_lerouter_route_id or str(metadata.get("route_id") or "default"),
    )
    route_request = RouteRequest(
        user_id=user_id or "hermes",
        route_id=route_id,
        messages=body.messages,
        tools=body.tools,
        tool_choice=body.tool_choice,
        response_format=body.response_format,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        stream=False,
        previous_query_data=metadata.get("previous_query_data"),
        budget=budget,
        provider_options={},
        metadata=metadata,
        execute=True,
        inference_mode="router_managed",
    )
    write_request_started_log(
        user_id=user_id or "hermes",
        route_id=route_id,
        metadata={
            "source": "openai_chat_completions",
            "stream": body.stream,
            "model": body.model,
            "message_count": len(body.messages),
            "inference_mode": "router_managed",
            "execution_owner": "lerouter",
        },
    )
    if body.stream:
        route_task = asyncio.create_task(lerouter_route(route_request, authorization))
        return StreamingResponse(
            stream_openai_compatible_response(
                request_model=body.model,
                route_task=route_task,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    route_result = await lerouter_route(route_request, authorization)
    return openai_compatible_response(request_model=body.model, route_result=route_result)


if modal:
    modal_image = modal.Image.debian_slim(python_version="3.11").pip_install(
        "fastapi",
        "pydantic",
        "modal",
        "pymongo",
        "certifi",
        "httpx",
    )
    routing_worker_endpoint = os.environ.get("LEROUTER_ROUTING_WORKER_URL", "").strip()
    length_predictor_endpoint = LENGTH_PREDICTOR_URL
    modal_runtime_env = {}
    if routing_worker_endpoint:
        modal_runtime_env["LEROUTER_ROUTING_WORKER_URL"] = routing_worker_endpoint
    if length_predictor_endpoint:
        modal_runtime_env["LEROUTER_LENGTH_PREDICTOR_URL"] = length_predictor_endpoint
    if modal_runtime_env:
        modal_image = modal_image.env(modal_runtime_env)
    modal_image = modal_image.add_local_file(
        Path(__file__).resolve().parent / "workflow_budget.py",
        remote_path="/root/workflow_budget.py",
    )
    modal_volume = modal.Volume.from_name("lerouter-data", create_if_missing=True)
    modal_app = modal.App("lerouter-api")
    app = modal_app
    modal_function_options = {
        "image": modal_image,
        "volumes": {"/data": modal_volume},
        "min_containers": 1,
    }
    modal_secret_names = [
        name.strip()
        for name in os.environ.get(
            "LEROUTER_MODAL_SECRET_NAMES",
            os.environ.get(
                "LEROUTER_MODAL_SECRET_NAME",
                "lerouter-mongodb,lerouter-routing-urls,together-ai-api-key,openrouter-api-key,anthropic-api-key,openai-api-key,lerouter-internal-service-token",
            ),
        ).split(",")
        if name.strip()
    ]
    if modal_secret_names:
        modal_function_options["secrets"] = [
            modal.Secret.from_name(name)
            for name in modal_secret_names
        ]

    @modal_app.function(**modal_function_options)
    @modal.asgi_app()
    def fastapi_app():
        return api

    candidate_job_function_options = {
        **modal_function_options,
        "min_containers": 0,
    }

    @modal_app.function(**candidate_job_function_options, timeout=60 * 60 * 4)
    async def run_candidate_selection_job(job: dict[str, Any]):
        return await process_candidate_selection_job(str(job.get("id")), initial_job=job)
