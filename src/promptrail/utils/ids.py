"""Runtime-safe identifiers."""

from __future__ import annotations

import secrets
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_crockford(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def secure_id(prefix: str) -> str:
    """Return a UUID-like, prefixed, non-sequential secure ID."""
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError("prefix must be non-empty alphanumeric/underscore")
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    random_bits = secrets.randbits(80)
    return f"{prefix}_{_encode_crockford(timestamp_ms, 10)}{_encode_crockford(random_bits, 16)}"
