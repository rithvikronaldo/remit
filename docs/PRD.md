# PRD — Remit
### AI Remittance Adjudication Engine

| | |
|---|---|
| **Document** | Product Requirements Document |
| **Version** | 3.0 (standalone rewrite) |
| **Status** | Draft for build |
| **Author** | Rithvik (rithvikronaldo.dev) |
| **Type** | New standalone project (no external dependencies) |
| **Domain** | Dental Revenue Cycle Management (RCM) — remittance adjudication automation |
| **Core thesis** | An AI system that *reads, understands, and acts on* insurance remittances — grounded in retrieved domain knowledge, not hardcoded rules. |

---

## 1. Executive summary

Remit is a standalone AI engine that ingests insurance remittances (electronic ERA/835 files and scanned/printed EOBs), **interprets** what the payer did to each claim line, **decides** the correct financial action for every adjustment and denial — grounded in a retrieval-augmented knowledge base of the underlying coding and payer rules — and records how each line was settled (insurance paid, contractual write-off, patient responsibility) in a form that reconciles to the actual money received (the EFT).

The product is not primarily a bookkeeping tool. **The hard, valuable part — and the heart of this project — is the interpretation layer:** turning coded adjustments (`CO-45`, `PR-2`) and denials into the right action (write off, bill the patient, bill the secondary payer, appeal), and doing it with a Retrieval-Augmented Generation (RAG) system so every decision is grounded in domain knowledge, explainable with citations, and extensible by editing data rather than rewriting code.

This document specifies Remit end to end, with the AI/RAG decision layer and the domain semantics as the centrepiece, and a deliberately minimal settlement-and-reconciliation step as the downstream sink.

---

## 2. What this project is really about

Two things, stated plainly because they drive every design decision:

1. **Applied AI / RAG done credibly near money.** The engine reads messy, coded payer output and decides what it means. That decision must be grounded (retrieved from a real knowledge base), structured (a validated schema, never free text), cited (you can see *why*), confidence-scored, and safe (it escalates rather than guesses). This is the skill the project exists to demonstrate.
2. **Deep domain fluency.** RCM is a dense, jargon-heavy world — 835 segments, CARC/RARC codes, group codes, reassociation, COB, recoupments. Encoding that knowledge *as the RAG corpus* and reasoning over it correctly is the differentiator. The corpus is the product's brain, and it grows by adding knowledge, not by adding `if` statements.

Everything else — ingestion, settlement, the dashboard — exists to make those two things demonstrable end to end.

---

## 3. Scope

**In scope (v3.0):**
- One canonical internal remittance schema fed by two ingestion paths (835 parse, PDF EOB extraction).
- A RAG knowledge base over CARC/RARC codes, group codes, and payer adjudication rules.
- A hybrid decision layer (deterministic rules for the trivial cases, RAG for everything that requires interpretation).
- A minimal settlement record per line, with a hard reconciliation guarantee against the EFT.
- An exception queue with human-in-the-loop resolution.
- An evaluation harness with a labelled golden set and per-version accuracy metrics.

**Non-goals:**
- No external ledger or accounting system; settlement is a small internal record, purpose-built — not general-purpose bookkeeping.
- No live Practice Management System (PMS) integration.
- No real Protected Health Information (PHI) — **all data is synthetic.**
- No claim submission (837), eligibility (270/271), or prior authorisation.
- Not a production billing UI; the dashboard is a read-only demonstration surface.

---

## 4. Problem statement and background

When a dental practice submits a claim (an **837**) to a payer, the payer adjudicates it and returns a remittance explaining, line by line, what it paid, what it adjusted, and what the patient now owes. This arrives as either:

- an **ERA (Electronic Remittance Advice)** — an X12 **835** EDI transaction, structured and machine-readable; or
- an **EOB (Explanation of Benefits)** — a human-readable document, often a scanned PDF.

A billing specialist must then **interpret and record** it: for each line, note what insurance paid, what was written off, and what the patient now owes, and then **reconcile** the total against the actual electronic funds transfer (EFT). Denials and underpayments must be **worked** — appealed, billed to a secondary payer, billed to the patient, or written off.

The interpretation is the bottleneck. The adjustment codes are terse and context-dependent; the right action for `CO-45` differs from `PR-1` differs from a `CARC 197` denial, and it can depend on the payer and the procedure. This is precisely the judgement-heavy, knowledge-bound task that a grounded AI system is well suited to — and that a pile of brittle rules is not.

---

## 5. Goals, non-goals, and success outcomes

### 5.1 Goals
1. Correctly interpret every adjustment and denial on a remittance, grounded in retrieved domain knowledge.
2. Make every AI decision explainable (cited), structured, and confidence-gated.
3. Escalate rather than guess — the system fails closed.
4. Record the settled amounts per line and reconcile their total to the EFT.
5. Be measurably correct via an eval harness, and extensible by editing the knowledge base.

### 5.2 Success metrics (outcomes)

| Metric | Definition | Target (v3.0) |
|---|---|---|
| Decision accuracy | Correct action ÷ all adjudicated lines, vs. golden set | ≥ 95% |
| Denial-action accuracy | Correct action ÷ labelled denials | ≥ 95% |
| Citation coverage | RAG decisions carrying valid supporting citations | 100% |
| Extraction accuracy | Correct fields ÷ total (PDF path), key money fields | ≥ 98% |
| Reconciliation accuracy | Remittances tying to EFT to the cent ÷ balanced ones | 100% (hard gate) |
| Exception precision | Items correctly flagged ÷ items flagged | ≥ 90% |
| Latency | Median end-to-end per 20-claim remittance | < 60 s |

### 5.3 Demonstrable end state
A single run ingests a multi-claim remittance, the RAG layer interprets each line and prints a cited decision (`CO-45 → contractual write-off, per CARC 45 + payer fee-schedule rule`), the settled amounts are recorded and the remittance reconciles to the cent, the one ambiguous denial is queued with its evidence, and the eval report shows decision and settlement accuracy against the golden set.

---

## 6. Personas and use cases

### 6.1 Personas
- **Billing specialist (primary operator)** — wants only the genuinely ambiguous items in front of them; trusts the rest to settle automatically.
- **Practice owner (stakeholder)** — wants an accurate, current cash position.
- **RCM engineer (builder)** — wants the knowledge base, not the code, to be the thing that grows.

### 6.2 User stories
- **UC-1** Upload an ERA → every line interpreted and settled automatically.
- **UC-2** Upload a scanned EOB PDF → extracted and settled with the same accuracy.
- **UC-3** Open a denial → see the code decoded, the recommended action, and the cited reasoning.
- **UC-4** See each remittance tied to its EFT, with any break surfaced immediately.
- **UC-5** Run the eval harness → a per-version accuracy report before shipping a prompt or knowledge-base change.

---

## 7. The AI / RAG decision layer (core of the system)

This is the centre of the product. It answers, for every line: *what did the payer do, and what should happen as a result?*

### 7.1 Design principle — grounded, structured, gated
- **Grounded:** decisions are retrieved from a real knowledge base, not produced from the model's parametric memory. No retrieval support → no decision (escalate).
- **Structured:** the LLM returns a strict schema (action, rationale, citations, confidence) enforced via function-calling / JSON schema. Free text is never parsed into a financial action.
- **Gated:** below a confidence threshold, or with empty/weak retrieval, the line is escalated to the exception queue rather than auto-settled.

### 7.2 The cascade — rules first, retrieval second, LLM last
1. **Deterministic mapping** resolves the trivial, unambiguous `(group_code, reason_code)` pairs (e.g. `PR-1 → patient_deductible`) with zero model risk. This handles the bulk of volume.
2. **RAG** handles anything unknown, ambiguous, payer-specific, or a denial: retrieve the relevant CARC/RARC definitions and payer rules, then have the LLM produce a grounded, cited decision.
3. **The LLM never decides unconstrained** — its output is validated against the schema and its citations checked before it can influence a settlement.

This minimises the model's blast radius on money-moving decisions while keeping the system explainable and extensible.

### 7.3 The knowledge base (the RAG corpus — the product's brain)
- **CARC** (Claim Adjustment Reason Codes) and **RARC** (Remittance Advice Remark Codes) reference dictionaries.
- **Group codes:** `CO` (Contractual Obligation), `PR` (Patient Responsibility), `OA` (Other Adjustment), `PI` (Payer Initiated).
- **Payer-specific adjudication notes** and a **denial → next-action playbook** (appeal vs. write-off vs. bill patient vs. bill secondary).
- Chunked at code/clause granularity, embedded, and stored in `pgvector`. **Extending the system's competence = adding documents to this corpus**, not editing code — that extensibility is an explicit requirement.

### 7.4 Retrieval
- The query is the structured tuple `(group_code, reason_code, payer, cdt_code)` expanded into a retrieval string.
- Top-`k` clause retrieval with a similarity floor. Empty/weak retrieval → escalate, never improvise.

### 7.5 Decision output (validated)
```
{
  "action": "contractual_writeoff | bill_patient | bill_secondary | appeal | review",
  "rationale": "string",
  "citations": ["carc:45", "payer_rule:fee_schedule_v2"],
  "confidence": 0.0–1.0
}
```
- `confidence < threshold` → `low_confidence_decision` exception.
- `citations` must be non-empty for any RAG-derived decision; an uncited decision is rejected.

### 7.6 Observability
Every chain run emits a trace (query, retrieved chunks, model output, latency) via LangSmith. Decisions are logged with their evidence so a reviewer sees exactly why an action was recommended.

---

## 8. Domain reference — the business terms the engine reasons over

Treated as a first-class section because fluency here *is* the project. These terms define the corpus and the semantics the AI must get right.

### 8.1 The remittance documents
- **835** — the X12 EDI transaction carrying the ERA. Wrapped in `ISA`/`GS`/`ST` envelopes.
- **EOB** — the human-readable equivalent, often a scanned PDF.
- **837** — the original claim submission (context only; out of scope).

### 8.2 835 segment anatomy
- **BPR** — financial information: payment method (`ACH`/`CHK`/`NON`) and total payment amount.
- **TRN** — reassociation trace number; the key that links the 835 to its EFT deposit.
- **CLP** — claim payment information: claim status code, total charge, total paid, patient responsibility.
- **CAS** — claim adjustment: a repeating `(group code, reason code, amount)` triple — the thing the AI interprets.
- **SVC** — service-line payment: procedure code, line charge, line paid (with its own line-level `CAS`).
- **PLB** — provider-level adjustment: adjustments outside any single claim that still affect EFT reconciliation.

### 8.3 The coding systems
- **CDT** — Current Dental Terminology procedure codes (e.g. `D0120` periodic oral evaluation, `D1110` adult prophylaxis).
- **CARC** — Claim Adjustment Reason Codes (e.g. `45` = charge exceeds fee schedule; `197` = precert/authorisation absent).
- **RARC** — Remittance Advice Remark Codes; supplement a CARC.
- **Group codes** — `CO` (contractual, practice absorbs), `PR` (patient owes), `OA`, `PI`.

### 8.4 The money concepts
- **Billed** — what the practice charged.
- **Allowed** — what the payer's fee schedule permits.
- **Contractual write-off** — `billed − allowed`; forgone by contract (`CO` group).
- **Insurance paid** — what the payer actually paid.
- **Patient responsibility** — deductible / coinsurance / copay (`PR` group).
- **AR (Accounts Receivable)** — money owed to the practice; the backlog this engine clears.

### 8.5 The workflow concepts
- **Reassociation** — matching an 835 to its EFT via the TRN trace number.
- **COB (Coordination of Benefits)** — primary/secondary payer sequencing.
- **Recoupment / takeback** — a payer reclaiming a prior overpayment (negative `CLP`).
- **Reversal** — undoing a prior adjudication.

---

## 9. Functional pipeline

Linear pipeline; the decision layer (§7) is its centre of gravity.

1. **Ingest** — detect file type, persist the raw artifact, capture `TRN` and `BPR`, create the `remittance` record. Idempotent via content hash (no double-settlement).
2. **Extract / parse** —
   - *835 path:* deterministic X12 segment walk → canonical schema (no LLM — it's already structured).
   - *PDF path:* OCR/text-layer → vision LLM with strict structured output and per-field confidence.
   - *Validation gate (both):* `billed = paid + adjustment + patient_responsibility` per line; failures → exception.
3. **Match** — link each line to an open claim line by `(payer, patient_ref, date_of_service, cdt_code, billed)` with tolerance; accumulate split payments; unmatched → exception.
4. **Decide** — the AI/RAG cascade of §7; output is a validated, cited, confidence-scored disposition per line.
5. **Settle** — record the settled amounts per line (paid / write-off / patient / secondary) implied by the decisions; atomic per remittance (§10).
6. **Reconcile** — re-assert line conservation; reassociate `Σ insurance_paid == BPR total == EFT`; any delta is a hard fail → held, not partially committed.
7. **Exception queue** — every non-settleable item with reason, confidence, retrieved evidence, and recommended action; accept (settles) or override; resolutions feed the eval set.

---

## 10. The settlement record (lightweight, internal)

Deliberately minimal — just enough to record the outcome of each decision and prove the money adds up. No accounting system, no debits/credits, no general-purpose bookkeeping.

**One record per line.** For each processed claim line, store how the billed amount was settled: what insurance paid, what was written off, what the patient owes, what (if anything) goes to a secondary payer, and the decided action.

```
line_settlement:
  insurance_paid           # what the payer paid
  contractual_writeoff     # amount forgone by contract (Σ CO adjustments)
  patient_responsibility   # patient owes (Σ PR adjustments)
  secondary_responsibility # routed to a secondary payer (COB), if any
  action                   # from the decision layer
  status                   # settled | appealed | queued
```

**Two checks — arithmetic, not bookkeeping:**
- **Per line (fully accounted for):** `billed == insurance_paid + contractual_writeoff + patient_responsibility + secondary_responsibility`. Every billed dollar must land somewhere; a line that doesn't add up is an exception, not a settlement.
- **Per remittance (reconciliation):** `Σ insurance_paid across lines == EFT total`, matched via the `TRN` trace number (reassociation).

These are two `SUM()`-style checks, intentionally — the intelligence lives upstream in the decision layer. Settlement records are immutable and append-only (corrections are new records), each stamped with its `remittance_id` for audit, and writing them is idempotent per `(remittance_id, claim_line_id)`.

---

## 11. Technical architecture

Single AI-first service; one language, one repo.

```
        upload
          │
          ▼
   ┌───────────────────────────────────────────────────┐
   │              Remit service (Python + FastAPI)        │
   │                                                      │
   │  ingest → extract (835 parse | PDF vision LLM)       │
   │         → match → DECIDE (rules + RAG / LangChain)   │ ◄── pgvector
   │         → settle (record amounts) → reconcile        │     (CARC/RARC + payer corpus)
   │         → exception queue                            │
   └──────────────────────┬───────────────────────────────┘
                          │
                          ▼
                    PostgreSQL  (remittances, claims, lines, settlements, exceptions, eval cases)
```

### 11.1 Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Service / API | Python, FastAPI | One AI-first service |
| Persistence | PostgreSQL + `pgvector` | App data + vector corpus in one store |
| ORM / data | SQLModel (or SQLAlchemy) | Typed models, simple migrations |
| LLM orchestration | LangChain (LCEL) | Structured-output decision + extraction chains |
| Models | Claude (decisions + vision extraction); an embedding model for retrieval | Structured output / function-calling enforced |
| Observability | LangSmith traces + structured logs | Every chain run traceable |
| Frontend (optional) | React, Vite | Read-only run view, settlements, reconciliation, exceptions |
| Packaging | Docker / Docker Compose | One-command stack |

### 11.2 API surface (representative)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/remittances` | Upload + ingest; returns `remittance_id` and parsed lines |
| `POST` | `/remittances/{id}/process` | Run match → decide → settle → reconcile |
| `GET` | `/remittances/{id}/decisions` | Per-line AI decisions with citations and confidence |
| `GET` | `/remittances/{id}/reconciliation` | Reconciliation status, delta, breaks |
| `GET` | `/claims/{id}/settlement` | The settled amounts and action for a claim |
| `GET` | `/exceptions` | The triage queue |
| `POST` | `/exceptions/{id}/resolve` | Accept recommendation or override → settles |
| `POST` | `/eval/run` | Execute the golden-set harness; returns metrics |

---

## 12. Data model

PostgreSQL; `snake_case`; UUID primary keys; monetary values as `numeric` (never float).

- **`remittance`** — `id`, `payer`, `trn_trace_number`, `payment_method`, `eft_amount`, `paid_date`, `source_type` (`835`|`pdf`), `content_hash`, `status`.
- **`claim`** — `id`, `remittance_id` (fk), `patient_ref`, `date_of_service`, `clp_status_code`, `billed_total`, `status` (`open`|`settled`|`exception`).
- **`claim_line`** — `id`, `claim_id` (fk), `cdt_code`, `billed`, `allowed`, `paid`, `adjustment`, `group_code`, `carc_code` (fk), `patient_responsibility`, `extraction_confidence`.
- **`line_settlement`** — `id`, `claim_line_id` (fk), `insurance_paid`, `contractual_writeoff`, `patient_responsibility`, `secondary_responsibility`, `action`, `remittance_id` (audit), `settled_at`. Append-only / immutable.
- **`carc_code`** — `code` (pk), `group_code`, `description`, `recommended_action`. Source for the RAG corpus.
- **`knowledge_chunk`** — `id`, `source`, `content`, `embedding` (vector). The retrievable corpus.
- **`exception`** — `id`, `claim_line_id` (fk), `reason`, `confidence`, `evidence` (jsonb citations), `recommended_action`, `status`.
- **`eval_case`** — `id`, `input_ref`, `expected_decision` (jsonb), `expected_settlement` (jsonb). The golden set.

---

## 13. Evaluation methodology

A first-class part of the product.

- **Golden set:** synthetic remittances (835 and PDF) with hand-labelled expected *decisions* and *settlements*, spanning clean cases and every edge case in §14.
- **Metrics per run** (§5.2): decision accuracy, denial-action accuracy, citation coverage, extraction accuracy, settlement accuracy, reconciliation pass rate, exception precision/recall, latency.
- **Versioned:** each run tagged with pipeline/prompt/model/corpus version; results stored and diffable, so a corpus edit that improves denial accuracy but regresses extraction is caught before shipping.
- **Regression gate:** decision accuracy, settlement accuracy, and reconciliation must meet target on the golden set or the build is red.

---

## 14. Edge cases and domain nuances

Each is a labelled golden-set case — these separate a credible engine from a toy.

- **Denials (`CLP` status 4):** route per decided action (appeal / write-off / patient bill).
- **Reversals & recoupments:** negative `CLP` reverses a prior payment; record a reversing settlement, not a new charge.
- **Provider-level adjustments (`PLB`):** affect EFT reconciliation outside any single claim.
- **Interest payments:** reconcile without distorting claim-level AR.
- **Coordination of Benefits:** `CLP` status 2 → bill secondary, not patient.
- **Partial / split payments:** accumulate across remittances; never overwrite.
- **Bundling / unbundling:** payer pays a different code composition than billed.
- **Overpayments (`paid > allowed`):** flag, never silently absorb.
- **Capitation:** zero line payment by design, not a denial.
- **Duplicate remittance:** caught at ingestion via content hash.

---

## 15. Non-functional requirements

- **Grounded & explainable:** every AI decision is retrieved-and-cited; uncited or low-confidence decisions are blocked.
- **Fail-closed:** the engine escalates rather than mis-settling.
- **Money correctness:** reconciliation and settlement accuracy are hard gates (100% on golden set).
- **Auditability:** settlement records are immutable and append-only; every settlement traces to its source remittance.
- **Idempotency & transactionality:** ingestion and settlement are idempotent; a remittance settles atomically — all lines or none.
- **Observability:** structured logs end to end; LangSmith traces for every AI chain.
- **Extensibility:** new codes, payer rules, and a second remittance format extend the corpus/schema, not the control flow.
- **Compliance (designed-for, on synthetic data):** the system handles what would be PHI in production; v3.0 uses synthetic data only and documents the production controls it would require — BAA, encryption in transit/at rest, least-privilege access, audit logging, de-identification before any model call.

---

## 16. Phased build plan

Dependency-ordered; each phase independently demoable. (At ~8 focused hours/day, the AI decision path is reachable early; later phases harden and prove it.)

- **Phase 0 — Foundations.** Schema in Postgres; synthetic data generator emitting matched 835 files, PDF EOBs, and open claims. Foundation and test fixtures in one.
- **Phase 1 — Knowledge base + retrieval.** Build the CARC/RARC + payer-rule corpus; chunk, embed, load into `pgvector`; a retrieval function returning cited clauses. *Stand this up early — it's the core.*
- **Phase 2 — Decision layer.** The rules-first cascade; LangChain LCEL chain producing the structured, cited, confidence-scored decision; escalation gate. Evaluate against the golden set from day one.
- **Phase 3 — 835 parser.** Deterministic segment walk → canonical schema; envelope validation; TRN/BPR capture; idempotency hash.
- **Phase 4 — PDF EOB extraction.** OCR/text-layer + vision LLM, strict structured output, per-field confidence, arithmetic gate.
- **Phase 5 — Matching engine.** Match-key resolution with tolerance; split-payment accumulation; unmatched → exception.
- **Phase 6 — Settlement recording.** Record the settled amounts per line implied by the decisions; atomic per-remittance commit; immutable; idempotent.
- **Phase 7 — Reconciliation.** Line conservation re-assertion; TRN-keyed reassociation to EFT; hard-fail on any delta.
- **Phase 8 — Exception queue + human-in-the-loop.** Triage list with evidence and recommended action; accept/override; resolutions feed eval.
- **Phase 9 — Eval harness + observability.** Golden set across all §14 cases; per-version metrics; regression gate; LangSmith traces.
- **Phase 10 — Dashboard + polish.** React/Vite read-only surface; README; one-command Compose stack; scripted end-to-end demo and a 60-second narration.

---

## 17. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| LLM hallucination on decisions | Wrong settlement | Rules-first cascade; uncited/low-confidence decisions blocked; grounded retrieval only |
| Thin or wrong corpus | Bad decisions | Corpus is data — extend it; empty retrieval escalates rather than guesses |
| OCR/extraction noise on PDFs | Wrong money fields | Arithmetic validation gate + per-field confidence + escalation |
| Reconciliation edge cases (PLB, reversals) | Silent imbalance | Fail-closed; atomic commit; every edge case in the golden set |
| Scope creep into a full RCM platform | Never ships | Strict non-goals (§3); phased, demoable increments |

---

## 18. Future extensions
- Second (and Nth) remittance/payer format behind the same canonical schema.
- A real PMS adapter (e.g. Open Dental's API) behind the matching interface.
- Active-learning loop: exception resolutions enrich the corpus and the rules table.
- Confidence-calibrated auto-settlement thresholds tuned per payer.

---

## 19. Glossary

- **835** — X12 EDI transaction carrying the ERA.
- **837** — X12 claim submission (out of scope).
- **AR** — Accounts Receivable.
- **BAA** — Business Associate Agreement (HIPAA contract for handling PHI).
- **BPR** — 835 financial segment (payment method + total amount).
- **CARC** — Claim Adjustment Reason Code.
- **CAS** — 835 claim-adjustment segment: `(group code, reason code, amount)`.
- **CDT** — Current Dental Terminology procedure codes.
- **CLP** — 835 claim-payment-information segment.
- **COB** — Coordination of Benefits.
- **Contractual write-off** — billed minus allowed; forgone by contract.
- **EOB** — Explanation of Benefits (human-readable remittance).
- **ERA** — Electronic Remittance Advice (delivered as an 835).
- **EFT** — Electronic Funds Transfer (the deposit).
- **Group codes** — `CO`, `PR`, `OA`, `PI`.
- **PHI** — Protected Health Information.
- **PLB** — 835 provider-level-adjustment segment.
- **PMS** — Practice Management System.
- **RAG** — Retrieval-Augmented Generation.
- **RARC** — Remittance Advice Remark Code.
- **Reassociation** — matching an 835 to its EFT via the TRN trace number.
- **TRN** — 835 reassociation trace-number segment.
