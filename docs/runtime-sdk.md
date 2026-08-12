# PromptRail Runtime SDK public API guide

The runtime SDK lets applications correlate model, agent, and workflow activity
with a PromptRail run. The APIs below are intended for observability and runtime
correlation only. They do not optimize prompts, cache client requests, or alter
client-side model behavior.

## Install

Use the normal project install for PromptRail. Optional integrations, such as
OpenAI or OpenTelemetry, should be installed by the application that uses them.

```bash
pip install promptrail
pip install openai              # optional, only for OpenAI examples
pip install opentelemetry-api   # optional, only for OpenTelemetry examples
```

## Environment variables

Examples avoid embedded secrets. Configure credentials and settings through the
environment:

- `OPENAI_API_KEY`: OpenAI API key for examples that call OpenAI.
- `OPENAI_MODEL`: Optional model override, defaulting to a small example model.
- `PROMPTRAIL_PROJECT`: Optional project name for emitted runtime metadata.

## Public APIs

### `PromptRail`

Create a runtime object for the current application or project.

```python
from promptrail import PromptRail

rail = PromptRail(project="support-agent")
```

### `run(name, runtime=..., metadata=...)`

Open a correlated runtime run around existing work. Use it at workflow boundaries,
such as a request, job, trace span, or agent execution.

```python
from promptrail import PromptRail, run

rail = PromptRail(project="support-agent")

with run("classify-ticket", runtime=rail, metadata={"source": "worker"}):
    classify_ticket()
```

### `event(name, payload=None)`

Emit structured events inside the active run. Events should describe what happened
at runtime, not optimization instructions.

```python
from promptrail import event

event("ticket.received", {"chars": 248})
event("ticket.classified", {"label": "billing"})
```

### `current_runtime_context()`

Read the active runtime context to attach correlation identifiers to logs,
OpenTelemetry spans, or application records.

```python
from promptrail import current_runtime_context

ctx = current_runtime_context()
logger.info("handled request", extra={"promptrail_run_id": getattr(ctx, "run_id", None)})
```

### `wrap_openai(client, runtime=...)`

Wrap an existing OpenAI client so API calls can be correlated with the active
PromptRail runtime run. The wrapper is for runtime correlation only and should not
be used as a client-side optimizer.

```python
import os
from openai import OpenAI
from promptrail import PromptRail, run, wrap_openai

rail = PromptRail(project="support-agent")
client = wrap_openai(OpenAI(api_key=os.environ["OPENAI_API_KEY"]), runtime=rail)

with run("answer-ticket", runtime=rail):
    client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": "Summarize this ticket."}],
    )
```

## Patterns

- Start one `run(...)` per workflow boundary.
- Emit `event(...)` at meaningful steps, decisions, and completion points.
- Use `current_runtime_context()` to join PromptRail runs to logs and traces.
- Keep provider keys in environment variables or your existing secret manager.
- Treat OpenAI and OpenTelemetry packages as optional application dependencies.

See `examples/` for runnable patterns covering basic OpenAI usage, explicit runs,
OpenTelemetry correlation, asynchronous agents, parallel agents, and multi-step
agents.
