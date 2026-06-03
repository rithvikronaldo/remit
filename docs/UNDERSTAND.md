# Remit — Understand the System (no code, interview study guide)

Read this 3–4 times over the week. Goal: explain the whole thing in your own
words and survive cross-questions. No code here — concepts and the *why*.

---

## 1. What it is, in one breath
An AI engine that reads insurance remittances (the documents that say what a payer
paid/adjusted/denied on a dental claim), **interprets** every coded line into the
right financial action, records how each line settled, and **proves the totals
match the money actually deposited** — escalating anything uncertain to a human
with evidence.

## 2. The mental model (memorize this shape)
Two front doors, one trusted brain, one money-check, one safety net.
- **Two front doors:** a structured file (835) and a scanned PDF. Both become the
  *same internal format*.
- **One brain:** the decision layer — rules for the easy codes, grounded AI (RAG)
  for the hard ones.
- **One money-check:** settlement + reconciliation — every dollar accounted for,
  tied to the deposit to the cent.
- **One safety net:** the exception queue — anything uncertain goes to a human.

## 3. The journey of one remittance (the workflow — be able to narrate this)
1. **Ingest** — a file arrives. Detect whether it's an 835 or a PDF; hash it so the
   same file can't be processed twice (no double-paying).
2. **Parse / Extract** — turn it into one standard internal shape.
   - 835 → a deterministic parser (it's already structured; no AI needed).
   - PDF → a vision AI reads it. **Either way, the same math check runs:** billed
     must equal paid + adjustments. If it doesn't, that line is flagged, not trusted.
3. **Match** — link each line back to the original claim the office submitted
   ("this is Jane's cleaning from May 10"). Unmatched lines are flagged, never forced.
4. **Decide** *(the core)* — for each coded adjustment, work out the right action:
   write-off, bill patient, bill secondary insurer, appeal, or review. (See §4.)
5. **Settle** — record per line: insurance paid $X, wrote off $Y, patient owes $Z,
   secondary owes $W. Rule: **every billed dollar must land in some bucket.**
6. **Reconcile** — prove the recorded payments + provider-level adjustments equal
   the actual deposit (the EFT). Off by one cent → the whole remittance is **held**,
   not partially committed.
7. **Exceptions** — every flagged item lands in one queue with its evidence and a
   recommended action. A human accepts or overrides; that decision is recorded.

## 4. THE CORE — the decision cascade (this is what the interview is about)
The guiding principle: **don't let an AI guess about money.** So decisions pass
through three tiers, and most never reach the AI:

- **Tier 1 — Rules.** The unambiguous codes (e.g. "exceeds fee schedule → write-off",
  "coinsurance → bill the patient") are handled by a simple lookup table. No AI, no
  risk, instant. This is the *majority* of lines.
- **Tier 2 — Retrieval (the grounding gate).** For anything else, the system looks
  up the relevant code definitions and payer rules from a **knowledge base**. If it
  finds nothing relevant → it **escalates** rather than improvising. "No grounding,
  no decision."
- **Tier 3 — The AI, on a leash.** Only denials and genuine ambiguity reach the
  model. It returns a *structured* answer (not free text), justified **only** by the
  retrieved material.

**Why "retrieval" (RAG) and not just asking the AI?** Because retrieved answers are
**grounded** (from a real source, not the model's memory) and **explainable** (you
can show *why* — the citation). And you grow the system's competence by adding
documents to the knowledge base, not by rewriting code.

### The three guardrails (learn these cold — this is the safety story)
Any failure here → escalate to a human, never settle:
1. **Citation containment.** The AI's cited sources must be a subset of what was
   actually retrieved. If it cites something that wasn't there, it made it up → block.
2. **Contractual-balance-bill block.** A "contractual" adjustment (the office's
   contract with the insurer) can *never* be billed to the patient. Hard rule the AI
   cannot override.
3. **Confidence gate.** If the AI isn't confident enough, the line goes to a human
   *with the AI's draft attached* — not into the books.

## 5. Two ideas that make it credible
- **Grounded + cited + confidence-scored + fail-closed.** Every automated decision
  can show its source, and the system would rather stop than be wrong.
- **The knowledge is the product.** The "brain" is a corpus of code definitions,
  payer rules, and a denial playbook. Add knowledge → the system gets smarter, no
  code change. That's the extensibility story.

## 6. How it's implemented (conceptual — enough to answer "how did you build X?")
- **One service, one language.** A single Python service exposes an API; a small
  React dashboard reads from it. Not microservices — deliberately simple.
- **One canonical schema.** Both ingestion paths converge on a single internal
  data shape (remittance → claims → lines → adjustments). Everything downstream
  speaks that one shape, so the parser, the AI, and settlement don't each reinvent it.
- **The knowledge base.** Documents (code definitions, payer rules, playbooks) are
  turned into searchable vectors. Retrieval is **hybrid**: an *exact* lookup by code
  (the deterministic anchor — a 2-character code is useless to fuzzy-search) plus a
  *semantic* search for the payer-specific context.
- **The money records.** Per-line settlement records are **append-only and
  immutable** (corrections are new records), written **atomically** per remittance
  (all lines or none), and **idempotent** (re-running can't double-count).
- **Evaluation.** A "golden set" of synthetic remittances with hand-labelled correct
  answers. Every run is scored (decision accuracy, settlement accuracy, reconciles-or-
  not) and **versioned**, so a change that helps one thing but hurts another is
  caught *before* shipping. Reconciliation is a hard pass/fail gate.
- **Cost control.** Routine lines never call the AI; AI answers are cached, so each
  unique situation costs one call at most, ever.

## 7. Cross-question bank (the hard ones — rehearse answers out loud)
- **"Why not just give the whole thing to GPT-4?"** → Blast radius, cost, latency,
  and trust. Most lines are unambiguous — rules handle them perfectly and for free.
  The model's job is narrowed to the genuinely hard cases, where it's grounded and
  checked. Smaller surface for error near money.
- **"What happens when the model is wrong?"** → It fails closed. Three guardrails +
  a confidence gate divert it to a human with evidence, rather than mis-settling.
- **"How do you know it actually works?"** → A versioned golden-set eval with a
  regression gate; settlement and reconciliation must hit target or the build is red.
  Reconciliation to the cent is non-negotiable.
- **"How does it handle a scanned PDF vs a clean file?"** → Different readers, but
  the *same* arithmetic gate and the same downstream. The PDF reader emits a per-field
  confidence; low confidence → flagged.
- **"What's a denial and how do you handle it?"** → The payer refused the line. The
  system retrieves the denial playbook for that code and recommends appeal / write-off
  / bill-secondary — with the source shown — or escalates if unsure.
- **"How do you extend it to a new payer or code?"** → Add a document to the
  knowledge base. No code change. That's the core design choice.
- **"What about duplicates / split payments / reversals?"** → Duplicates caught by
  content hash at ingest. Split payments accumulate against the open claim, never
  overwrite. Reversals (negative claims) record a reversing entry, not a new charge.
- **"Is this real data?"** → No — synthetic, by design (no PHI). It ships with a
  generator that produces realistic, math-valid remittances plus the ground truth.
- **"Did you build it or did AI?"** → "I architected it and built it end-to-end with
  AI pair-programming. The design decisions — the cascade, the guardrails, the
  fail-closed reconciliation — are mine, and I can walk you through any of them."
- **"What would you do next / what's missing?"** → Validate the AI tier against the
  rules baseline on the full denial set with a live model; expand the corpus to the
  full official code set; an active-learning loop where human resolutions feed back
  into the rules/corpus; per-payer confidence thresholds; a real practice-management
  integration.

## 8. Vocabulary you must say fluently
835 (the electronic remittance file) · EOB (the human-readable version) · CARC/RARC
(adjustment reason codes) · group codes CO/PR/OA/PI (contractual / patient / other /
payer) · CDT (dental procedure codes) · contractual write-off (billed − allowed) ·
COB (coordination of benefits, primary vs secondary) · reassociation / TRN (matching
the file to its bank deposit) · reconciliation (proving recorded == received) ·
denial / appeal / recoupment · RAG (retrieval-augmented generation) · fail-closed.

## 9. The numbers to drop
Two ingestion paths → one schema · rules handle the bulk, AI sees only denials ·
reconciles to the cent (one-cent break = held) · ~80 automated tests incl. a
parser round-trip and per-guardrail coverage · golden-set eval hits 100% on the
demo fixture with the regression gate passing · runs at $0 by default (local
embeddings + a deterministic baseline; the real model activates with an API key).

## 10. Your 1-week study plan
- **Days 1–2:** read this + `INTERVIEW.md`. Be able to narrate §3 (the journey) and
  §4 (the cascade) without looking.
- **Days 3–4:** rehearse the `PITCH.md` 2-minute script out loud, and the §7 answers.
- **Day 5:** run `make demo` and click the dashboard Pipeline tab; connect what you
  see to the words.
- **Days 6–7:** mock interview — have someone (or me) fire §7 at you until automatic.

**The one thing to nail:** *"The interesting decision wasn't the AI — it was keeping
the AI out of 90% of the work, and making sure it fails closed and reconciles to the
cent."* If you believe and can defend that sentence, you're ready.
