import React, { useEffect, useState } from "react";
import { api } from "./api.js";

const TABS = ["Pipeline", "Claims", "Remittances", "Decisions", "Reconciliation", "Exceptions", "Eval"];

export default function App() {
  const [tab, setTab] = useState("Pipeline");
  const [remits, setRemits] = useState([]);
  const [selected, setSelected] = useState(null);
  const [uploading, setUploading] = useState("");

  const refresh = () =>
    api.remittances().then((r) => {
      setRemits(r);
      if (r.length && !selected) setSelected(r[0].remittance_id);
      return r;
    });

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

  return (
    <div className="app">
      <header>
        <div className="brand">
          <h1>Remit</h1>
          <span className="sub">AI remittance adjudication engine</span>
        </div>
        <div className="uploader">
          <label className="upload-btn">
            ⬆ Upload remittance (835 / PDF)
            <input type="file" accept=".835,.txt,.pdf,application/pdf" onChange={handleUpload} hidden />
          </label>
          {uploading && <span className="upload-status">{uploading}</span>}
        </div>
      </header>

      <div className="howto">
        <b>How this works:</b> the office bills insurance (<b>Claims</b>) → insurance sends back a payment + explanation (<b>Remittances</b>) → the engine reads it, decides each line with deterministic rules + grounded AI (<b>Decisions</b>), records the money and proves it ties to the deposit to the cent (<b>Reconciliation</b>), and routes anything uncertain to a human (<b>Exceptions</b>). <b>Pipeline</b> shows one remittance flowing through all of it; <b>Eval</b> scores the engine against known-correct answers.
      </div>

      <nav>
        {TABS.map((t) => (
          <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
        <select value={selected || ""} onChange={(e) => setSelected(e.target.value)}>
          {remits.map((r) => (
            <option key={r.remittance_id} value={r.remittance_id}>{r.remittance_id} · {r.payer}</option>
          ))}
        </select>
      </nav>

      <main>
        {tab === "Pipeline" && <Pipeline trn={selected} />}
        {tab === "Claims" && <Claims />}
        {tab === "Remittances" && <Remittances remits={remits} />}
        {tab === "Decisions" && <Decisions trn={selected} />}
        {tab === "Reconciliation" && <Reconciliation trn={selected} remit={remits.find((r) => r.remittance_id === selected)} />}
        {tab === "Exceptions" && <Exceptions />}
        {tab === "Eval" && <Eval />}
      </main>
    </div>
  );
}

function Pipeline({ trn }) {
  const [p, setP] = useState(null);
  useEffect(() => { if (trn) api.pipeline(trn).then(setP).catch(() => setP(null)); }, [trn]);
  if (!p) return <p>Select a remittance.</p>;
  const icons = { Ingest: "📥", "Parse / Extract": "📄", Match: "🔗", Decide: "🧠",
                  Settle: "💰", Reconcile: "✅", Exceptions: "🚩" };
  return (
    <div className="pipe">
      <p className="caption">
        One insurance remittance, left → right through every stage. This is the whole workflow:
        read it in, interpret each coded line (rules for the routine ones, grounded AI for denials),
        record the money, prove it ties to the deposit, and hand anything uncertain to a human.
      </p>
      <div className="flow">
        {p.stages.map((s, i) => (
          <React.Fragment key={s.name}>
            <div className={"stage " + s.status}>
              <div className="ic">{icons[s.name] || "•"}</div>
              <div className="nm">{s.name}</div>
              <div className="sm">{s.summary}</div>
              <div className="dt">{s.detail}</div>
            </div>
            {i < p.stages.length - 1 && <div className="arrow">→</div>}
          </React.Fragment>
        ))}
      </div>
      <div className={"outcome " + (p.committed ? "ok" : "held")}>
        {p.committed
          ? "✓ Reconciled and committed — every dollar accounted for, ties to the deposit."
          : "⏸ Held for review — money ties out, but a flagged line is waiting in the Exceptions queue."}
      </div>
      <p style={{ marginTop: ".6rem" }}>
        <a href={`/api/remittances/${trn}/source`} target="_blank" rel="noreferrer">📄 View the original document (the EOB / 835 we read) →</a>
      </p>
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
  const money = [
    ["Billed", ev.billed], ["Insurance paid", ev.insurance_paid],
    ["Write-off", ev.contractual_writeoff], ["Patient", ev.patient_responsibility],
    ["Secondary", ev.secondary_responsibility], ["Appealed (open)", ev.appealed_open],
  ].filter(([, v]) => v !== undefined && v !== null);
  return (
    <div className="ev">
      {ev.detail && <p className="ev-detail">{ev.detail}</p>}
      {money.length > 0 && (
        <div className="ev-grid">
          {money.map(([k, v]) => (
            <div key={k} className="ev-item"><span className="ev-k">{k}</span><span className="ev-v">${v}</span></div>
          ))}
        </div>
      )}
      {(ev.line_exceptions || []).map((le, idx) => (
        <p key={idx} className="ev-note">⚠ {REASON_LABEL[le.reason] || le.reason}: {le.detail}</p>
      ))}
    </div>
  );
}

function Exceptions() {
  const [items, setItems] = useState([]);
  const load = () => api.exceptions("open").then(setItems).catch(() => setItems([]));
  useEffect(() => { load(); }, []);
  const resolve = async (id, decision, action) => { await api.resolve(id, decision, action); load(); };
  return (
    <div>
      <p className="exc-count"><b>{items.length}</b> open · each is held for a human to accept the recommendation or override it.</p>
      {items.map((i) => (
        <div key={i.id} className="card">
          <div className="exc-head">
            <span className={"exc-badge b-" + i.reason}>{REASON_LABEL[i.reason] || i.reason}</span>
            {(i.claim_id || i.cdt_code) && <span className="exc-claim">{i.claim_id} {i.cdt_code}</span>}
            <span className="exc-rec">recommend: <b>{i.recommended_action}</b></span>
          </div>
          <Evidence ev={i.evidence} />
          <div className="actions">
            <button onClick={() => resolve(i.id, "accept")}>Accept ({i.recommended_action})</button>
            <button onClick={() => resolve(i.id, "override", "contractual_writeoff")}>Override → write-off</button>
            <button onClick={() => resolve(i.id, "override", "bill_patient")}>Override → bill patient</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function Eval() {
  const [m, setM] = useState(null);
  return (
    <div>
      <p className="caption">The "how do we know it works" check: scores the engine against a golden set of known-correct answers — decision accuracy, reconcile pass-rate, exception precision/recall, latency. <b>meets_targets</b> is the regression gate; it must stay true or the build fails.</p>
      <button onClick={() => api.runEval().then(setM).catch(() => {})}>Run pipeline eval</button>
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
