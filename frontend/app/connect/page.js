"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import styles from "./connect.module.css";

const providers = [
  ["LangSmith","L","#237b61","Sync projects and traces"], ["Langfuse","◒","#e96f39","Import observations"],
  ["Braintrust","B","#6657db","Connect experiments"], ["Helicone","H","#6858e8","Sync request history"],
  ["OpenTelemetry","◫","#ed7b20","Receive OTLP traces"], ["Custom API","⌘","#17212b","Bring your own source"],
];

export default function TraceConnectionPage() {
  const [tab,setTab] = useState("providers"); const [provider,setProvider] = useState(null);
  const [toast,setToast] = useState(null); const [file,setFile] = useState(null); const inputRef=useRef(null);
  function notify(title,detail){setToast({title,detail});setTimeout(()=>setToast(null),4200)}
  function chooseFile(selected){const next=selected?.[0];if(!next)return;if(!/\.(json|jsonl)$/i.test(next.name)){notify("Unsupported file","Choose a JSON or JSONL trace export.");return}if(next.size>50*1024*1024){notify("File is too large","Trace imports are limited to 50 MB.");return}setFile(next);notify("Trace data ready","Schema will be normalized to PromptRail event schema 1.0.")}
  async function submit(e){
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const source = provider.name === "Custom API" ? "custom" : provider.name.toLowerCase();
    const response = await fetch("/api/trace-sources/connect", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({source,credential:form.get("credential"),project:form.get("project"),metadata_only:form.get("metadata_only")==="on"})});
    const payload = await response.json();
    if(!response.ok){notify("Configuration failed",payload.error||"Unable to validate this source.");return}
    setProvider(null);notify("Configuration validated",payload.message);
  }
  return <div className={styles.page}>
    <header className={styles.header}><Link className={styles.brand} href="/"><span className={styles.mark}><i/><i/><i/></span><strong>PromptRail</strong></Link><nav><Link href="/docs/sdk">SDK docs</Link><Link className={styles.active} href="/connect">Connect traces</Link></nav></header>
    <main className={styles.main}>
      <section className={styles.hero}><p>01 · HISTORICAL CONTEXT</p><h1>Connect your<br/>historical traces <em>once.</em></h1><span>PromptRail learns how your application normally executes, observes the current run, and dynamically assigns a cost and latency budget to every LLM call.</span><div className={styles.flow}><div><b>⌁</b><small>YOUR TRACES</small><strong>Historical runs</strong></div><i>→</i><div><b>P</b><small>PROMPTRAIL</small><strong>Learn patterns</strong></div><i>→</i><div><b>↗</b><small>LLM ENDPOINT</small><strong>Budget every call</strong></div></div></section>
      <section className={styles.panel}><div className={styles.panelHead}><div><span>1</span><div><h2>Choose a trace source</h2><p>Validate a platform configuration or import trace data directly.</p></div></div><small>SDK SCHEMA 1.0 · METADATA FIRST</small></div>
        <div className={styles.tabs}><button className={tab==="providers"?styles.selected:""} onClick={()=>setTab("providers")}>Connect a platform <span>6</span></button><button className={tab==="json"?styles.selected:""} onClick={()=>setTab("json")}>Import JSON</button></div>
        {tab==="providers"?<><div className={styles.grid}>{providers.map(([name,icon,color,copy])=><button key={name} className={styles.provider} onClick={()=>setProvider({name,icon,color})}><b style={{background:color}}>{icon}</b><span><strong>{name}</strong><small>{copy}</small></span><i>→</i></button>)}</div><p className={styles.disclaimer}>Provider credentials are validated for shape only. Remote synchronization starts after each control-plane connector is deployed.</p></>:<div className={styles.importWrap}><button className={styles.drop} onClick={()=>inputRef.current?.click()} onDrop={e=>{e.preventDefault();chooseFile(e.dataTransfer.files)}} onDragOver={e=>e.preventDefault()}><input ref={inputRef} hidden type="file" accept=".json,.jsonl,application/json" onChange={e=>chooseFile(e.target.files)}/><b>{file?"✓":"↥"}</b><h3>{file?file.name:"Drop your trace data here"}</h3><p>{file?`${(file.size/1024).toFixed(1)} KB · Ready to normalize`:"or browse your computer"}</p><small>JSON or JSONL · Maximum 50 MB · OTLP supported</small></button><div className={styles.schema}><span>EXPECTED STRUCTURE</span><pre>{`{\n  "trace_id": "tr_01...",\n  "spans": [{\n    "model": "gpt-5",\n    "input_tokens": 2840,\n    "latency_ms": 920\n  }]\n}`}</pre></div></div>}
      </section>
    </main>
    {provider?<><button className={styles.scrim} onClick={()=>setProvider(null)} aria-label="Close"/><aside className={styles.drawer}><button className={styles.close} onClick={()=>setProvider(null)}>×</button><div className={styles.drawerBrand}><b style={{background:provider.color}}>{provider.icon}</b><span><small>VALIDATE SOURCE</small><strong>{provider.name}</strong></span></div><h2>Prepare your execution history for PromptRail.</h2><p>Validate a read-only source configuration against the SDK contract. Credentials are not persisted by this preview.</p><form onSubmit={submit}><label>API key<input name="credential" required={provider.name!=="OpenTelemetry"} type="password" placeholder="••••••••••••••••"/></label><label>Project or workspace<input name="project" required placeholder="production-agent"/></label><label className={styles.check}><input name="metadata_only" type="checkbox" defaultChecked/><span><strong>Exclude prompt and response content</strong><small>Only metadata, usage, timing, and relationships.</small></span></label><button type="submit">Validate configuration <b>→</b></button></form></aside></>:null}
    {toast?<div className={styles.toast}><b>✓</b><span><strong>{toast.title}</strong><small>{toast.detail}</small></span></div>:null}
  </div>;
}
