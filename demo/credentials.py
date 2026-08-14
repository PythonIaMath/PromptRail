"""Resolve demo-only service credentials without writing them to disk or logs."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path


def ensure_internal_service_token() -> None:
    if os.environ.get("LEROUTER_INTERNAL_SERVICE_TOKEN", "").strip():
        return
    modal_cli = shutil.which("modal")
    if modal_cli is None:
        raise RuntimeError(
            "LEROUTER_INTERNAL_SERVICE_TOKEN is missing and the Modal CLI is unavailable"
        )
    bridge = Path(__file__).with_name("modal_secret_bridge.py")
    result = subprocess.run(
        [modal_cli, "run", str(bridge)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "could not resolve lerouter-internal-service-token from the active Modal profile"
        )
    prefix = "PROMPTRAIL_MODAL_TOKEN_B64="
    encoded = next(
        (
            line.removeprefix(prefix).strip()
            for line in result.stdout.splitlines()
            if line.startswith(prefix)
        ),
        "",
    )
    try:
        token = base64.b64decode(encoded, validate=True).decode().strip()
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError("Modal returned an invalid internal service credential") from error
    if not token or len(token) > 4_096 or any(ord(character) < 32 for character in token):
        raise RuntimeError("Modal returned an invalid internal service credential")
    os.environ["LEROUTER_INTERNAL_SERVICE_TOKEN"] = token
