# PromptRail SDK

<span class="hero-kicker">Observe once. Budget every call.</span>

# Runtime intelligence for your LLM application

Connect live execution context and historical traces to PromptRail. The Python SDK supplies run, user, trace, and span identity while PromptRail assigns cost and latency budgets in the control plane.

<div class="hero-actions">
  <a class="md-button md-button--primary" href="runtime-sdk/">Start with the Python SDK</a>
  <a class="md-button" href="runtime-event-schema/">Explore the event schema</a>
</div>

<div class="feature-grid" markdown>

<div class="feature-card" markdown>

### Gateway correlation

Attach fresh run and trace identity to OpenAI-compatible requests without monkey-patching the client.

</div>

<div class="feature-card" markdown>

### OpenTelemetry observation

Add PromptRail as a span processor without replacing your existing provider or exporters.

</div>

<div class="feature-card" markdown>

### Historical imports

Normalize PromptRail batches, JSONL, generic spans, and OTLP JSON into one canonical event schema.

</div>

<div class="feature-card" markdown>

### Metadata-first privacy

Prompt and response content is excluded by default. Operational metadata remains bounded and useful.

</div>

</div>

## Install

```bash
pip install "promptrail[runtime]"
```

## Initialize

```python
import os

from promptrail import PromptRail

PromptRail.init(
    api_key=os.environ["PROMPTRAIL_API_KEY"],
    application="support-agent",
    environment="production",
)
```

## Instrument an OpenAI-compatible client

```python
from openai import OpenAI
from promptrail import run, wrap_openai

client = wrap_openai(
    OpenAI(
        base_url="https://api.promptrail.ai/v1",
        api_key=os.environ["PROMPTRAIL_API_KEY"],
    )
)

with run(user_id="customer_123"):
    response = client.responses.create(
        model="gpt-4o-mini",
        input="Summarize the open support ticket.",
    )
```

[Read the complete setup guide →](runtime-sdk.md)

## Current status

The runtime context, OpenAI wrapper, HTTP hooks, OpenTelemetry processor, event exporter, privacy policy, and historical JSON importer are implemented and tested.

Remote LangSmith, Langfuse, Braintrust, and Helicone synchronization remains under construction. Configuration validation is available now, but it does not imply that a remote sync has started.
