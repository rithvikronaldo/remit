import React, { useEffect, useState } from "react";
import { api } from "./api.js";

const NAV = [
  { id: "Home", label: "Home" },
  { id: "Pipeline", label: "Pipeline" },
  { id: "Inbox", label: "Inbox" },
  { id: "Claims", label: "Claims" },
  { id: "Remittances", label: "Remittances" },
  { id: "Decisions", label: "Decisions" },
  { id: "Reconciliation", label: "Reconciliation" },
  { id: "Eval", label: "Eval" },
];

const ICONS = {
  Home: <><path d="M3 9.5 12 3l9 6.5"/><path d="M5 9v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/></>,
  Pipeline: <><path d="M3 12h4l3 8 4-16 3 8h4"/></>,
  Inbox: <><path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z"/></>,
  Claims: <><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M9 12h6M9 16h6"/></>,
  Remittances: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M8 13h8M8 17h5"/></>,
  Decisions: <><line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/></>,
  Reconciliation: <><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></>,
  Eval: <><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></>,
};
function NavIcon({ name }) {
  return (
    <svg className="nav-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      {ICONS[name] || null}
    </svg>
  );
}

export default function App() {
  const [tab, setTab] = useState("Home");
  const [remits, setRemits] = useState([]);
  const [selected, setSelected] = useState(null);
  const [uploading, setUploading] = useState("");
  const [excCount, setExcCount] = useState(0);

  const refresh = async () => {
    const r = await api.remittances();
    setRemits(r);
    if (r.length && !selected) setSelected(r[0].remittance_id);
    api.exceptions("open").then((x) => setExcCount(x.length)).catch(() => {});
    return r;
  };

  useEffect(() => { refresh().catch(() => {}); }, []);

  async function handleUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";                          // allow re-selecting the same file
    try {
      setUploading(`Ingesting ${file.name}…`);
      const up = await api.upload(file);           // Ingest: detect type + dedupe
      setUploading(`Processing ${up.remittance_id}…`);
      await api.process(up.remittance_id);         // run the full pipeline
      await refresh();
      setSelected(up.remittance_id);
      setTab("Pipeline");                          // jump to the flow so you watch it
      setUploading(`✓ Ingested ${file.name} (${up.source.toUpperCase()})`);
    } catch (err) {
      const msg = String(err.message || err);
      setUploading(msg.includes("409")
        ? "⚠ Duplicate — that exact file was already ingested (idempotency caught it)."
        : `✗ ${msg}`);
    }
  }

  async function loadSample(id) {
    const res = await api.loadSample(id);
    await refresh();
    setSelected(res.remittance_id);
    setTab("Pipeline");
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="side-brand"><span className="flower">✻</span><h1>Remit</h1></div>
        <nav className="side-nav">
          {NAV.map((n) => (
            <button key={n.id} className={n.id === tab ? "active" : ""} onClick={() => setTab(n.id)}>
              <NavIcon name={n.id} />
              <span>{n.label}</span>
              {n.id === "Inbox" && excCount > 0 && <span className="badge">{excCount}</span>}
            </button>
          ))}
        </nav>
      </aside>

      <div className="content">
        <div className="topbar">
          <span className="page-title">{tab}</span>
          <span className="spacer" />
          {uploading && <span className="upload-status">{uploading}</span>}
          <select value={selected || ""} onChange={(e) => setSelected(e.target.value)}>
            {remits.map((r) => (
              <option key={r.remittance_id} value={r.remittance_id}>{r.remittance_id} · {r.payer}</option>
            ))}
          </select>
          <label className="upload-btn">
            ⬆ Upload remittance
            <input type="file" accept=".835,.txt,.pdf,application/pdf" onChange={handleUpload} hidden />
          </label>
        </div>

        <div className="content-inner">
          <div className="howto">
            <b>How this works:</b> the office bills insurance (<b>Claims</b>) → insurance sends back a payment + explanation (<b>Remittances</b>) → the engine reads it, decides each line with deterministic rules + grounded AI (<b>Decisions</b>), records the money and proves it ties to the deposit to the cent (<b>Reconciliation</b>), and routes anything uncertain to a human (<b>Inbox</b>). <b>Pipeline</b> shows one remittance flowing through all of it; <b>Eval</b> scores the engine against known-correct answers.
          </div>
          <main>
            {tab === "Home" && <Home remits={remits} excCount={excCount} go={setTab} onLoadSample={loadSample} />}
            {tab === "Pipeline" && <Pipeline trn={selected} />}
            {tab === "Inbox" && <Exceptions onChange={() => api.exceptions("open").then((x) => setExcCount(x.length)).catch(() => {})} />}
            {tab === "Claims" && <Claims />}
            {tab === "Remittances" && <Remittances remits={remits} />}
            {tab === "Decisions" && <Decisions trn={selected} />}
            {tab === "Reconciliation" && <Reconciliation trn={selected} remit={remits.find((r) => r.remittance_id === selected)} />}
            {tab === "Eval" && <Eval />}
          </main>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone, onClick }) {
  return (
    <div className={"stat " + (tone || "") + (onClick ? " clickable" : "")} onClick={onClick}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

function Home({ remits, excCount, go, onLoadSample }) {
  const [samples, setSamples] = useState([]);
  const [loadingId, setLoadingId] = useState(null);
  useEffect(() => { api.samples().then(setSamples).catch(() => {}); }, []);
  async function pick(id) { setLoadingId(id); try { await onLoadSample(id); } finally { setLoadingId(null); } }
  const total = remits.length;
  const processed = remits.filter((r) => r.processed).length;
  const committed = remits.filter((r) => r.committed).length;
  const held = remits.filter((r) => r.processed && !r.committed).length;
  const reconciled = remits.filter((r) => r.tied).length;
  const sum = remits.reduce((a, r) => a + parseFloat(r.eft_amount || 0), 0);
  const money = "$" + sum.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (
    <div>
      <p className="caption">A control-tower view of everything Remit has processed in this batch.</p>
      {samples.length > 0 && (
        <div className="card">
          <div className="working-head"><span className="flower">✻</span><b>Try a sample batch</b></div>
          <p className="mut" style={{ margin: ".1rem 0 .8rem" }}>Load a ready-made claims + remittance cohort and watch Remit process it live.</p>
          <div className="sample-grid">
            {samples.map((s) => (
              <button key={s.id} className="sample-card" onClick={() => pick(s.id)} disabled={loadingId === s.id}>
                <span className="sample-title">{s.label}</span>
                <span className="mut" style={{ fontSize: "12px" }}>{s.payer} · {s.claims} claims</span>
                <span className="sample-desc">{loadingId === s.id ? "Loading…" : s.description}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="stats">
        <Stat label="Remittances" value={total} />
        <Stat label="Committed" value={committed} tone="ok" />
        <Stat label="Held for review" value={held} tone={held ? "warn" : ""} />
        <Stat label="In your inbox" value={excCount} tone={excCount ? "warn" : "ok"} onClick={() => go("Inbox")} />
        <Stat label="Total processed" value={money} />
      </div>
      <div className="card">
        <div className="working-head"><span className="flower">✻</span><b>Batch summary</b></div>
        <div className="summary-lines">
          <div className="sline"><span>Remittances processed</span><span className="num">{processed} / {total}</span></div>
          <div className="sline"><span>Reconciled to the cent</span><span className="num">{reconciled} / {total}</span></div>
          <div className="sline"><span>Committed (posted)</span><span className="num">{committed}</span></div>
          <div className="sline"><span>Held for review</span><span className="num">{held}</span></div>
          <div className="sline"><span>Items in your inbox</span><span className="num">{excCount}</span></div>
          <div className="sline"><span>Total amount processed</span><span className="num">{money}</span></div>
        </div>
      </div>
    </div>
  );
}

const STAGE_ICON = { Ingest: "📥", "Parse / Extract": "📄", Match: "🔗", Decide: "🧠", Settle: "💰", Reconcile: "✅", Exceptions: "🚩" };
const STAGE_VERB = { Ingest: "Reading the file", "Parse / Extract": "Extracting and checking the math", Match: "Matching lines to claims", Decide: "Deciding each line", Settle: "Recording the money", Reconcile: "Reconciling to the deposit", Exceptions: "Routing exceptions to review" };
const STAGE_SVG = {
  Ingest: <><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></>,
  "Parse / Extract": <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8M10 9H8"/></>,
  Match: <><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></>,
  Decide: ICONS.Decisions,
  Settle: <><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></>,
  Reconcile: ICONS.Reconciliation,
  Exceptions: <><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" y1="22" x2="4" y2="15"/></>,
};
function StageIcon({ name }) {
  return <svg className="task-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{STAGE_SVG[name] || null}</svg>;
}

function Pipeline({ trn }) {
  const [p, setP] = useState(null);
  const [active, setActive] = useState(0);
  const [runId, setRunId] = useState(0);
  useEffect(() => {
    if (!trn) { setP(null); return; }
    setP(null); setActive(0);
    api.pipeline(trn).then((data) => { setP(data); setActive(0); }).catch(() => setP(null));
  }, [trn, runId]);
  useEffect(() => {
    if (!p || active >= p.stages.length) return;
    const t = setTimeout(() => setActive((a) => a + 1), 700);
    return () => clearTimeout(t);
  }, [p, active]);
  if (!p) return <p className="mut">Select a remittance to watch it process.</p>;
  const done = active >= p.stages.length;
  return (
    <div className="working">
      <div className="working-head">
        <span className="flower">✻</span>
        <b>{done ? "Remit — finished" : "Remit working…"}</b>
        <span className="mut" style={{ marginLeft: "auto", fontSize: "13px" }}>{trn}</span>
        {done && <button className="linklike" style={{ marginLeft: ".75rem" }} onClick={() => setRunId((r) => r + 1)}>↻ replay</button>}
      </div>
      <div className="tasks">
        {p.stages.map((s, i) => {
          const state = active > i ? "settled" : active === i ? "working" : "upcoming";
          return (
            <div key={s.name} className={`task ${state} ${state === "settled" ? "s-" + s.status : ""}`}>
              <span className="task-status">
                {state === "working" && <span className="spinner" />}
                {state === "upcoming" && <span className="dashed-circle" />}
                {state === "settled" && (s.status === "done" ? <span className="check">✓</span> : <span className="warnmark">!</span>)}
              </span>
              <StageIcon name={s.name} />
              <span className="task-label">{s.name}<span className="mut"> — {state === "working" ? (STAGE_VERB[s.name] || "working") + "…" : s.detail}</span></span>
              <span className="task-right mut">{state === "settled" ? s.summary : state === "working" ? "working…" : "up next"}</span>
            </div>
          );
        })}
      </div>
      {done && (
        <>
          <div className={"outcome " + (p.committed ? "ok" : "held")} style={{ marginTop: "1rem" }}>
            {p.committed
              ? "✓ Reconciled and committed — every dollar accounted for, ties to the deposit."
              : "⏸ Held for review — money ties out, but flagged lines are waiting in the Exceptions queue."}
          </div>
          <p style={{ marginTop: ".6rem" }}>
            <a href={api.sourceUrl(trn)} target="_blank" rel="noreferrer">📄 View the original document (the EOB / 835 we read) →</a>
          </p>
        </>
      )}
    </div>
  );
}

function ClaimDetail({ detail, onClose }) {
  const { claim_id, records, error } = detail;
  return (
    <div className="card claim-detail">
      <div className="exc-head">
        <span className="exc-badge">Claim {claim_id}</span>
        <span className="mut">the full story for this claim</span>
        <button className="linklike" style={{ marginLeft: "auto" }} onClick={onClose}>✕ close</button>
      </div>
      {error && <p className="mut">{error}</p>}
      {!records && !error && <p>Loading…</p>}
      {records && (
        <table>
          <thead><tr><th>CDT</th><th>Billed</th><th>Ins paid</th><th>Write-off</th><th>Patient</th><th>Secondary</th><th>Action</th><th>Status</th></tr></thead>
          <tbody>
            {records.map((r, i) => (
              <tr key={i}>
                <td>{r.cdt_code}</td>
                <td className="num">${r.billed}</td>
                <td className="num">${r.insurance_paid}</td>
                <td className="num">${r.contractual_writeoff}</td>
                <td className="num">${r.patient_responsibility}</td>
                <td className="num">${r.secondary_responsibility}</td>
                <td><span className={"act act-" + r.action}>{r.action}</span></td>
                <td>{r.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Claims() {
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState("");
  const [detail, setDetail] = useState(null);
  const load = () => api.claims().then(setRows).catch(() => setRows([]));
  useEffect(() => { load(); }, []);
  async function handleUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";
    try {
      setStatus(`Loading ${file.name}…`);
      const res = await api.uploadClaims(file);
      await load();
      setStatus(`✓ Added ${res.added} claim lines · ${res.total_open_lines} open total. Now upload the matching remittance to see it match.`);
    } catch (err) {
      setStatus(`✗ ${String(err.message || err)}`);
    }
  }
  async function openClaim(claim_id) {
    setDetail({ claim_id, records: null, error: null });
    try {
      const recs = await api.claimSettlement(claim_id);
      setDetail({ claim_id, records: recs, error: null });
    } catch {
      setDetail({ claim_id, records: null, error: "No settlement yet — this claim hasn't been matched & processed (so there's no money story to show). Match a remittance to it first." });
    }
  }
  return (
    <div>
      <p className="caption">
        The office's <b>open claims</b> — what was billed to insurance and is waiting to be paid.
        This is the list the <b>Match</b> stage links each incoming remittance line back to.
        A remittance line with no matching claim here becomes an <i>unmatched_line</i> exception.
      </p>
      <div className="uploader" style={{ marginBottom: ".6rem" }}>
        <label className="upload-btn">⬆ Upload claims (JSON)
          <input type="file" accept=".json,application/json" onChange={handleUpload} hidden />
        </label>
        {status && <span className="upload-status">{status}</span>}
      </div>
      <p className="caption" style={{ marginTop: 0 }}>Tip: click a <b>Claim ID</b> to see that claim's full story — what each line settled to.</p>
      {detail && <ClaimDetail detail={detail} onClose={() => setDetail(null)} />}
      <table>
        <thead><tr><th>Claim</th><th>Patient</th><th>CDT</th><th>Date of service</th><th>Payer</th><th>Billed</th><th>Status</th></tr></thead>
        <tbody>
          {rows.map((c, i) => (
            <tr key={i}>
              <td><button className="linklike" onClick={() => openClaim(c.claim_id)}>{c.claim_id}</button></td><td>{c.patient_ref}</td><td>{c.cdt_code}</td>
              <td>{c.date_of_service}</td><td>{c.payer}</td>
              <td className="num">${c.billed}</td>
              <td><span className={"pill " + (c.status === "matched" ? "ok" : "held")}>{c.status}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Remittances({ remits }) {
  return (
    <div>
      <p className="caption">What <b>came back</b> from insurance (835 files or EOB PDFs). Each row is one payment; "Status" is whether it committed or is held for review. Use the dropdown (top right) to pick one and explore it in the other tabs.</p>
      <table>
        <thead><tr><th>TRN</th><th>Payer</th><th>Source</th><th>EFT</th><th>Claims</th><th>Tied</th><th>Status</th></tr></thead>
        <tbody>
          {remits.map((r) => (
            <tr key={r.remittance_id}>
              <td>{r.remittance_id}</td><td>{r.payer}</td><td>{r.source}</td>
              <td className="num">${r.eft_amount}</td><td className="num">{r.claims}</td>
              <td>{r.tied ? "✓" : "—"}</td>
              <td><span className={"pill " + (r.committed ? "ok" : "held")}>{r.committed ? "committed" : "held"}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Decisions({ trn }) {
  const [rows, setRows] = useState([]);
  useEffect(() => { if (trn) api.decisions(trn).then(setRows).catch(() => setRows([])); }, [trn]);
  return (
    <div>
      <p className="caption">The action chosen for <b>each adjustment</b> on the selected remittance. <b>Source</b> = <i>rules</i> (deterministic, no AI) or <i>rag</i> (grounded AI, for denials) — RAG rows carry a confidence and a <b>citation</b> (the document it grounded on). Each row ties to a claim via <b>Claim ID</b> (see the Claims tab).</p>
      <table>
        <thead><tr><th>Claim</th><th>CDT</th><th>Adj</th><th>Amount</th><th>Action</th><th>Source</th><th>Conf</th><th>Citations</th></tr></thead>
        <tbody>
          {rows.map((d, i) => (
            <tr key={i} className={d.escalated ? "row-esc" : ""}>
              <td>{d.claim_id}</td><td>{d.cdt_code}</td><td>{d.group_code}-{d.reason_code}</td>
              <td className="num">{d.amount}</td>
              <td><span className={"act act-" + d.action}>{d.escalated ? "escalate" : d.action}</span></td>
              <td>{d.source}</td>
              <td className="num">{d.confidence ?? "—"}</td>
              <td className="cites">{(d.citations || []).map((c) => <code key={c}>{c}</code>)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Reconciliation({ trn, remit }) {
  const [r, setR] = useState(null);
  useEffect(() => { if (trn) api.reconciliation(trn).then(setR).catch(() => setR(null)); }, [trn]);
  if (!r) return <p>Select a processed remittance.</p>;
  const committed = remit?.committed;
  return (
    <div className="recon">
      <p className="caption">Proof the money is right: recorded payments + provider-level adjustments must equal the actual deposit (EFT), to the cent. The banner is the money check; the box below is the overall outcome (committed or held).</p>
      <div className={"banner " + (r.tied ? "ok" : "break")}>
        MONEY: {r.tied ? "RECONCILED" : "BREAK"} · delta ${r.delta}
      </div>
      <table>
        <tbody>
          <tr><td>Σ insurance paid</td><td className="num">${r.sum_insurance_paid}</td></tr>
          <tr><td>PLB (provider-level adj.)</td><td className="num">${r.plb_amount}</td></tr>
          <tr><td>EFT (BPR)</td><td className="num">${r.eft_amount}</td></tr>
          <tr><td>Delta</td><td className="num">${r.delta}</td></tr>
        </tbody>
      </table>
      <div className={"outcome " + (committed ? "ok" : "held")} style={{ marginTop: ".75rem" }}>
        {committed
          ? "✓ Committed — money ties out and every line cleared."
          : "⏸ Held for review — money ties out, but the remittance can't post until the flagged lines are resolved (see Exceptions)."}
      </div>
      {r.exceptions.map((e, i) => <p key={i} className="exc">⚠ {e.reason}: {e.detail}</p>)}
    </div>
  );
}

const REASON_LABEL = {
  low_confidence: "Low confidence",
  unmatched_line: "Unmatched line",
  extraction_arithmetic_break: "Math mismatch",
  reconciliation_break: "Reconciliation break",
};

function Evidence({ ev }) {
  if (!ev) return null;
  const fmt = (v) => (String(v).startsWith("-") ? "-$" + String(v).slice(1) : "$" + v);
  const money = [
    ["Billed", ev.billed], ["Insurance paid", ev.insurance_paid],
    ["Write-off", ev.contractual_writeoff], ["Patient", ev.patient_responsibility],
    ["Secondary", ev.secondary_responsibility], ["Appealed (open)", ev.appealed_open],
    ["Other adj.", ev.other_adjustments],
  ].filter(([, v]) => v !== undefined && v !== null);
  const hasOther = ev.other_adjustments !== undefined && parseFloat(ev.other_adjustments) !== 0;
  return (
    <div className="ev">
      {ev.detail && <p className="ev-detail">{ev.detail}</p>}
      {money.length > 0 && (
        <div className="ev-grid">
          {money.map(([k, v]) => (
            <div key={k} className="ev-item"><span className="ev-k">{k}</span><span className="ev-v">{fmt(v)}</span></div>
          ))}
        </div>
      )}
      {hasOther && <p className="ev-note">"Other adj." is an adjustment the engine couldn't auto-settle (e.g. an overpayment, or a prior-payer/COB impact). It balances the line back to Billed and is exactly why this line is held for a human.</p>}
      {(ev.line_exceptions || []).map((le, idx) => (
        <p key={idx} className="ev-note">⚠ {REASON_LABEL[le.reason] || le.reason}: {le.detail}</p>
      ))}
    </div>
  );
}

function ReviewPanel({ item, onResolve }) {
  const isReview = item.recommended_action === "review";              // engine has no confident call
  const contractual = parseFloat(item.evidence?.contractual_writeoff || 0) > 0;  // CO/PI portion present
  return (
    <div>
      <div className="prepared">
        {isReview
          ? "✻ Remit couldn't decide this confidently — it's your call."
          : "✻ Remit prepared a recommendation — review it, then approve or override."}
      </div>
      <div className="exc-head">
        <span className={"exc-badge b-" + item.reason}>{REASON_LABEL[item.reason] || item.reason}</span>
        {(item.claim_id || item.cdt_code) && <span className="exc-claim">{item.claim_id} {item.cdt_code}</span>}
      </div>
      <Evidence ev={item.evidence} />
      <p className="mut" style={{ marginTop: ".6rem" }}>
        {isReview
          ? "No confident recommendation — choose how to settle this line:"
          : <>Recommended action: <b>{item.recommended_action}</b></>}
      </p>
      <div className="review-actions">
        {!isReview && (
          <button className="btn-approve" onClick={() => onResolve(item.id, "accept")}>
            ✓ Approve ({item.recommended_action})
          </button>
        )}
        <button className={isReview ? "btn-approve" : ""} onClick={() => onResolve(item.id, "override", "contractual_writeoff")}>
          {isReview ? "Write off" : "Override → write-off"}
        </button>
        {contractual ? (
          <span className="blocked-note" title="A contractual (CO) adjustment can never be balance-billed to the patient.">
            🔒 Bill patient — blocked
          </span>
        ) : (
          <button onClick={() => onResolve(item.id, "override", "bill_patient")}>
            {isReview ? "Bill patient" : "Override → bill patient"}
          </button>
        )}
      </div>
      {contractual && (
        <p className="ev-note" style={{ marginTop: ".55rem" }}>
          🔒 Billing the patient is blocked here: this line carries a contractual (CO) adjustment, which can never be balance-billed to the patient — the same hard rule the engine enforces.
        </p>
      )}
    </div>
  );
}

function Exceptions({ onChange }) {
  const [items, setItems] = useState([]);
  const [sel, setSel] = useState(null);
  const load = () => api.exceptions("open").then((x) => {
    setItems(x);
    setSel((s) => (x.some((i) => i.id === s) ? s : (x[0]?.id ?? null)));
  }).catch(() => setItems([]));
  useEffect(() => { load(); }, []);
  const resolve = async (id, decision, action) => { await api.resolve(id, decision, action); load(); onChange?.(); };
  const selected = items.find((i) => i.id === sel) || null;

  if (items.length === 0) {
    return (
      <div>
        <p className="caption">Your <b>inbox</b> — everything the engine flagged for a human.</p>
        <div className="prepared">✓ Inbox zero — nothing needs review right now.</div>
      </div>
    );
  }
  return (
    <div>
      <p className="caption">Your <b>inbox</b> — everything the engine flagged for a human. Remit drafts a recommendation for each; you approve it or override.</p>
      <div className="inbox">
        <div className="inbox-list">
          {items.map((i) => (
            <button key={i.id} className={"inbox-row " + (i.id === sel ? "active" : "")} onClick={() => setSel(i.id)}>
              <span className={"exc-badge b-" + i.reason}>{REASON_LABEL[i.reason] || i.reason}</span>
              <span className="row-sub">{i.claim_id || "—"} {i.cdt_code || ""} · rec: {i.recommended_action}</span>
            </button>
          ))}
        </div>
        <div className="inbox-detail">
          {selected ? <ReviewPanel item={selected} onResolve={resolve} /> : <p className="mut">Select an item.</p>}
        </div>
      </div>
    </div>
  );
}

function Eval() {
  const [m, setM] = useState(null);
  return (
    <div>
      <p className="caption">The "how do we know it works" check: scores the engine against a golden set of known-correct answers — decision accuracy, reconcile pass-rate, exception precision/recall, latency. <b>meets_targets</b> is the regression gate; it must stay true or the build fails.</p>
      <button className="btn" onClick={() => api.runEval().then(setM).catch(() => {})}>Run pipeline eval</button>
      {m && (
        <table><tbody>
          {Object.entries(m).filter(([, v]) => typeof v !== "object").map(([k, v]) => (
            <tr key={k}><td>{k}</td><td className="num">{String(v)}</td></tr>
          ))}
        </tbody></table>
      )}
    </div>
  );
}
