"""Modal-only bridge for retrieving the demo's internal service credential."""

from __future__ import annotations

import base64
import os

import modal

app = modal.App("promptrail-demo-credential-bridge")


@app.function(secrets=[modal.Secret.from_name("lerouter-internal-service-token")])
def internal_service_token() -> str:
    token = os.environ.get("LEROUTER_INTERNAL_SERVICE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Modal secret omitted LEROUTER_INTERNAL_SERVICE_TOKEN")
    return token


@app.local_entrypoint()
def main() -> None:
    token = internal_service_token.remote()
    encoded = base64.b64encode(token.encode()).decode()
    print(f"PROMPTRAIL_MODAL_TOKEN_B64={encoded}")
