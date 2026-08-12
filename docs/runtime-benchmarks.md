# PromptRail Runtime SDK performance

The Runtime SDK hot path performs no synchronous network I/O. The benchmark exercises local context lookup, canonical event creation, non-blocking queue insertion, gateway header injection, and OpenTelemetry span processing.

## Reproduce

```bash
uv run pytest -q -s tests/performance/test_runtime_overhead.py
```

The test executes 1,500 timed iterations per operation, reports median and P99 wall-clock duration, and applies a generous cross-CI safety assertion of P99 below 5 ms. The product target remains P50 below 100 microseconds and P99 below 1 ms for local event processing.

## Measured result

Measured 2026-08-12 on macOS aarch64 with CPython 3.11.15. Export was disabled, no network request was made, and the repository working tree contained the complete Runtime SDK integration changes.

| Operation | Median | P99 | Product target |
| --- | ---: | ---: | --- |
| Context lookup | 0.0018 ms | 0.0022 ms | P50 < 0.100 ms, P99 < 1.000 ms |
| Event creation | 0.0051 ms | 0.0063 ms | P50 < 0.100 ms, P99 < 1.000 ms |
| Queue insertion | 0.0035 ms | 0.0051 ms | P50 < 0.100 ms, P99 < 1.000 ms |
| Header injection | 0.0058 ms | 0.0070 ms | Typical < 0.100 ms |
| Span processing | 0.0038 ms | 0.0073 ms | P50 < 0.100 ms, P99 < 1.000 ms |

All measured local paths are comfortably below the requested targets on this machine. These numbers are not network or end-to-end inference benchmarks. Re-run on deployment runtimes and track changes across SDK releases.
