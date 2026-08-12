from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_runtime_sdk_public_acceptance_workflow() -> None:
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    source_path = str(root / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (source_path, environment.get("PYTHONPATH")) if value
    )
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("_runtime_sdk_acceptance_app.py"))],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report == {
        "concurrent_users": 100,
        "events": 633,
        "gateway_requests": 107,
        "status": "accepted",
    }
