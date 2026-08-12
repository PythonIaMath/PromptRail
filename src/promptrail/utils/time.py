"""Clock helpers for runtime telemetry."""

from __future__ import annotations

import time


def epoch_ms() -> int:
    return time.time_ns() // 1_000_000


def monotonic_ms() -> int:
    return time.monotonic_ns() // 1_000_000
