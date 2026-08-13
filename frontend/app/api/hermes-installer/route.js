import { readFile } from "node:fs/promises";
import path from "node:path";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const TEMPLATE_DIR = path.join(process.cwd(), "app", "lib", "hermes-installer");

function bootstrapScript(requestUrl) {
  const origin = new URL(requestUrl).origin;
  return `#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import py_compile
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("LEROUTER_INSTALLER_URL", "${origin}/api/hermes-installer").rstrip("/")
BACKGROUND = os.environ.get("LEROUTER_INSTALL_BACKGROUND", "").strip().lower() in {"1", "true", "yes", "on"}
REQUIRED = ["LEROUTER_API_URL", "LEROUTER_AGENT_TOKEN"]
missing = [name for name in REQUIRED if not os.environ.get(name)]
if missing:
    print("Missing required env vars: " + ", ".join(missing), file=sys.stderr)
    print("Set LEROUTER_API_URL, LEROUTER_AGENT_TOKEN, LEROUTER_ROUTE_ID, and LEROUTER_DASHBOARD_URL, then rerun.", file=sys.stderr)
    sys.exit(2)

home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
plugin_dir = home / "plugins" / "lerouter-user-managed"
scripts_dir = home / "scripts"
state_dir = home / "lerouter-user-managed"
plugin_dir.mkdir(parents=True, exist_ok=True)
scripts_dir.mkdir(parents=True, exist_ok=True)
state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
state_dir.chmod(0o700)

def fetch(kind: str) -> str:
    with urllib.request.urlopen(f"{BASE_URL}?file={kind}", timeout=60) as response:
        return response.read().decode("utf-8")

(plugin_dir / "plugin.yaml").write_text(fetch("plugin.yaml"), encoding="utf-8")
(plugin_dir / "__init__.py").write_text(fetch("plugin.py"), encoding="utf-8")
setup_path = scripts_dir / "lerouter_setup_user_managed.py"
setup_path.write_text(fetch("setup.py"), encoding="utf-8")

py_compile.compile(str(plugin_dir / "__init__.py"), doraise=True)
py_compile.compile(str(setup_path), doraise=True)
if BACKGROUND:
    log_path = state_dir / "setup.log"
    pid_path = state_dir / "setup.pid"
    run_path = state_dir / "setup-run.json"
    events_path = state_dir / "events.jsonl"
    state_path = state_dir / "state.json"
    try:
        existing_pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        existing_pid = None
    if existing_pid is not None:
        try:
            os.kill(existing_pid, 0)
        except OSError:
            existing_pid = None
    if existing_pid is not None:
        proc_cmdline = Path(f"/proc/{existing_pid}/cmdline")
        if proc_cmdline.exists():
            try:
                if str(setup_path).encode() not in proc_cmdline.read_bytes():
                    existing_pid = None
            except OSError:
                existing_pid = None
    if existing_pid is not None:
        try:
            run_info = json.loads(run_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            run_info = {}
        print(json.dumps({
            "started": False,
            "already_running": True,
            "pid": existing_pid,
            "pid_path": str(pid_path),
            "log_path": str(log_path),
            "events_path": str(events_path),
            "state_path": str(state_path),
            "run_path": str(run_path),
            "started_at": run_info.get("started_at"),
            "events_offset": run_info.get("events_offset"),
        }))
        sys.exit(0)
    started_at = datetime.now(timezone.utc).isoformat()
    events_offset = events_path.stat().st_size if events_path.exists() else 0
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    log_handle = os.fdopen(log_fd, "a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, str(setup_path)],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_handle.close()
    pid_path.write_text(str(process.pid) + "\\n", encoding="utf-8")
    pid_path.chmod(0o600)
    run_info = {
        "started": True,
        "already_running": False,
        "pid": process.pid,
        "pid_path": str(pid_path),
        "log_path": str(log_path),
        "events_path": str(events_path),
        "state_path": str(state_path),
        "run_path": str(run_path),
        "started_at": started_at,
        "events_offset": events_offset,
    }
    run_path.write_text(json.dumps(run_info) + "\\n", encoding="utf-8")
    run_path.chmod(0o600)
    print(json.dumps(run_info))
    sys.exit(0)
result = subprocess.run([sys.executable, str(setup_path)], text=True)
sys.exit(result.returncode)
`;
}

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const file = searchParams.get("file") || "bootstrap.py";
  const allowed = new Map([
    ["bootstrap.py", null],
    ["plugin.py", "plugin.py"],
    ["setup.py", "setup.py"],
    ["plugin.yaml", "plugin.yaml"],
  ]);

  if (!allowed.has(file)) {
    return new Response("Unknown installer file.\n", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });
  }

  let body;
  if (file === "bootstrap.py") {
    body = bootstrapScript(request.url);
  } else {
    body = await readFile(path.join(TEMPLATE_DIR, allowed.get(file)), "utf8");
  }

  return new Response(body, {
    headers: {
      "content-type": file.endsWith(".yaml") ? "application/yaml; charset=utf-8" : "text/x-python; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}
