"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const MAX_POINTS = 5000;
const DEFAULT_CONTEXT_APP_ID = "ap-9ZeGwdew4xDxU8pd0SSETB";
const BIN_COUNT = 10;

function formatNumber(value, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }
  return Number(value).toFixed(digits);
}

function compactModelName(value) {
  if (!value) {
    return "-";
  }
  return String(value).split("/").at(-1);
}

function makePath(points, width, height, padding) {
  if (points.length < 2) {
    return "";
  }

  const values = points.map((point) => point.loss);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  return points
    .map((point, index) => {
      const x = padding + (index / (points.length - 1)) * (width - padding * 2);
      const y = height - padding - ((point.loss - min) / range) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
}

function LossChart({ points }) {
  const width = 900;
  const height = 300;
  const padding = 34;
  const values = points.map((point) => point.loss);
  const min = values.length ? Math.min(...values) : null;
  const max = values.length ? Math.max(...values) : null;
  const last = points.at(-1);
  const path = makePath(points, width, height, padding);

  return (
    <div className="chart-panel">
      <div className="chart-head">
        <div>
          <p className="dashboard-label">Training Loss</p>
          <h2>{last ? formatNumber(last.loss) : "-"}</h2>
        </div>
        <div className="chart-range">
          <span>min {formatNumber(min)}</span>
          <span>max {formatNumber(max)}</span>
          <span>{points.length} points</span>
        </div>
      </div>

      <svg className="loss-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Loss chart">
        <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} />
        <line x1={padding} y1={padding} x2={padding} y2={height - padding} />
        {path ? <path d={path} /> : null}
        {points.length === 1 ? <circle cx={width / 2} cy={height / 2} r="5" /> : null}
      </svg>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="dashboard-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function DistributionBars({ bins, total }) {
  return (
    <section className="dashboard-panel" aria-label="Context importance distribution">
      <div className="events-table-head">
        <h2>Distribution importance</h2>
        <span>{total} labels</span>
      </div>
      <div className="distribution-bars">
        {bins.map((count, index) => {
          const start = index / BIN_COUNT;
          const end = (index + 1) / BIN_COUNT;
          const width = total ? Math.max(3, (count / total) * 100) : 0;
          return (
            <div className="distribution-row" key={start}>
              <span>{`${start.toFixed(1)}-${end.toFixed(1)}`}</span>
              <div className="distribution-track">
                <i style={{ width: `${width}%` }} />
              </div>
              <strong>{count}</strong>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function ModelBreakdown({ counts, total }) {
  return (
    <section className="dashboard-panel" aria-label="Answer model distribution">
      <div className="events-table-head">
        <h2>Modeles completion</h2>
        <span>{Object.keys(counts).length} modeles</span>
      </div>
      <div className="model-breakdown">
        {Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .map(([model, count]) => {
            const width = total ? Math.max(4, (count / total) * 100) : 0;
            return (
              <div className="model-row" key={model}>
                <div>
                  <strong title={model}>{compactModelName(model)}</strong>
                  <span>{count}</span>
                </div>
                <div className="distribution-track">
                  <i style={{ width: `${width}%` }} />
                </div>
              </div>
            );
          })}
        {!Object.keys(counts).length ? <p className="empty-state">Waiting for model data.</p> : null}
      </div>
    </section>
  );
}

export default function LossDashboard() {
  const [appId, setAppId] = useState("");
  const [status, setStatus] = useState("idle");
  const [statusLine, setStatusLine] = useState("No stream connected");
  const [losses, setLosses] = useState([]);
  const [labels, setLabels] = useState([]);
  const [labelTarget, setLabelTarget] = useState(null);
  const [retries, setRetries] = useState([]);
  const [errors, setErrors] = useState([]);
  const [streamKind, setStreamKind] = useState("context");
  const eventSourceRef = useRef(null);
  const closedExpectedRef = useRef(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("app");
    const fromStorage = window.localStorage.getItem("lerouter-dashboard-app");
    setAppId(fromUrl || fromStorage || DEFAULT_CONTEXT_APP_ID);
  }, []);

  const summary = useMemo(() => {
    const lastLoss = losses.at(-1)?.loss ?? null;
    const minLoss = losses.length ? Math.min(...losses.map((item) => item.loss)) : null;
    const completed = labels.at(-1)?.completed ?? labels.length;
    const total = labelTarget ?? labels.at(-1)?.total ?? null;
    const contextLabels = labels.filter((item) => item.context_importance !== undefined);
    const oldCorrectCount = labels.filter((item) => item.correct).length;
    const oldAvgQuality = labels.length
      ? labels.reduce((sum, item) => sum + Number(item.quality_score || 0), 0) / labels.length
      : null;
    const importanceAvg = contextLabels.length
      ? contextLabels.reduce((sum, item) => sum + Number(item.context_importance || 0), 0) / contextLabels.length
      : null;
    const withContextAvg = contextLabels.length
      ? contextLabels.reduce((sum, item) => sum + Number(item.with_context_score || 0), 0) / contextLabels.length
      : null;
    const noContextAvg = contextLabels.length
      ? contextLabels.reduce((sum, item) => sum + Number(item.no_context_score || 0), 0) / contextLabels.length
      : null;
    const modelCounts = contextLabels.reduce((acc, item) => {
      const model = item.answer_model || item.model_id || "unknown";
      acc[model] = (acc[model] || 0) + 1;
      return acc;
    }, {});
    const bins = Array.from({ length: BIN_COUNT }, () => 0);
    for (const item of contextLabels) {
      const value = Math.max(0, Math.min(1, Number(item.context_importance || 0)));
      const index = Math.min(BIN_COUNT - 1, Math.floor(value * BIN_COUNT));
      bins[index] += 1;
    }

    return {
      lastLoss,
      minLoss,
      completed,
      total,
      correctRate: labels.length ? oldCorrectCount / labels.length : null,
      avgQuality: oldAvgQuality,
      importanceAvg,
      withContextAvg,
      noContextAvg,
      modelCounts,
      bins,
      contextCount: contextLabels.length,
    };
  }, [labels, labelTarget, losses]);

  function disconnect() {
    closedExpectedRef.current = true;
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setStatus("idle");
    setStatusLine("Stream disconnected");
  }

  function pushLabel(payload, kind) {
    setLabels((current) => [...current.slice(-MAX_POINTS + 1), payload]);
    setLabelTarget(payload.total);
    setStreamKind(kind);
    setStatus("labeling");
    setStatusLine(`Labeling ${payload.completed}/${payload.total}`);
  }

  function pushLoss(payload, kind) {
    const loss = Number(payload.loss ?? payload.loss_mae_tokens);
    if (!Number.isFinite(loss)) {
      pushError({
        event: "loss_parse_error",
        line: `Missing numeric loss in ${JSON.stringify(payload)}`,
      });
      return;
    }

    setLosses((current) => [...current.slice(-MAX_POINTS + 1), { ...payload, loss }]);
    setStreamKind(kind);
    setStatus("training");
    setStatusLine(`Training step ${payload.step}`);
  }

  function pushError(payload) {
    setErrors((current) => [...current.slice(-999), payload]);
  }

  function connect() {
    const trimmed = appId.trim();
    if (!trimmed) {
      setStatusLine("Missing Modal app id");
      return;
    }

    disconnect();
    closedExpectedRef.current = false;
    window.localStorage.setItem("lerouter-dashboard-app", trimmed);
    setStatus("connecting");
    setStatusLine(`Connecting to ${trimmed}`);
    setLosses([]);
    setLabels([]);
    setLabelTarget(null);
    setRetries([]);
    setErrors([]);

    const source = new EventSource(`/api/modal-loss?app=${encodeURIComponent(trimmed)}`);
    eventSourceRef.current = source;

    source.addEventListener("status", (event) => {
      const payload = JSON.parse(event.data);
      if (payload.state === "closed") {
        closedExpectedRef.current = true;
        source.close();
      }
      setStatus(payload.state === "closed" ? "closed" : payload.state);
      setStatusLine(payload.state === "closed" ? "Modal log stream closed" : payload.state);
    });

    source.addEventListener("context_samples_loaded", (event) => {
      const payload = JSON.parse(event.data);
      setLabelTarget(payload.max_samples || payload.total || payload.rows);
      setStreamKind("context");
      setStatus("loading");
      setStatusLine(`Loaded ${payload.max_samples || payload.rows || payload.total || "-"} samples`);
    });

    source.addEventListener("context_label_start", (event) => {
      const payload = JSON.parse(event.data);
      setLabelTarget(payload.total);
      setStreamKind("context");
      setStatus("labeling");
      setStatusLine(`Labeling 0/${payload.total}`);
    });

    source.addEventListener("label_start", (event) => {
      const payload = JSON.parse(event.data);
      setLabelTarget(payload.total);
      setStreamKind("quality");
      setStatus("labeling");
      setStatusLine(`Labeling 0/${payload.total}`);
    });

    source.addEventListener("context_label", (event) => {
      pushLabel(JSON.parse(event.data), "context");
    });

    source.addEventListener("label", (event) => {
      pushLabel(JSON.parse(event.data), "quality");
    });

    source.addEventListener("context_label_retry", (event) => {
      const payload = JSON.parse(event.data);
      setRetries((current) => [...current.slice(-999), payload]);
      setStreamKind("context");
      setStatusLine(`Retry sample ${payload.sample_index}, attempt ${payload.next_attempt}`);
    });

    source.addEventListener("context_label_error", (event) => {
      const payload = JSON.parse(event.data);
      pushError(payload);
      setStreamKind("context");
      setStatusLine(`Label error ${payload.completed || "-"}/${payload.total || "-"}`);
    });

    source.addEventListener("label_error", (event) => {
      const payload = JSON.parse(event.data);
      pushError(payload);
      setStreamKind("quality");
      setStatusLine(`Label error ${payload.completed || "-"}/${payload.total || "-"}`);
    });

    source.addEventListener("context_loss", (event) => {
      pushLoss(JSON.parse(event.data), "context");
    });

    source.addEventListener("length_loss", (event) => {
      pushLoss(JSON.parse(event.data), "length");
    });

    source.addEventListener("length_hf_ingest_model", (event) => {
      const payload = JSON.parse(event.data);
      setStreamKind("length");
      setStatus("loading");
      setLabelTarget(payload.total);
      setStatusLine(`HF ingest ${payload.completed}/${payload.total}: ${compactModelName(payload.model)}`);
    });

    source.addEventListener("loss", (event) => {
      pushLoss(JSON.parse(event.data), "quality");
    });

    source.addEventListener("stderr", (event) => {
      pushError(JSON.parse(event.data));
    });

    source.onerror = () => {
      if (eventSourceRef.current === source) {
        if (closedExpectedRef.current) {
          setStatus("closed");
          setStatusLine("Modal log stream closed");
          return;
        }
        setStatus((current) => (current === "closed" ? "closed" : "error"));
        setStatusLine("Stream interrupted");
      }
    };
  }

  useEffect(() => {
    return () => disconnect();
  }, []);

  const labelProgress =
    summary.total && summary.total > 0
      ? Math.min(100, Math.round((summary.completed / summary.total) * 100))
      : 0;
  const recentLabels = labels.slice(-12).reverse();
  const contextMode = streamKind === "context";

  return (
    <main className="dashboard-page">
      <section className="dashboard-shell">
        <header className="dashboard-header">
          <div>
            <p className="dashboard-label">PromptRail Training</p>
            <h1>Evolution loss et stats</h1>
          </div>
          <div className={`stream-status stream-status-${status}`}>{statusLine}</div>
        </header>

        <form
          className="dashboard-controls"
          onSubmit={(event) => {
            event.preventDefault();
            connect();
          }}
        >
          <label>
            <span>Modal App ID</span>
            <input
              value={appId}
              onChange={(event) => setAppId(event.target.value)}
              placeholder={DEFAULT_CONTEXT_APP_ID}
              spellCheck="false"
            />
          </label>
          <button type="submit">Connect</button>
          <button type="button" className="button-muted" onClick={disconnect}>
            Stop
          </button>
        </form>

        <section className="dashboard-grid" aria-label="Run metrics">
          <Stat label="Last loss" value={formatNumber(summary.lastLoss)} />
          <Stat label="Min loss" value={formatNumber(summary.minLoss)} />
          <Stat
            label="Labels"
            value={summary.total ? `${summary.completed}/${summary.total}` : String(summary.completed)}
          />
          <Stat
            label="Avg importance"
            value={contextMode ? formatNumber(summary.importanceAvg, 3) : formatNumber(summary.avgQuality, 3)}
          />
          <Stat
            label={contextMode ? "With / no context" : "Correct rate"}
            value={
              contextMode
                ? `${formatNumber(summary.withContextAvg, 2)} / ${formatNumber(summary.noContextAvg, 2)}`
                : summary.correctRate === null
                  ? "-"
                  : `${Math.round(summary.correctRate * 100)}%`
            }
          />
          <Stat label="Retries / errors" value={`${retries.length} / ${errors.length}`} />
        </section>

        <section className="label-progress" aria-label="Label progress">
          <div>
            <span>Labeling</span>
            <strong>{labelProgress}%</strong>
          </div>
          <div className="progress-track">
            <span style={{ width: `${labelProgress}%` }} />
          </div>
        </section>

        <LossChart points={losses} />

        {contextMode ? (
          <section className="dashboard-split">
            <DistributionBars bins={summary.bins} total={summary.contextCount} />
            <ModelBreakdown counts={summary.modelCounts} total={summary.contextCount} />
          </section>
        ) : null}

        <section className="events-table" aria-label="Recent label events">
          <div className="events-table-head">
            <h2>Recent Labels</h2>
            <span>{labels.length} received</span>
          </div>
          <div className="events-table-body">
            {recentLabels.map((item, index) => {
              const model = item.answer_model || item.model_id;
              const score = item.context_importance ?? item.quality_score;
              const route = contextMode
                ? `${formatNumber(item.with_context_score, 2)} / ${formatNumber(item.no_context_score, 2)}`
                : item.route;
              return (
                <div className="event-row context-event-row" key={`${item.completed}-${model}-${index}`}>
                  <span>{item.completed}</span>
                  <strong title={model}>{compactModelName(model)}</strong>
                  <span>{route}</span>
                  <span>{formatNumber(score, 2)}</span>
                </div>
              );
            })}
            {!labels.length ? <p className="empty-state">Waiting for label events.</p> : null}
          </div>
        </section>

        {retries.length || errors.length ? (
          <section className="events-table dashboard-log-table" aria-label="Recent retry and error events">
            <div className="events-table-head">
              <h2>Retries et erreurs</h2>
              <span>{retries.length + errors.length} events</span>
            </div>
            <div className="events-table-body">
              {[...retries.map((item) => ({ ...item, kind: "retry" })), ...errors.map((item) => ({ ...item, kind: "error" }))]
                .slice(-8)
                .reverse()
                .map((item, index) => (
                  <div className="event-row log-event-row" key={`${item.kind}-${item.sample_index || index}-${index}`}>
                    <span>{item.kind}</span>
                    <strong>{item.sample_index ?? item.completed ?? "-"}</strong>
                    <span>{item.attempt ? `attempt ${item.attempt}` : item.code || "-"}</span>
                    <span title={item.error || item.line}>{item.error || item.line || "-"}</span>
                  </div>
                ))}
            </div>
          </section>
        ) : null}
      </section>
    </main>
  );
}
