import React, { useEffect, useState } from "react";
import { api } from "./api.js";

const TABS = ["Pipeline", "Remittances", "Decisions", "Reconciliation", "Exceptions", "Eval"];

export default function App() {
  const [tab, setTab] = useState("Pipeline");
  const [remits, setRemits] = useState([]);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    api.remittances().then((r) => {
      setRemits(r);
      if (r.length && !selected) setSelected(r[0].remittance_id);
    }).catch(() => {});
  }, []);

  return (
    <div className="app">
      <header>
        <h1>Remit</h1>
        <span className="sub">AI remittance adjudication — read-only dashboard</span>
      </header>

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
        {tab === "Remittances" && <Remittances remits={remits} />}
        {tab === "Decisions" && <Decisions trn={selected} />}
        {tab === "Reconciliation" && <Reconciliation trn={selected} />}
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
    </div>
  );
}

function Remittances({ remits }) {
  return (
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
  );
}

function Decisions({ trn }) {
  const [rows, setRows] = useState([]);
  useEffect(() => { if (trn) api.decisions(trn).then(setRows).catch(() => setRows([])); }, [trn]);
  return (
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
  );
}

function Reconciliation({ trn }) {
  const [r, setR] = useState(null);
  useEffect(() => { if (trn) api.reconciliation(trn).then(setR).catch(() => setR(null)); }, [trn]);
  if (!r) return <p>Select a processed remittance.</p>;
  return (
    <div className="recon">
      <div className={"banner " + (r.tied ? "ok" : "break")}>
        {r.tied ? "RECONCILED" : "BREAK"} · delta ${r.delta}
      </div>
      <table>
        <tbody>
          <tr><td>Σ insurance paid</td><td className="num">${r.sum_insurance_paid}</td></tr>
          <tr><td>PLB</td><td className="num">${r.plb_amount}</td></tr>
          <tr><td>EFT (BPR)</td><td className="num">${r.eft_amount}</td></tr>
          <tr><td>Delta</td><td className="num">${r.delta}</td></tr>
          <tr><td>Held</td><td>{r.held ? "yes" : "no"}</td></tr>
        </tbody>
      </table>
      {r.exceptions.map((e, i) => <p key={i} className="exc">⚠ {e.reason}: {e.detail}</p>)}
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
      <p>{items.length} open</p>
      {items.map((i) => (
        <div key={i.id} className="card">
          <div><b>{i.reason}</b> · {i.claim_id} {i.cdt_code} · recommend <i>{i.recommended_action}</i></div>
          <pre>{JSON.stringify(i.evidence, null, 2)}</pre>
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
