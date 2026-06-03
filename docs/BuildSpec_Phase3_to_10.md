# Remit — Build Spec: Phases 3–10

Companion to the Remit PRD (v3.0), the Phase 0/1 spec, and the Phase 2 spec. Phases 0–2 are the core (data, corpus, decision engine); these phases are the plumbing that feeds the decision engine real data and turn its output into settled, reconciled, demonstrable results. Each is concrete and consistent with the shared canonical model and `decide_adjustment()`.

---

## Phase 3 — 835 parser

**Goal.** Deterministically turn an X12 835 file into the canonical `Remittance` model. No LLM — the 835 is already structured.

**Approach.** Split into segments on `~`, elements on `*`, components on `:`. Validate the envelope (`ISA`/`GS`/`ST` … `SE`/`GE`/`IEA`) and control numbers, then walk the hierarchy:

| Segment | Maps to |
|---|---|
| `BPR` | `payment_method`, `eft_amount` |
| `TRN` | `trn` (reassociation key) |
| `N1*PR` / `N1*PE` | payer / payee |
| `CLP` | a `Claim` — id, status code, billed total, paid total, patient responsibility |
| `NM1*QC` | `patient_ref` |
| `SVC` | a `ClaimLine` — cdt code, billed, paid |
| `CAS` (claim- or line-level) | repeating `Adjustment` triples `(group, reason, amount)` |
| `DTM*472` | date of service |
| `PLB` | provider-level adjustment (held for reconciliation) |

**Nuances.** `CAS` repeats and can carry up to six triples per segment; parse all of them. `CLP` status codes map to canonical status (`1` primary, `2` secondary, `4` denied, `22` reversal). Compute the content hash here for ingestion idempotency.

**Validation gate.** Per line, assert `billed == paid + Σ adjustments`; failures become `extraction_arithmetic_break` exceptions rather than silently parsing.

**Library note.** A hand-rolled segment parser is ~150 lines and fully controllable; `pyx12` exists if you'd rather not. Hand-rolled is recommended for this scope.

**Definition of done.**
- [ ] Parses the Phase 0 fixtures and **round-trips**: `generator → 835 → parser → canonical` equals the generator's original `Remittance`. (This is the single best test you have — it proves both the writer and the parser at once.)
- [ ] Envelope/control-number validation rejects malformed files.
- [ ] Idempotency hash computed and duplicates rejected at ingestion.

---

## Phase 4 — PDF EOB extraction

**Goal.** Produce the same canonical model from a scanned/printed EOB PDF, with confidence scoring.

**Approach.** If the PDF has a text layer, pull it; otherwise OCR (rasterise + OCR engine). Then a vision-capable LLM with the `ClaimLine`/`Claim` schema as **hard structured output**, emitting a per-field confidence.

```python
extract_chain = vision_prompt | ChatAnthropic(model="claude-...", temperature=0)\
                                  .with_structured_output(ExtractedRemittance)
```

**The key discipline — never trust extraction blindly.** Apply the *identical* arithmetic gate used on the 835 path: `billed == paid + Σ adjustments` per line, and claim totals must sum. The model's claim is checked against the arithmetic invariant, not believed. Money fields below a confidence floor → flagged → exception.

**Definition of done.**
- [ ] Extracts the Phase 0 PDF fixtures into canonical models.
- [ ] Field extraction accuracy ≥ 98% on key money fields vs. the fixtures' ground truth.
- [ ] Arithmetic gate + low-confidence flagging route bad extractions to exceptions instead of settling.

---

## Phase 5 — Matching engine

**Goal.** Link each parsed/extracted line to the **open claim line** already in the system (the claim was booked at submission time, at the billed amount; Phase 0 seeds these alongside the remittance).

**Match key.** `(payer, patient_ref, date_of_service, cdt_code, billed)` with small tolerances (e.g. billed within rounding, DOS exact).

**Nuances.**
- **Split / partial payments:** a claim paid across two remittances — *accumulate* against the open line, never overwrite.
- **Bundling / unbundling:** payer pays a different code composition than billed — flag for review rather than forcing a match.
- An **unmatched line is never force-matched** → `unmatched_line` exception.

**Definition of done.**
- [ ] Matches clean fixtures at the expected rate; split payments accumulate correctly.
- [ ] Unmatched and ambiguous lines are flagged, not guessed.

---

## Phase 6 — Settlement recording

**Goal.** Turn matched lines + their per-adjustment decisions (from `decide_adjustment`, Phase 2) into one settlement record per line.

**Settlement record** (per line; billed `B`, paid `P`, contractual `W = Σ CO`, patient `R = Σ PR`, secondary `S`):

| Field | Value |
|---|---|
| `insurance_paid` | P |
| `contractual_writeoff` | W |
| `patient_responsibility` | R |
| `secondary_responsibility` | S (COB only, else 0) |
| `action` | from the decision |
| `status` | settled / appealed / queued |

**The check is arithmetic, not bookkeeping:** `P + W + R + S == B` — every billed dollar is accounted for. **Denied lines (`P = 0`)** settle per the decision: `appeal` keeps the amount open against insurance; `contractual_writeoff` records it as written off; `bill_secondary`/`bill_patient` route it accordingly.

**Guarantees.** Atomic per remittance (all lines commit or none); records append-only (corrections are new records, never edits); idempotent per `(remittance_id, claim_line_id)`; every record stamped with its `remittance_id` for audit.

**Definition of done.**
- [ ] Every settled line fully accounts for its billed amount; the run is idempotent on replay.
- [ ] Settlement accuracy = 100% vs. the golden set's `expected_settlement`.
- [ ] Denial dispositions settle correctly per decision.

---

## Phase 7 — Reconciliation

**Goal.** Prove the remittance ties to the money received.

**Checks.**
1. **Line conservation** re-asserted after settlement: `P + W + R + S == B` per line.
2. **Reassociation:** `Σ insurance_paid across lines  ==  BPR total  ==  EFT amount`, matched via `TRN`. Include any `PLB` provider-level adjustments in the tie-out.

A delta of any cent is a **hard fail** → `reconciliation_break` exception, and the remittance is **held, not partially committed** — run reconciliation inside (or as a pre-commit check on) the settlement transaction so a break never leaves half-settled claims.

**Definition of done.**
- [ ] 100% reconcile on balanced fixtures.
- [ ] Injected imbalances (overpayment, PLB mismatch, dropped line) are caught and held.

---

## Phase 8 — Exception queue + human-in-the-loop

**Goal.** A single triage surface for everything the pipeline refused to auto-process.

**Sources** (every fail-closed path lands here): `extraction_arithmetic_break`, `unmatched_line`, `no_grounding`, `low_confidence_decision`, `co_balance_bill_violation`, `reconciliation_break`.

**Each item carries:** `reason`, `confidence`, `evidence` (jsonb — the retrieved citations / the model's draft decision), and a `recommended_action`.

**Resolution.** `POST /exceptions/{id}/resolve` either **accepts** the recommendation (which then settles) or **overrides** it (operator picks the action → settles). The resolution is recorded and feeds the eval/golden set — the seed of an active-learning loop.

**Definition of done.**
- [ ] Every escalation path appears here with its evidence attached.
- [ ] Accept and override both settle correctly and idempotently.
- [ ] Resolutions are captured for later corpus/rule enrichment.

---

## Phase 9 — Eval harness + observability

**Goal.** Generalise Phase 2's `run_eval` from per-adjustment to the **whole pipeline**, and make every run traceable.

**Pipeline eval.** For each fixture: run ingest → parse/extract → match → decide → settle → reconcile, then compare the final settlement records, the per-line decisions, and the reconciliation result to the fixture's ground truth.

**Metrics** (PRD §5.2): decision & denial-action accuracy, citation coverage, extraction accuracy, settlement accuracy, reconciliation pass rate, exception precision/recall, latency. Tagged with `(model, prompt_version, corpus_version)` and persisted so any change is diffable.

**Regression gate (CI).** Decision, settlement, and reconciliation accuracy must meet target on the golden set or the build is red.

**Observability.** LangSmith traces for every chain (query, retrieved chunks, output, latency); structured logs end to end; a generated run report (JSON + a simple HTML view).

**Definition of done.**
- [ ] End-to-end eval covers every §14 edge case.
- [ ] CI fails on regression below target.
- [ ] Traces and a run report are viewable for any run.

---

## Phase 10 — Dashboard + polish

**Goal.** Make the whole thing legible and demoable in one command.

**Dashboard (React + Vite, read-only).**
- Run timeline: a remittance moving through the pipeline stages.
- Per-line decisions with **action, confidence, and citations** shown (the RAG layer made visible — this is the screen to demo).
- Settled amounts per claim with the arithmetic check (every billed dollar accounted for).
- Reconciliation status (tied / broken + delta).
- Exception queue with evidence and resolve actions.

**Polish.** README (one-command quickstart), `docker compose up` brings up the whole stack, a scripted end-to-end demo (`make demo`), and a 60-second narration: *remittance in → RAG interprets each line with citations → settled and reconciled to the cent → the one denial is queued.*

**Definition of done.**
- [ ] `docker compose up` → upload a fixture → watch it flow end to end in the UI.
- [ ] The decisions screen shows citations and confidence for every RAG decision.
- [ ] `make demo` runs the scripted narrative start to finish.

---

## The dependency spine, at a glance
```
0 data ─┐
1 corpus┼─► 2 decide ──► 6 settle ─► 7 reconcile ──► 9 eval ──► 10 dashboard
3 parse ┘        ▲          ▲             │
4 extract────────┘   5 match┘             └─► 8 exceptions (fed by every stage)
```
Build 0→1→2 first (the engine, provable on its own), then 3/4→5 to feed it real lines, then 6→7 to settle and reconcile, then 8 to catch everything else, then 9→10 to prove and present it.
