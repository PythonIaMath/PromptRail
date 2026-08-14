"""Benchmark PromptRail's real control plane; never substitutes local fake services."""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import statistics
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    from .backend import (
        DemoService,
        EventLedger,
        _usage_fields,
        extract_completed_response,
        should_reroute_provider_error,
    )
    from .credentials import ensure_internal_service_token
except ImportError:  # Direct script execution from the repository root.
    from backend import (
        DemoService,
        EventLedger,
        _usage_fields,
        extract_completed_response,
        should_reroute_provider_error,
    )
    from credentials import ensure_internal_service_token

ROOT = Path(__file__).resolve().parent
REQUIRED_ENV = (
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "MONGODB_URI",
    "LEROUTER_INTERNAL_SERVICE_TOKEN",
)

SCENARIOS = (
    ("short_chat", "What's up?", ()),
    ("repository_overview", "What is this codebase? Inspect it before answering.", ()),
    ("debugging", "Find why the managed agent loops after a tool result and explain the fix.", ()),
    ("implementation", "Implement a small validated change and run the relevant tests.", ()),
    (
        "test_failure",
        "Analyze this failed test, preserve the error, and propose the next action.",
        (),
    ),
    ("long_context", "Summarize the architecture while preserving unresolved constraints.", ()),
)

MULTITURN_MARKER = "PROMPTRAIL-MULTITURN-7429"
MULTITURN_PROMPTS = (
    (
        f"Remember this exact marker for later turns: {MULTITURN_MARKER}. "
        "Reply briefly that you stored it."
    ),
    "What exact marker did I ask you to remember? Return it verbatim.",
    (
        "Using our conversation so far, explain in two sentences why preserving "
        "relevant multi-turn context matters. Include the exact marker once."
    ),
    "Repeat the exact marker one final time without asking me to provide it again.",
)


def write_progress(
    path: Path | None,
    *,
    stage: str,
    completed: int,
    total: int,
    detail: str,
) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "time": time.time(),
        "stage": stage,
        "completed": completed,
        "total": total,
        "detail": detail,
    }
    with path.open("a", encoding="utf-8") as destination:
        destination.write(json.dumps(record, separators=(",", ":")) + "\n")


class ProviderBenchmarkHTTPError(RuntimeError):
    def __init__(self, status: int, detail: str, retry_after_seconds: int | None) -> None:
        super().__init__(f"provider benchmark HTTP {status}: {detail}")
        self.status = status
        self.retry_after_seconds = retry_after_seconds


def load_env(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def request_body(prompt: str, scenario: str) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    if scenario == "long_context":
        history = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": (
                    f"Historical turn {index}: preserve budget, routing, cache, and "
                    "compaction constraints. " + "context evidence " * 180
                ),
            }
            for index in range(12)
        ]
    elif scenario == "test_failure":
        history = [
            {
                "role": "user",
                "content": (
                    "Here is the earlier test output to preserve as conversation evidence:\n"
                    + "FAILED test_router.py::test_budget\n"
                    "AssertionError: route rejected\n" * 80
                ),
            }
        ]
    return {
        "model": "openai/gpt-5.6-sol",
        "input": [*history, {"role": "user", "content": prompt}],
        "tools": [
            {
                "type": "function",
                "name": "shell",
                "description": "Run a shell command",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                    "additionalProperties": False,
                },
            }
        ],
        "stream": True,
    }


def multiturn_body(history: list[dict[str, Any]], prompt: str) -> dict[str, Any]:
    return {
        "model": "openai/gpt-5.6-sol",
        "input": [*history, {"role": "user", "content": prompt}],
        "stream": True,
    }


def assistant_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in response.get("output") or ():
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or ():
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts).strip()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(samples: list[dict[str, Any]], fields: Iterable[str]) -> dict[str, Any]:
    if not samples:
        return {}
    result: dict[str, Any] = {}
    for field in fields:
        values = [float(sample[field]) for sample in samples]
        result[field] = {
            "p50": round(statistics.median(values), 3),
            "p95": round(percentile(values, 0.95), 3),
            "max": round(max(values), 3),
        }
    return result


def provider_call(service: DemoService, payload: dict[str, Any]) -> dict[str, Any]:
    host, path, key, forwarded, provider = service.upstream("managed", payload)
    connection = http.client.HTTPSConnection(host, timeout=600)
    data = json.dumps(forwarded, separators=(",", ":")).encode()
    started = time.perf_counter()
    connection.request(
        "POST",
        path,
        body=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Content-Length": str(len(data)),
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://promptrail.ai",
            "X-Title": "PromptRail latency benchmark",
        },
    )
    response = connection.getresponse()
    if response.status >= 400:
        raw_retry_after = response.getheader("retry-after")
        retry_after = (
            int(raw_retry_after)
            if raw_retry_after is not None and raw_retry_after.isdigit()
            else None
        )
        detail = response.read(3_000).decode(errors="replace")
        status = response.status
        connection.close()
        raise ProviderBenchmarkHTTPError(status, detail, retry_after)
    captured = bytearray()
    first_chunk_at: float | None = None
    while chunk := response.read(4_096):
        if first_chunk_at is None:
            first_chunk_at = time.perf_counter()
        captured.extend(chunk)
    completed_at = time.perf_counter()
    content_type = response.getheader("content-type", "application/json")
    connection.close()
    completed = extract_completed_response(bytes(captured), content_type)
    if first_chunk_at is None or completed is None:
        raise RuntimeError("provider benchmark returned no completed streamed response")
    return {
        "provider": provider,
        "ttft_ms": (first_chunk_at - started) * 1_000,
        "total_ms": (completed_at - started) * 1_000,
        "response": completed,
    }


def provider_call_with_failover(
    service: DemoService,
    original: dict[str, Any],
    payload: dict[str, Any],
    prepared: Any,
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """Execute a managed call, cascading across ranked paid models on provider errors.

    This function owns the active reservation while it runs. On success ownership of
    the returned reservation passes back to the caller; on every exception it resolves
    the current reservation before propagating the error.
    """

    del original
    started = time.perf_counter()
    attempt = 1
    attempted_models: set[str] = set()
    current_payload = payload
    current_prepared = prepared
    try:
        while True:
            try:
                result = provider_call(service, current_payload)
                return result, current_payload, current_prepared
            except ProviderBenchmarkHTTPError as error:
                unavailable_models = service.ranker.rate_limited_models()
                available_alternatives = sum(
                    item.candidate.model_id not in unavailable_models
                    for item in current_prepared.model_alternatives
                )
                if not should_reroute_provider_error(
                    "managed",
                    error.status,
                    current_prepared,
                    attempt,
                    attempt + available_alternatives,
                ):
                    service.fail_upstream(
                        current_prepared,
                        error.status,
                        error.retry_after_seconds,
                    )
                    current_prepared = None
                    raise
                previous_model = current_prepared.model.route.native_model_id
                attempted_models.add(current_prepared.model.candidate.model_id)
                current_payload, current_prepared = service.reroute_upstream(
                    current_payload,
                    current_prepared,
                    error.status,
                    error.retry_after_seconds,
                    frozenset(attempted_models),
                    math.ceil((time.perf_counter() - started) * 1_000),
                )
                attempt += 1
                service.ledger.record(
                    "managed",
                    "provider_error_reroute",
                    previous_model=previous_model,
                    model=current_prepared.model.route.native_model_id,
                    status=error.status,
                    attempt=attempt,
                    benchmark=True,
                )
    except BaseException:
        if current_prepared is not None:
            service.managed.gateway.fail_model(
                prepared=current_prepared,
                billing_unknown=False,
            )
        raise


def prepare_once(
    service: DemoService,
    scenario: str,
    prompt: str,
    *,
    execute_provider: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    original = request_body(prompt, scenario)
    forwarded, prepared = service.prepare("managed", original)
    observed_ms = (time.perf_counter() - started) * 1_000
    reservation_resolved = False
    try:
        sample = {
            "record_type": "sample",
            "scenario": scenario,
            "observed_control_plane_ms": round(observed_ms, 3),
            "output_prediction_ms": prepared.output_prediction_ms,
            "context_analysis_ms": prepared.context_analysis_ms,
            "gemma_allocation_ms": prepared.gemma_allocation_ms,
            "semantic_ranking_ms": prepared.semantic_ranking_ms,
            "candidate_feasibility_ms": prepared.candidate_feasibility_ms,
            "compaction_ms": prepared.compaction_ms,
            "provider_planning_ms": prepared.provider_planning_ms,
            "control_plane_total_ms": prepared.control_plane_total_ms,
            "predicted_output_tokens": prepared.predicted_output_tokens,
            "required_context_tokens": prepared.budget.required_context_tokens,
            "input_tokens": prepared.cache.total_tokens,
            "selected_model": prepared.model.candidate.model_id,
            "selected_provider": prepared.model.route.provider,
            "predictor_on_critical_path": (
                prepared.output_prediction_ms
                >= max(prepared.context_analysis_ms, prepared.semantic_ranking_ms)
            ),
        }
        if execute_provider:
            reservation_resolved = True
            pass_through, forwarded, prepared = provider_call_with_failover(
                service,
                original,
                forwarded,
                prepared,
            )
            reservation_resolved = False
            reservation_resolved = True
            managed, forwarded, prepared = provider_call_with_failover(
                service,
                original,
                forwarded,
                prepared,
            )
            reservation_resolved = False
            sample["selected_model"] = prepared.model.candidate.model_id
            sample["selected_provider"] = prepared.model.route.provider
            _, actual_output_tokens, _, _ = _usage_fields(managed["response"].get("usage"))
            sample.update(
                {
                    "pass_through_ttft_ms": round(pass_through["ttft_ms"], 3),
                    "managed_provider_ttft_ms": round(managed["ttft_ms"], 3),
                    "managed_end_to_end_ttft_ms": round(observed_ms + managed["ttft_ms"], 3),
                    "ttft_overhead_ms": round(
                        observed_ms + managed["ttft_ms"] - pass_through["ttft_ms"], 3
                    ),
                    "actual_output_tokens": actual_output_tokens,
                    "prediction_absolute_error_tokens": abs(
                        prepared.predicted_output_tokens - actual_output_tokens
                    ),
                    "prediction_ape": (
                        abs(prepared.predicted_output_tokens - actual_output_tokens)
                        / max(1, actual_output_tokens)
                    ),
                    "prediction_under": prepared.predicted_output_tokens < actual_output_tokens,
                }
            )
            service.settle(
                "managed",
                prepared,
                managed["response"],
                math.ceil(managed["total_ms"]),
                managed["provider"],
                str(forwarded.get("model") or ""),
                math.ceil(managed["ttft_ms"]),
                math.ceil(observed_ms + managed["ttft_ms"]),
            )
            reservation_resolved = True
        return sample
    finally:
        if not reservation_resolved:
            service.managed.gateway.fail_model(prepared=prepared, billing_unknown=False)


def run_multiturn(
    service: DemoService, turn_count: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if turn_count < 2 or turn_count > len(MULTITURN_PROMPTS):
        raise ValueError(f"multiturn turn count must be between 2 and {len(MULTITURN_PROMPTS)}")
    history: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for turn, prompt in enumerate(MULTITURN_PROMPTS[:turn_count], start=1):
        started = time.perf_counter()
        original = multiturn_body(history, prompt)
        forwarded, prepared = service.prepare("managed", original)
        control_ms = (time.perf_counter() - started) * 1_000
        reservation_resolved = False
        try:
            reservation_resolved = True
            provider, forwarded, prepared = provider_call_with_failover(
                service,
                original,
                forwarded,
                prepared,
            )
            reservation_resolved = False
            response = provider["response"]
            answer = assistant_text(response)
            if not answer:
                raise RuntimeError(f"multi-turn provider response {turn} contained no answer")
            input_tokens, output_tokens, cached_tokens, cost = _usage_fields(response.get("usage"))
            service.settle(
                "managed",
                prepared,
                response,
                math.ceil(provider["total_ms"]),
                provider["provider"],
                str(forwarded.get("model") or ""),
                math.ceil(provider["ttft_ms"]),
                math.ceil(control_ms + provider["ttft_ms"]),
            )
            reservation_resolved = True
            history.extend(
                (
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": answer},
                )
            )
            records.append(
                {
                    "record_type": "multiturn_sample",
                    "turn": turn,
                    "history_messages": len(history),
                    "continuity_marker_present": MULTITURN_MARKER in answer,
                    "selected_model": prepared.model.candidate.model_id,
                    "selected_provider": prepared.model.route.provider,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cached_tokens": cached_tokens,
                    "cost_usd": cost,
                    "predicted_output_tokens": prepared.predicted_output_tokens,
                    "required_context_tokens": prepared.budget.required_context_tokens,
                    "compacted_tokens": (
                        prepared.compaction.tokens_before - prepared.compaction.tokens_after
                    ),
                    "control_plane_ms": round(control_ms, 3),
                    "provider_ttft_ms": round(provider["ttft_ms"], 3),
                    "end_to_end_ttft_ms": round(control_ms + provider["ttft_ms"], 3),
                }
            )
        finally:
            if not reservation_resolved:
                service.managed.gateway.fail_model(prepared=prepared, billing_unknown=False)
    continuity_turns = records[1:]
    continuity_verified = all(record["continuity_marker_present"] for record in continuity_turns)
    if not continuity_verified:
        missing = [
            str(record["turn"])
            for record in continuity_turns
            if not record["continuity_marker_present"]
        ]
        raise RuntimeError(
            "multi-turn continuity failed; marker missing from turn(s): " + ", ".join(missing)
        )
    return records, {
        "record_type": "multiturn_summary",
        "turns": turn_count,
        "continuity_verified": True,
        "models": [record["selected_model"] for record in records],
        "total_cost_usd": sum(float(record["cost_usd"]) for record in records),
        "total_cached_tokens": sum(int(record["cached_tokens"]) for record in records),
        "final_history_messages": len(history),
    }


def service(root: Path, ledger: EventLedger) -> DemoService:
    return DemoService(
        root,
        os.environ["OPENAI_API_KEY"],
        os.environ["OPENROUTER_API_KEY"],
        os.environ["MONGODB_URI"],
        ledger,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--warm-samples", type=int, default=30)
    parser.add_argument("--cold-samples", type=int, default=3)
    parser.add_argument("--provider-samples", type=int, default=6)
    parser.add_argument("--multiturn-turns", type=int, default=4)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--progress-file", type=Path)
    args = parser.parse_args()
    load_env(args.env_file.expanduser().resolve())
    ensure_internal_service_token()
    missing = [key for key in REQUIRED_ENV if not os.environ.get(key, "").strip()]
    if missing:
        raise SystemExit(
            "real benchmark refused: missing required environment variables: " + ", ".join(missing)
        )
    output = args.output or (
        ROOT / "benchmarks" / f"control-plane-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    ledger = EventLedger(output.with_suffix(".events.jsonl"))
    samples: list[dict[str, Any]] = []
    multiturn_records: list[dict[str, Any]] = []
    progress_file = args.progress_file.expanduser().resolve() if args.progress_file else None
    total_steps = 1 + args.warm_samples + 1 + args.cold_samples
    completed_steps = 0
    write_progress(
        progress_file,
        stage="starting",
        completed=completed_steps,
        total=total_steps,
        detail="Connecting to the real PromptRail control-plane services.",
    )

    warm_service = service(ROOT, ledger)
    try:
        # One unreported warm-up establishes persistent HTTP/TLS connections.
        prepare_once(warm_service, *SCENARIOS[0][:2])
        completed_steps += 1
        write_progress(
            progress_file,
            stage="warm",
            completed=completed_steps,
            total=total_steps,
            detail="Connections warmed; measuring reused sessions.",
        )
        for index in range(args.warm_samples):
            scenario, prompt, _ = SCENARIOS[index % len(SCENARIOS)]
            sample = prepare_once(
                warm_service,
                scenario,
                prompt,
                execute_provider=index < args.provider_samples,
            )
            sample["temperature"] = "warm"
            samples.append(sample)
            completed_steps += 1
            write_progress(
                progress_file,
                stage="warm",
                completed=completed_steps,
                total=total_steps,
                detail=f"Warm sample {index + 1} of {args.warm_samples}: {scenario}.",
            )
    finally:
        warm_service.close()

    multiturn_service = service(ROOT, ledger)
    try:
        write_progress(
            progress_file,
            stage="multiturn",
            completed=completed_steps,
            total=total_steps,
            detail=f"Verifying continuity across {args.multiturn_turns} real turns.",
        )
        multiturn_samples, multiturn_summary = run_multiturn(
            multiturn_service, args.multiturn_turns
        )
        multiturn_records.extend((*multiturn_samples, multiturn_summary))
        completed_steps += 1
        write_progress(
            progress_file,
            stage="cold",
            completed=completed_steps,
            total=total_steps,
            detail="Multi-turn continuity verified; measuring cold starts.",
        )
    finally:
        multiturn_service.close()

    for index in range(args.cold_samples):
        cold_service = service(ROOT, ledger)
        try:
            scenario, prompt, _ = SCENARIOS[index % len(SCENARIOS)]
            sample = prepare_once(cold_service, scenario, prompt)
            sample["temperature"] = "cold"
            samples.append(sample)
            completed_steps += 1
            write_progress(
                progress_file,
                stage="cold",
                completed=completed_steps,
                total=total_steps,
                detail=f"Cold sample {index + 1} of {args.cold_samples} complete.",
            )
        finally:
            cold_service.close()

    fields = (
        "output_prediction_ms",
        "context_analysis_ms",
        "gemma_allocation_ms",
        "semantic_ranking_ms",
        "candidate_feasibility_ms",
        "compaction_ms",
        "provider_planning_ms",
        "control_plane_total_ms",
        "observed_control_plane_ms",
    )
    warm = [sample for sample in samples if sample["temperature"] == "warm"]
    cold = [sample for sample in samples if sample["temperature"] == "cold"]
    summary = {
        "record_type": "summary",
        "real_services_only": True,
        "warm_samples": len(warm),
        "cold_samples": len(cold),
        "target_warm_control_plane_p95_ms": 1_000,
        "target_met": percentile(
            [float(sample["observed_control_plane_ms"]) for sample in warm], 0.95
        )
        < 1_000,
        "warm": summarize(warm, fields),
        "cold": summarize(cold, fields),
        "multiturn": multiturn_summary,
    }
    provider_samples = [sample for sample in warm if "ttft_overhead_ms" in sample]
    if provider_samples:
        summary["matched_provider"] = summarize(
            provider_samples,
            (
                "pass_through_ttft_ms",
                "managed_provider_ttft_ms",
                "managed_end_to_end_ttft_ms",
                "ttft_overhead_ms",
                "prediction_absolute_error_tokens",
                "prediction_ape",
            ),
        )
        summary["prediction_under_rate"] = sum(
            bool(sample["prediction_under"]) for sample in provider_samples
        ) / len(provider_samples)
    with output.open("w", encoding="utf-8") as destination:
        for record in [*samples, *multiturn_records, summary]:
            destination.write(json.dumps(record, separators=(",", ":")) + "\n")
    write_progress(
        progress_file,
        stage="complete",
        completed=total_steps,
        total=total_steps,
        detail="Benchmark complete. Results are provider-reported and real-service only.",
    )
    print(json.dumps({"report": str(output), **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
