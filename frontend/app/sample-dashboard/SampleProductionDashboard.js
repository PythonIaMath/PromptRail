"use client";

import { useEffect, useMemo, useRef, useState } from "react";

const MAX_EVENTS = 2000;
const DEFAULT_APP_ID = "ap-lNSmVDo2MGEPJDyhVr4G4w";

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }
  return Number(value).toLocaleString("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function formatRate(value) {
  if (!value && value !== 0) {
    return "-";
  }
  return `${formatNumber(value, 1)}/min`;
}

function percent(part, total) {
  if (!total) {
    return 0;
  }
  return Math.max(0, Math.min(100, (Number(part || 0) / Number(total)) * 100));
}

function compactName(value) {
  if (!value) {
    return "-";
  }
  return String(value).split("/").at(-1);
}

function Stat({ label, value, hint }) {
  return (
    <div className="sample-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

function ProgressBar({ label, value, total }) {
  const width = percent(value, total);
  return (
    <section className="sample-progress">
      <div>
        <span>{label}</span>
        <strong>
          {formatNumber(value)} / {formatNumber(total)}
        </strong>
      </div>
      <div className="progress-track">
        <span style={{ width: `${width}%` }} />
      </div>
    </section>
  );
}

function Bars({ title, subtitle, counts }) {
  const entries = Object.entries(counts || {}).sort((a, b) => Number(b[1]) - Number(a[1]));
  const total = entries.reduce((sum, [, count]) => sum + Number(count || 0), 0);

  return (
    <section className="sample-panel">
      <div className="events-table-head">
        <h2>{title}</h2>
        <span>{subtitle}</span>
      </div>
      <div className="sample-bars">
        {entries.map(([name, count]) => (
          <div className="sample-bar-row" key={name}>
            <div>
              <strong title={name}>{compactName(name)}</strong>
              <span>{formatNumber(count)}</span>
            </div>
            <div className="progress-track">
              <span style={{ width: `${Math.max(3, percent(count, total))}%` }} />
            </div>
          </div>
        ))}
        {!entries.length ? <p className="empty-state">Waiting for data.</p> : null}
      </div>
    </section>
  );
}

export default function SampleProductionDashboard() {
  const [appId, setAppId] = useState("");
  const [status, setStatus] = useState("idle");
  const [statusLine, setStatusLine] = useState("No stream connected");
  const [routing, setRouting] = useState(null);
  const [routes, setRoutes] = useState({});
  const [planned, setPlanned] = useState(null);
  const [labelStart, setLabelStart] = useState(null);
  const [labels, setLabels] = useState([]);
  const [errors, setErrors] = useState([]);
  const [checkpoints, setCheckpoints] = useState([]);
  const [throughput, setThroughput] = useState(null);
  const [profileStatus, setProfileStatus] = useState(null);
  const eventSourceRef = useRef(null);
  const closedExpectedRef = useRef(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const fromUrl = params.get("app");
    const fromStorage = window.localStorage.getItem("lerouter-sample-dashboard-app");
    setAppId(fromUrl || fromStorage || DEFAULT_APP_ID);
  }, []);

  const summary = useMemo(() => {
    const lastLabel = labels.at(-1);
    const lastCheckpoint = checkpoints.at(-1);
    const completed = lastLabel?.completed ?? lastCheckpoint?.completed ?? 0;
    const total = labelStart?.total ?? planned?.label_jobs ?? lastLabel?.total ?? lastCheckpoint?.total ?? 0;
    const saved = lastCheckpoint?.labels ?? labels.length;
    const failures = lastCheckpoint?.failures ?? errors.filter((item) => item.event !== "stderr").length;
    const modelCounts = labels.reduce((acc, item) => {
      const model = item.model_id || "unknown";
      acc[model] = (acc[model] || 0) + 1;
      return acc;
    }, {});
    const routeCounts = labels.reduce((acc, item) => {
      const route = item.route || "unknown";
      acc[route] = (acc[route] || 0) + 1;
      return acc;
    }, {});
    const avgScore = labels.length
      ? labels.reduce((sum, item) => sum + Number(item.quality_score || 0), 0) / labels.length
      : null;
    const usableRate = completed ? saved / completed : null;

    return {
      completed,
      total,
      saved,
      failures,
      modelCounts,
      routeCounts,
      avgScore,
      usableRate,
    };
  }, [checkpoints, errors, labelStart, labels, planned]);

  function disconnect() {
    closedExpectedRef.current = true;
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setStatus("idle");
    setStatusLine("Stream disconnected");
  }

  function connect() {
    const trimmed = appId.trim();
    if (!trimmed) {
      setStatusLine("Missing Modal app id");
      return;
    }

    disconnect();
    closedExpectedRef.current = false;
    window.localStorage.setItem("lerouter-sample-dashboard-app", trimmed);
    setStatus("connecting");
    setStatusLine(`Connecting to ${trimmed}`);
    setRouting(null);
    setRoutes({});
    setPlanned(null);
    setLabelStart(null);
    setLabels([]);
    setErrors([]);
    setCheckpoints([]);
    setThroughput(null);
    setProfileStatus(null);

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

    source.addEventListener("api_key_pools", (event) => {
      const payload = JSON.parse(event.data);
      setStatus("planning");
      setStatusLine(`Keys loaded: ${payload.answer_provider}, judge ${payload.judge_provider}`);
    });

    source.addEventListener("archrouter_route_progress", (event) => {
      const payload = JSON.parse(event.data);
      setRouting(payload);
      setStatus("routing");
      setStatusLine(`ArchRouter ${payload.completed}/${payload.total}`);
    });

    source.addEventListener("archrouter_routes", (event) => {
      const payload = JSON.parse(event.data);
      setRoutes(payload.routes || {});
      setStatusLine(`Routes selected: ${payload.samples} samples`);
    });

    source.addEventListener("synthetic_route_balance", (event) => {
      const payload = JSON.parse(event.data);
      setRoutes(payload.routes_after || {});
    });

    source.addEventListener("planned_model_coverage", (event) => {
      const payload = JSON.parse(event.data);
      setPlanned(payload);
      setStatus("planned");
      setStatusLine(`Planned ${payload.label_jobs} labels across ${payload.unique_models} models`);
    });

    source.addEventListener("model_profiles_loaded", (event) => {
      setProfileStatus(JSON.parse(event.data));
    });

    source.addEventListener("label_start", (event) => {
      const payload = JSON.parse(event.data);
      setLabelStart(payload);
      setStatus("labeling");
      setStatusLine(`Labeling 0/${payload.total}`);
    });

    source.addEventListener("label_throughput", (event) => {
      const payload = JSON.parse(event.data);
      setThroughput(payload);
      setStatus("labeling");
      setStatusLine(`Labels ${payload.labels}/${labelStart?.total || planned?.label_jobs || "-"}`);
    });

    source.addEventListener("label", (event) => {
      const payload = JSON.parse(event.data);
      setLabels((current) => [...current.slice(-MAX_EVENTS + 1), payload]);
      setStatus("labeling");
      setStatusLine(`Label ${payload.completed}/${payload.total}`);
    });

    source.addEventListener("label_checkpoint", (event) => {
      const payload = JSON.parse(event.data);
      setCheckpoints((current) => [...current.slice(-MAX_EVENTS + 1), payload]);
      setStatus("checkpointed");
      setStatusLine(`Checkpoint ${payload.labels} labels, ${payload.failures} failures`);
    });

    source.addEventListener("label_error", (event) => {
      const payload = JSON.parse(event.data);
      setErrors((current) => [...current.slice(-MAX_EVENTS + 1), payload]);
      setStatus("labeling");
      setStatusLine(`Failure ${payload.completed}/${payload.total}`);
    });

    source.addEventListener("stderr", (event) => {
      const payload = JSON.parse(event.data);
      setErrors((current) => [...current.slice(-MAX_EVENTS + 1), { event: "stderr", ...payload }]);
    });

    source.onerror = () => {
      if (eventSourceRef.current !== source) {
        return;
      }
      if (closedExpectedRef.current) {
        setStatus("closed");
        setStatusLine("Modal log stream closed");
        return;
      }
      setStatus("error");
      setStatusLine("Stream interrupted");
    };
  }

  useEffect(() => {
    return () => disconnect();
  }, []);

  const recentLabels = labels.slice(-14).reverse();
  const recentErrors = errors.slice(-8).reverse();
  const latestRunId = planned?.run_id || labelStart?.run_id || routing?.run_id || labels.at(-1)?.run_id || "-";

  return (
    <main className="dashboard-page sample-dashboard-page">
      <section className="dashboard-shell sample-dashboard-shell">
        <header className="dashboard-header sample-dashboard-header">
          <div>
            <p className="dashboard-label">PromptRail Sample Factory</p>
            <h1>Production des samples</h1>
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
              placeholder={DEFAULT_APP_ID}
              spellCheck="false"
            />
          </label>
          <button type="submit">Connect</button>
          <button type="button" className="button-muted" onClick={disconnect}>
            Stop
          </button>
        </form>

        <section className="sample-run-strip">
          <code>{latestRunId}</code>
          <span>{profileStatus ? `${profileStatus.loaded}/${profileStatus.models} profiles loaded` : "profiles pending"}</span>
          <span>{planned ? `${planned.unique_models} models planned` : "model coverage pending"}</span>
        </section>

        <section className="sample-stats-grid" aria-label="Production metrics">
          <Stat label="Labels produits" value={`${formatNumber(summary.completed)} / ${formatNumber(summary.total)}`} />
          <Stat label="Sauvegardes" value={formatNumber(summary.saved)} hint="dernier checkpoint" />
          <Stat label="Failures" value={formatNumber(summary.failures)} />
          <Stat label="Usable rate" value={summary.usableRate === null ? "-" : `${formatNumber(summary.usableRate * 100, 1)}%`} />
          <Stat label="Avg reward" value={formatNumber(summary.avgScore, 3)} />
          <Stat label="Throughput" value={formatRate(throughput?.label_rpm)} hint={`answers ${formatRate(throughput?.answer_rpm)}`} />
        </section>

        <div className="sample-progress-grid">
          <ProgressBar label="ArchRouter" value={routing?.completed || 0} total={routing?.total || 0} />
          <ProgressBar label="Labeling" value={summary.completed} total={summary.total} />
        </div>

        <section className="sample-mix-grid">
          <Bars title="Providers" subtitle="planned requests" counts={planned?.answer_providers || {}} />
          <Bars title="SOTA mix" subtitle="planned labels" counts={planned?.sota_mix || {}} />
        </section>

        <section className="sample-mix-grid">
          <Bars title="Routes produites" subtitle={`${Object.keys(summary.routeCounts).length} active routes`} counts={summary.routeCounts} />
          <Bars title="Modeles produits" subtitle={`${Object.keys(summary.modelCounts).length} active models`} counts={summary.modelCounts} />
        </section>

        <section className="sample-mix-grid">
          <Bars title="Routes selectionnees" subtitle={`${Object.keys(routes).length} routes`} counts={routes} />
          <Bars title="Modeles planifies" subtitle={`${Object.keys(planned?.models || {}).length} models`} counts={planned?.models || {}} />
        </section>

        <section className="events-table" aria-label="Recent labels">
          <div className="events-table-head">
            <h2>Derniers labels</h2>
            <span>{labels.length} events recus</span>
          </div>
          <div className="events-table-body">
            {recentLabels.map((item, index) => (
              <div className="event-row sample-label-row" key={`${item.completed}-${item.model_id}-${index}`}>
                <span>{item.completed}</span>
                <strong title={item.model_id}>{compactName(item.model_id)}</strong>
                <span title={item.route}>{item.route}</span>
                <span>{formatNumber(item.quality_score, 3)}</span>
              </div>
            ))}
            {!recentLabels.length ? <p className="empty-state">Waiting for label events.</p> : null}
          </div>
        </section>

        {recentErrors.length ? (
          <section className="events-table dashboard-log-table" aria-label="Recent errors">
            <div className="events-table-head">
              <h2>Erreurs recentes</h2>
              <span>{errors.length} events</span>
            </div>
            <div className="events-table-body">
              {recentErrors.map((item, index) => (
                <div className="event-row log-event-row" key={`${item.completed || item.line || index}-${index}`}>
                  <span>{item.stage || item.event || "error"}</span>
                  <strong>{item.completed ?? "-"}</strong>
                  <span>{item.model_id ? compactName(item.model_id) : item.route || "-"}</span>
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
