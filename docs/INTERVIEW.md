# Remit — Interview Prep & Talking Points

A study sheet for talking about this project. Read it top to bottom once, then
skim the **Q&A** before the call. Everything here is something you should be able
to say in your own words.

---

## 0. The honesty frame (say this up front — it helps you)
> "It's a portfolio project I architected and built end-to-end (with AI pair-programming), on **synthetic data**, with a **representative** knowledge base rather than the full code set. The point was to show how I'd approach *your* problem — the architecture and the safety design are real; the data is illustrative."

Recruiters trust "here's my thinking, end to end" far more than someone pretending it's production. It also pre-empts the "is this real?" question.

---

## 1. The 30-second pitch
> "Remit is an AI engine for dental revenue cycle. It ingests insurance remittances — the 835 files and scanned EOB PDFs that say what the payer paid, adjusted, and denied — and **interprets** every coded adjustment into the right financial action: write-off, bill the patient, bill the secondary insurer, or appeal. The interpretation uses retrieval-augmented generation, so every decision is **grounded in a knowledge base, cited, and confidence-scored**. Routine codes are handled by deterministic rules; only the hard cases touch the model; anything uncertain escalates to a human. Then it settles each line and **reconciles the total to the cent** against the actual deposit."

## 2. The one-sentence "why it matters"
> "Processing remittances is the most labor-intensive, judgment-heavy task in a dental office's billing — Remit auto-handles ~90% confidently and hands a human just the few that need judgment, **with its reasoning shown**, and never silently gets the money wrong."

---

## 3. The flow (be able to draw this)
```
upload → ingest → parse(835) / extract(PDF) → match → DECIDE (rules + RAG) → settle → reconcile
                                                          │                              │
                                                          └──────────► exception queue ◄─┘
```
1. **Ingest** — detect file type, hash it (so the same file can't be processed twice).
2. **Parse / Extract** — 835 is structured → deterministic parser; PDF → vision LLM. **Both pass the same arithmetic check.**
3. **Match** — link each line to the original claim the office submitted.
4. **Decide** — *the core* (see §4).
5. **Settle** — record per line: paid / written-off / patient / secondary. Every dollar lands somewhere.
6. **Reconcile** — prove `Σ paid + provider adjustments == the EFT deposit`. Off by a cent → hard stop.
7. **Exceptions** — anything fail-closed lands here with evidence + a recommended action.

---

## 4. The core: the 3-tier decision cascade (the part that impresses)
> "The key idea is **don't let an AI guess about money.** So decisions cascade through three tiers:"

1. **Rules** — unambiguous codes (`CO-45 → contractual write-off`) resolve via a lookup table. No model, zero risk, handles the bulk.
2. **Retrieve (grounding gate)** — for everything else, look up the relevant code definitions + payer rules from the knowledge base. *No grounding found → escalate, never improvise.*
3. **LLM, on a leash** — the model only sees denials/ambiguous cases, returns a **structured** decision (not free text), and must justify it **only** from the retrieved context.

**The three guardrails (memorize these — they're the safety story):**
- **Citation containment** — the model's cited sources must be a subset of what retrieval actually returned. Kills hallucinated citations.
- **CO balance-bill block** — a contractual adjustment can *never* become "bill the patient." A hard domain rule the model can't override.
- **Confidence gate** — below threshold → goes to a human with the draft attached, not into the books.

> "So the model's blast radius on money is tiny, and every automated decision is grounded, cited, and reversible."

---

## 5. Why each big decision (defend the design)
| They ask… | You say… |
|---|---|
| Why rules *and* RAG, not just the LLM? | Smaller blast radius, lower cost, lower latency, and the routine 90% is provably correct with zero model risk. |
| Why RAG instead of just prompting the model? | **Grounding + explainability.** Decisions are looked up, not recalled, so you can show *why* and extend competence by editing data, not code. |
| What if the model is wrong? | It fails **closed** — three guardrails + a confidence gate escalate to a human rather than mis-settle. |
| How do you know it actually works? | A **golden-set eval** scores decision/settlement/reconciliation accuracy every run, versioned and diffable, with a CI regression gate. Reconciliation to the cent is a hard gate. |
| How does it stay cheap? | Rules handle most lines (no API call); the rest are cached per `(adjustment, payer, procedure, corpus-version, prompt-version)`, so each unique situation calls the model once, ever. |
| How would you scale the knowledge? | Add documents to the corpus — the system grows by adding *knowledge*, not `if`-statements. |

---

## 6. Numbers & facts to cite
- Two ingestion paths (X12 **835** + vision-extracted **PDF**) converge on **one canonical schema** and the **same arithmetic gate**.
- Decision tiers measured: ~**rules handle the bulk, model sees only denials/ambiguous, rest escalate**.
- Eval on the demo fixture: decision / denial-action / citation / settlement / reconciliation all hit target; **regression gate passes**.
- **81 automated tests**, including dedicated tests for each guardrail and a parser **round-trip** (generate → write 835 → parse → must equal the original).
- Reconciliation is exact — a **one-cent** delta holds the whole remittance.

---

## 7. Domain vocabulary (so you sound fluent)
- **ERA / 835** — Electronic Remittance Advice; the X12 file format insurers send.
- **EOB** — Explanation of Benefits; the human-readable (often scanned) version.
- **CARC / RARC** — Claim Adjustment Reason Codes / Remark Codes (e.g. `45` exceeds fee schedule, `197` no prior auth).
- **Group codes** — `CO` (contractual, office absorbs), `PR` (patient owes), `OA`/`PI` (other/payer).
- **CDT** — dental procedure codes (`D1110` = adult cleaning).
- **Contractual write-off** — billed minus the allowed amount; forgiven by contract.
- **COB** — Coordination of Benefits (primary vs secondary insurer).
- **Reassociation / TRN** — matching the 835 to its actual bank deposit via a trace number.
- **Reconciliation** — proving the recorded amounts equal the money received.
- **Denial / appeal / recoupment** — payer refuses a line / you contest it / payer claws back a prior overpayment.

---

## 8. "What would you do next?" (shows product maturity)
- Validate the LLM tier against the deterministic baseline with a real API key on the full denial set.
- Expand the corpus to the full official CARC/RARC set and more payer rules.
- An **active-learning loop**: human resolutions in the exception queue feed back into the rules/corpus.
- Per-payer confidence-calibrated auto-settle thresholds.
- A real Practice-Management-System adapter behind the matching interface.

---

## 9. If they push on weaknesses (answer calmly)
- "Synthetic data" → "Yes — deliberate; it ships with a generator so there's no PHI. The PRD documents the production controls: BAA, encryption, least-privilege, audit logging, de-identification before any model call."
- "Representative corpus" → "Right — the architecture is built to extend by adding documents; scaling the data is a data task, not a code change."
- "Did you build all of it?" → use the honesty frame in §0.
