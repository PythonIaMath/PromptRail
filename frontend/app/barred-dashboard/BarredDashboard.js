"use client";

import { useEffect, useMemo, useState } from "react";

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "-";
  }
  return Number(value).toLocaleString("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function percent(part, total) {
  if (!total) {
    return 0;
  }
  return Math.max(0, Math.min(100, (Number(part || 0) / Number(total)) * 100));
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
  return (
    <section className="sample-progress">
      <div>
        <span>{label}</span>
        <strong>
          {formatNumber(value)} / {formatNumber(total)}
        </strong>
      </div>
      <div className="progress-track">
        <span style={{ width: `${percent(value, total)}%` }} />
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
              <strong title={name}>{name}</strong>
              <span>{formatNumber(count)}</span>
            </div>
            <div className="progress-track">
              <span style={{ width: `${Math.max(3, percent(count, total))}%` }} />
            </div>
          </div>
        ))}
        {!entries.length ? <p className="empty-state">Waiting for BARRED events.</p> : null}
      </div>
    </section>
  );
}

export default function BarredDashboard() {
  const [runName, setRunName] = useState("");
  const [inputRun, setInputRun] = useState("");
  const [data, setData] = useState(null);
  const [status, setStatus] = useState("connecting");

  async function load(nextRun = runName) {
    const query = nextRun ? `?run=${encodeURIComponent(nextRun)}` : "";
    try {
      const response = await fetch(`/api/barred-progress${query}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || "BARRED progress unavailable.");
      }
      setData(payload);
      setRunName(payload.runName);
      setInputRun(payload.runName);
      setStatus("live");
    } catch (error) {
      setStatus(error.message || "error");
    }
  }

  useEffect(() => {
    load("");
    const timer = setInterval(() => load(runName), 5000);
    return () => clearInterval(timer);
  }, [runName]);

  const summary = useMemo(() => {
    const latest = data?.latest || {};
    const accepted = Number(latest.accepted ?? data?.rowCount ?? 0);
    const target = Number(data?.target || 2000);
    const attempts = Number(latest.attempts || 0);
    const rejected = Number(data?.rejectedCount ?? latest.rejected ?? 0);
    const avgAttemptSeconds = Number(latest.avg_attempt_seconds || 0);
    const etaSeconds = accepted > 0 ? Math.max(0, (target - accepted) * Number(latest.seconds_per_accepted || avgAttemptSeconds || 0)) : null;
    return {
      accepted,
      target,
      attempts,
      rejected,
      avgAttemptSeconds,
      etaHours: etaSeconds === null ? null : etaSeconds / 3600,
      routeCounts: latest.route_counts || {},
      boundaryCounts: latest.boundary_counts || {},
    };
  }, [data]);

  const recentEvents = (data?.events || []).slice(-12).reverse();

  return (
    <main className="dashboard-page sample-dashboard-page">
      <section className="dashboard-shell sample-dashboard-shell">
        <header className="dashboard-header sample-dashboard-header">
          <div>
            <p className="dashboard-label">PromptRail BARRED Factory</p>
            <h1>Generation BARRED</h1>
          </div>
          <div className={`stream-status stream-status-${status === "live" ? "labeling" : "error"}`}>{status}</div>
        </header>

        <form
          className="dashboard-controls"
          onSubmit={(event) => {
            event.preventDefault();
            setRunName(inputRun.trim());
            load(inputRun.trim());
          }}
        >
          <label>
            <span>Run local</span>
            <input value={inputRun} onChange={(event) => setInputRun(event.target.value)} spellCheck="false" />
          </label>
          <button type="submit">Load</button>
        </form>

        <section className="sample-run-strip">
          <code>{data?.runName || "-"}</code>
          <span>{data?.barredDir || "directory pending"}</span>
          <span>{data?.logPath || "log pending"}</span>
        </section>

        <section className="sample-stats-grid" aria-label="BARRED production metrics">
          <Stat label="Samples acceptes" value={`${formatNumber(summary.accepted)} / ${formatNumber(summary.target)}`} />
          <Stat label="Tentatives" value={formatNumber(summary.attempts)} />
          <Stat label="Rejets" value={formatNumber(summary.rejected)} />
          <Stat label="Acceptance" value={summary.attempts ? `${formatNumber((summary.accepted / summary.attempts) * 100, 1)}%` : "-"} />
          <Stat label="Seconds/sample" value={formatNumber(data?.latest?.seconds_per_accepted, 1)} />
          <Stat label="ETA" value={summary.etaHours === null ? "-" : `${formatNumber(summary.etaHours, 1)}h`} />
        </section>

        <div className="sample-progress-grid">
          <ProgressBar label="BARRED accepted" value={summary.accepted} total={summary.target} />
          <ProgressBar label="Schedule cells" value={data?.latest?.schedule_complete_cells || 0} total={data?.latest?.schedule_cells || 0} />
        </div>

        <section className="sample-mix-grid">
          <Bars title="Routes" subtitle="accepted samples" counts={summary.routeCounts} />
          <Bars title="Boundaries" subtitle="accepted samples" counts={summary.boundaryCounts} />
        </section>

        <section className="events-table" aria-label="Recent BARRED events">
          <div className="events-table-head">
            <h2>Derniers events</h2>
            <span>{data?.events?.length || 0} recents</span>
          </div>
          <div className="events-table-body">
            {recentEvents.map((event, index) => (
              <div className="event-row barred-event-row" key={`${event.updated_at}-${event.accepted}-${index}`}>
                <span>{event.accepted}</span>
                <strong>{event.attempts}</strong>
                <span>{formatNumber(event.seconds_per_accepted, 1)}s</span>
                <span>{event.updated_at || "-"}</span>
              </div>
            ))}
            {!recentEvents.length ? <p className="empty-state">Waiting for BARRED progress.</p> : null}
          </div>
        </section>
      </section>
    </main>
  );
}
