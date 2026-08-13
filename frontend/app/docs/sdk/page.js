import Link from "next/link";
import styles from "./sdk.module.css";

export const metadata = {
  title: "PromptRail Python SDK Documentation",
  description: "Install and integrate the PromptRail Python SDK for runtime tracing, OpenAI gateway correlation, OpenTelemetry, and historical imports.",
  alternates: { canonical: "/docs/sdk" },
};

const sections = [
  ["quickstart", "Quickstart"], ["configuration", "Configuration"], ["openai", "OpenAI"],
  ["otel", "OpenTelemetry"], ["history", "Historical traces"], ["privacy", "Privacy"],
  ["events", "Event schema"], ["troubleshooting", "Troubleshooting"],
];

function Code({ children }) { return <pre className={styles.code}><code>{children}</code></pre>; }

export default function SdkDocumentationPage() {
  return <div className={styles.page}>
    <header className={styles.header}>
      <Link className={styles.brand} href="/"><span className={styles.mark}><i/><i/><i/></span><strong>PromptRail</strong></Link>
      <nav><Link className={styles.active} href="/docs/sdk">SDK docs</Link><a href="https://pythoniamath.github.io/PromptRail/">Full reference ↗</a><Link href="/connect">Connect traces</Link><a href="https://github.com/PythonIaMath/PromptRail">GitHub ↗</a></nav>
    </header>
    <div className={styles.layout}>
      <aside className={styles.sidebar}>
        <p>PYTHON SDK</p>
        {sections.map(([id,label]) => <a href={`#${id}`} key={id}>{label}</a>)}
        <div className={styles.status}><span/>SDK v0.1 · Preview</div>
      </aside>
      <main className={styles.main}>
        <section className={styles.hero}>
          <p className={styles.eyebrow}>RUNTIME SDK · PYTHON 3.11+</p>
          <h1>Observe once.<br/><em>Budget every call.</em></h1>
          <p>Connect your application&apos;s live execution context and historical traces to PromptRail. The SDK adds run, user, trace, and span identity while the control plane assigns cost and latency budgets.</p>
          <div className={styles.actions}><a href="#quickstart">Get started</a><Link href="/connect">Connect historical traces →</Link></div>
        </section>

        <section id="quickstart" className={styles.section}>
          <div className={styles.sectionHead}><span>01</span><div><h2>Quickstart</h2><p>One process-level initialization. One wrapped client.</p></div></div>
          <h3>Install</h3><Code>{`pip install "promptrail[runtime]"`}</Code>
          <h3>Initialize</h3><Code>{`import os\nfrom promptrail import PromptRail\n\nPromptRail.init(\n    api_key=os.environ["PROMPTRAIL_API_KEY"],\n    application="support-agent",\n    environment="production",\n)`}</Code>
          <h3>Wrap your client</h3><Code>{`from openai import OpenAI\nfrom promptrail import run, wrap_openai\n\nclient = wrap_openai(OpenAI(\n    base_url="https://api.promptrail.ai/v1",\n    api_key=os.environ["PROMPTRAIL_API_KEY"],\n))\n\nwith run(user_id="customer_123"):\n    response = client.responses.create(\n        model="gpt-4o-mini",\n        input="Summarize the open support ticket.",\n    )`}</Code>
          <div className={styles.note}><strong>Shutdown cleanly.</strong><span>Call <code>PromptRail.shutdown()</code> during orderly process shutdown so queued telemetry gets its full flush deadline.</span></div>
        </section>

        <section id="configuration" className={styles.section}>
          <div className={styles.sectionHead}><span>02</span><div><h2>Configuration</h2><p>Metadata-only by default, bounded at every layer.</p></div></div>
          <div className={styles.table}><div><b>Option</b><b>Default</b><b>Purpose</b></div>{[
            ["application","None","Stable application or agent name"],["environment","None","Separate production and development behavior"],["user_id","None","Stable string or resolver callback"],["privacy_mode","metadata_only","Exclude content from telemetry"],["queue_size","2048","Maximum queued events"],["batch_size","50","Maximum events per export batch"],["enable_opentelemetry","True","Attach the PromptRail span processor"],
          ].map(row => <div key={row[0]}>{row.map(cell => <span key={cell}>{cell}</span>)}</div>)}</div>
        </section>

        <section id="openai" className={styles.section}><div className={styles.sectionHead}><span>03</span><div><h2>OpenAI integration</h2><p>Explicit instrumentation, no monkey-patching.</p></div></div><p>The wrapper supports synchronous and asynchronous chat completions, completions, responses, streaming responses, and embeddings. It checks <code>base_url</code> on every call and only adds private PromptRail headers to the configured gateway origin.</p></section>

        <section id="otel" className={styles.section}><div className={styles.sectionHead}><span>04</span><div><h2>OpenTelemetry</h2><p>Add one observer without replacing your exporters.</p></div></div><Code>{`from opentelemetry import trace\nfrom promptrail import PromptRail\n\nPromptRail.init(api_key=api_key, application="research-agent")\ntracer = trace.get_tracer("my.application")\n\nwith tracer.start_as_current_span(\n    "search-repository",\n    attributes={"promptrail.span.type": "tool", "tool.name": "search_repository"},\n):\n    search_repository()`}</Code></section>

        <section id="history" className={styles.section}><div className={styles.sectionHead}><span>05</span><div><h2>Historical traces</h2><p>Teach PromptRail how your application normally executes.</p></div></div><Code>{`from pathlib import Path\nfrom promptrail import import_historical_traces\n\nhistory = import_historical_traces(\n    Path("traces.jsonl").read_bytes(),\n    metadata_only=True,\n)\nprint(history.summary())`}</Code><p>Accepted formats include PromptRail event batches, JSONL, generic span exports, and OpenTelemetry <code>resourceSpans</code>. Imports are limited to 50 MB.</p><Link className={styles.inlineCta} href="/connect">Open the trace connection interface →</Link></section>

        <section id="privacy" className={styles.section}><div className={styles.sectionHead}><span>06</span><div><h2>Privacy</h2><p>Operational metadata in. Prompts and responses out.</p></div></div><div className={styles.cards}><article><strong>Excluded by default</strong><p>Prompts, completions, messages, documents, source code, request bodies, responses, and tool input/output.</p></article><article><strong>Retained metadata</strong><p>IDs, model and tool names, token counts, durations, hashes, sizes, MIME types, and statuses.</p></article></div></section>

        <section id="events" className={styles.section}><div className={styles.sectionHead}><span>07</span><div><h2>Event schema 1.0</h2><p>One canonical shape for live and historical execution.</p></div></div><Code>{`{\n  "schema_version": "1.0",\n  "event_id": "evt_01...",\n  "run_id": "run_01...",\n  "trace_id": "0123456789abcdef0123456789abcdef",\n  "span_id": "0123456789abcdef",\n  "type": "llm.end",\n  "timestamp_ms": 1720000000000,\n  "attributes": {"input_tokens": 420, "duration_ms": 730}\n}`}</Code></section>

        <section id="troubleshooting" className={styles.section}><div className={styles.sectionHead}><span>08</span><div><h2>Troubleshooting</h2><p>Fast checks for the failure modes that matter.</p></div></div><div className={styles.faq}><details><summary>Wrapped calls contain no PromptRail headers</summary><p>Initialize before the request, confirm the client base URL matches the configured gateway origin, and use a supported OpenAI resource.</p></details><details><summary>Events are not exported</summary><p>Confirm the API key, event endpoint, and <code>export_enabled</code>. Call shutdown before process exit and use debug mode for sanitized diagnostics.</p></details><details><summary>Context disappears in a thread</summary><p>Python context variables do not automatically cross thread-pool submissions. Use <code>submit_with_context</code>.</p></details></div></section>
      </main>
    </div>
  </div>;
}
