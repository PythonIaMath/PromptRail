"""Correlate parallel agent work under one PromptRail runtime run."""

from __future__ import annotations

import concurrent.futures
import os

from promptrail import PromptRail, current_runtime_context, event, run


def worker(name: str) -> str:
    ctx = current_runtime_context()
    event("parallel.worker.started", {"worker": name, "run_id": getattr(ctx, "run_id", None)})
    result = f"{name}:complete"
    event("parallel.worker.completed", {"worker": name})
    return result


def main() -> None:
    rail = PromptRail(project=os.environ.get("PROMPTRAIL_PROJECT", "examples"))
    workers = os.environ.get("EXAMPLE_WORKERS", "research,review,write").split(",")

    with run("parallel-agents", runtime=rail, metadata={"example": "parallel_agents"}):
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as pool:
            results = list(pool.map(worker, workers))
        event("parallel.all_completed", {"count": len(results)})
        print(results)


if __name__ == "__main__":
    main()
