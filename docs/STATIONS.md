# Remit — The 7 Stations (interview study, station by station)

The pipeline as an assembly line. For each station: what it does, why, the key
ideas, and the interview Q&A we drilled. No code — understanding + defensible
talking points. (Companion to BUSINESS.md, UNDERSTAND.md, INTERVIEW.md, PITCH.md.)

The flow: **Ingest → Parse/Extract → Match → Decide → Settle → Reconcile → Exceptions.**

---

## 🏭 Station 1 — INGEST

**What it does:** identify the incoming file and guard against duplicates.
**Outcome / handoff:** the file + a label ("835" or "PDF") + a verdict ("new, not a
duplicate"). The label tells Station 2 which reader to use. (Doesn't read contents
or touch money.)

**Why / how:**
- **Two jobs:** (1) detect the file type, (2) check we haven't processed it before.
- **Type detection:** uses the filename extension AND the content signature (an 835
  starts with `ISA`, a PDF with `%PDF`) — because filenames are unreliable; the
  bytes aren't.
- **Idempotency:** compute a content **hash** (SHA-256 fingerprint of the exact
  bytes). Seen that fingerprint before → reject as duplicate. Prevents
  **double-posting** the same payment (remittances get re-sent / retried).

**Key term:** **idempotency** = "running it again changes nothing."

**Q&A:**
- *Ingest's two jobs?* → Identify the file + de-dupe. Trust before action.
- *Why content not just filename?* → Filenames get renamed/lost; the byte signature
  (`ISA` / `%PDF`) is the reliable proof.
- *Why the hash matters?* → It's a fingerprint of the exact bytes, so re-sent files
  are caught → prevents double-posting payments. That's what makes it idempotent.

---

## 🏭 Station 2 — PARSE / EXTRACT

**What it does:** turn the raw file into clean structured data, and verify it before
trusting it.
**Outcome:** the **canonical model** (validated) + any flagged lines.

**Why / how — four ideas:**
1. **One canonical model.** Both readers produce the same nested shape:
   **Remittance → Claim → Line → Adjustment.** Money is **exact decimals, never
   floats** (float rounding would break "reconcile to the cent"). Built-in
   invariants (the math rules). Payoff: everything downstream is **source-agnostic** —
   a new payer format is a new reader, not a rewrite.
2. **835 → deterministic parser, NO AI.** It's already structured, so plain code
   splits/walks it. You don't use a probabilistic model where there's an exact
   answer (cheaper, faster, can't hallucinate). *(Ours is a small custom parser, not
   a library.)*
3. **PDF → vision AI.** PDFs are unstructured and every payer's layout differs; a
   vision model generalises across layouts where template-OCR breaks. *(We use
   Claude's vision model with **structured output** — forced into our schema — and a
   **confidence per field**. Digital PDFs with a text layer are read directly; only
   scanned ones hit the model.)*
4. **The verification gate (THE point).** Never trust an extracted number. Every line
   must satisfy **`billed = paid + adjustments`** (which is just the bucket rule:
   `billed = insurance paid + write-off + patient + secondary` — every dollar
   accounted for). Plus the AI's per-field confidence. Fail either → flagged to the
   human queue, NOT posted. **Fail-closed.**

**Scope:** Station 2 verifies the *reading* ("did I read it right?"), not the
*decision* ("what should we do?") — that's Station 4.

**🎯 Killer line:** "The AI's reading is a *claim, not a fact* — I fact-check it
against an arithmetic invariant that must hold for any real remittance, plus the
model's own confidence. A misread digit breaks the equation and gets flagged
instead of becoming a wrong payment. That's how you put an LLM near money: verify,
don't trust."

**Q&A:**
- *Walk me through reading an EOB.* → Two readers (835 = deterministic parser;
  PDF = vision AI) → one canonical shape → verify every line (`billed = paid +
  adjustments` + confidence) → fail-closed.
- *How do I know the AI won't post a wrong number?* → the killer line above.
- *Why not AI for everything, incl. structured files?* → Use AI only where needed;
  structured data has an exact answer, so deterministic code is better there.
- *What's the canonical model / why?* → one nested shape (remittance→claim→line→
  adjustment), exact-decimal money, invariants built in; makes the pipeline
  source-agnostic.

---

## 🏭 Station 3 — MATCH

**What it does:** link each remittance line back to the **original claim line the
office submitted** (the "open claim," booked at the billed amount).
**Outcome:** each line linked to its open claim line; unmatched lines flagged.

**Why:** a remittance says "we paid $X on procedure Y for patient Z." To post that,
you must know **which open charge it belongs to** — otherwise you can't apply the
payment to the right account/claim. Match = connecting the payment to the right
original bill.

**How:** a **match key** = (payer, patient, date of service, procedure/CDT, billed
amount), with small **tolerances** (billed within rounding; date exact). Find the
open claim line with that key.
- *Why a composite key, not just the claim ID?* → you match on the natural
  attributes of the line; it's robust even when an internal ID isn't reliable.

**Nuances (the edge cases that show depth):**
- **Split / partial payments:** a claim paid across two remittances → **accumulate**
  against the open line, **never overwrite** (insurer pays part now, part later).
- **Duplicate lines** (same procedure + amount twice in one claim) → consume each
  open instance once (a multiset), so both get matched.
- **Unmatched line → never force-matched → flagged** to the exception queue. This
  also catches **bundling/unbundling** (payer pays a different code combination than
  billed).

**Interview line:** "Match links each remittance line to the original claim line on
a composite key — payer, patient, date, procedure, amount — with tolerances. Split
payments accumulate instead of overwriting, duplicate lines consume distinct
instances, and anything it can't confidently match is flagged, never forced — which
also surfaces bundling where the payer pays a different code mix."

**Q&A:**
- *What does Match do / why needed?* → links the payment to the original open claim
  so it can be posted to the right account; you can't post a payment you can't trace
  to a charge.
- *What's the match key?* → payer + patient + date of service + procedure + billed,
  with tolerances.
- *Split payment — how handled?* → accumulate against the open line, never overwrite.
- *Can't match a line — what happens?* → flag it (unmatched_line exception); never
  force a match. Catches bundling too.
