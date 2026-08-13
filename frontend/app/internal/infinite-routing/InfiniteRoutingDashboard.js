"use client";
import { useEffect, useMemo, useState } from "react";
import styles from "./RoutingDashboard.module.css";
const formatter = new Intl.NumberFormat("en-US");

export default function InfiniteRoutingDashboard() {
  const [receipts, setReceipts] = useState(null);
  const [error, setError] = useState("");
  async function load() {
    setError("");
    const response = await fetch("/api/infinite/ops", { cache: "no-store" });
    if (!response.ok) { setError("Routing receipts are unavailable."); return; }
    const body = await response.json(); setReceipts(Array.isArray(body.receipts) ? body.receipts : []);
  }
  useEffect(() => { load(); }, []);
  const metrics = useMemo(() => {
    const rows = receipts || []; const successes = rows.filter((row) => row.attempts?.some((attempt) => attempt.result === "success")).length;
    const avg = rows.length ? Math.round(rows.reduce((sum, row) => sum + Number(row.decision_latency_ms), 0) / rows.length) : 0;
    return { total: rows.length, successes, avg, degraded: rows.filter((row) => row.degraded_free_only).length };
  }, [receipts]);
  return <main className={styles.page}>
    <header className={styles.header}><div><p className={styles.kicker}>Internal operations</p><h1>Infinite routing</h1><p>Sanitized execution receipts from the hosted gateway.</p></div><button type="button" onClick={load}>Refresh</button></header>
    {error ? <section className={styles.state}>{error}</section> : null}
    {receipts === null && !error ? <section className={styles.state}>Loading routing receipts...</section> : null}
    {receipts ? <><section className={styles.metrics}>
      <article><span>Requests</span><strong>{formatter.format(metrics.total)}</strong></article>
      <article><span>Successful</span><strong>{metrics.total ? `${Math.round(metrics.successes / metrics.total * 100)}%` : "0%"}</strong></article>
      <article><span>Average selection latency</span><strong>{formatter.format(metrics.avg)} ms</strong></article>
      <article><span>Degraded routes</span><strong>{formatter.format(metrics.degraded)}</strong></article>
    </section><section className={styles.tableWrap}><table><thead><tr><th>Time</th><th>Executed model</th><th>Capacity</th><th>Result</th><th>Selection latency</th><th>Route</th></tr></thead><tbody>
      {receipts.map((row) => <tr key={row.id}><td>{new Date(row.created_at).toLocaleString()}</td><td>{row.executed_model || row.selected_model || "Unknown"}</td><td>{row.capacity_class}</td><td>{row.attempts?.at(-1)?.result || "unknown"}</td><td>{formatter.format(row.decision_latency_ms)} ms</td><td><code>{row.route_id}</code></td></tr>)}
    </tbody></table>{receipts.length === 0 ? <p className={styles.empty}>No routing receipts recorded yet.</p> : null}</section></> : null}
  </main>;
}
