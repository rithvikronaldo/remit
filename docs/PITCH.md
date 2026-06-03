# Remit — Spoken Pitch & Résumé Block

Two ready-to-use assets: a ~2-minute script to practice out loud, and résumé/LinkedIn bullets.

---

## ⭐ "WHY REMIT" — the anchor (memorize word-for-word)

> "Every billed dollar gets split into buckets — insurance pays some, some is written off by contract, some the patient owes, some goes to a secondary insurer. The codes tell you which bucket. The easy lines are obvious; the **denials are the hard, high-value judgment calls** — appeal or write off, worth real money, with deadlines. And at the end, it all has to **reconcile to the actual deposit, to the cent**. Doing that across hundreds of lines a week, by hand, is the bottleneck — that's the problem we solve."

**10-second version:** "It splits every dollar of a claim into the right bucket — paid, write-off, patient, secondary — automatically; the routine stuff by rules, the denials by grounded AI; and it reconciles to the cent. It clears the boring 90% so a biller only works the few high-value decisions."

**The compounding insight (sounds senior):** "Most lost revenue isn't from wrong math — it's from denials nobody had time to chase before the deadline. By clearing the routine 90% instantly, we give billers the time back to actually *work* the denials."

### Handling a denial (worth $1,200) — the model answer
- **Actions:** appeal (recover it if we had authorization), write off (accept the loss if we didn't), or bill a secondary insurer; sometimes correct-and-resubmit if it's just a missing document.
- **Why it matters more than a routine `CO-45`:** a `CO-45` is a deterministic $50 write-off, no decision. A denial is $1,200 of *recoverable* money, the right action depends on context (code, payer, paperwork), and it's on a clock — appeal deadlines (~90 days). High value, real judgment, time-sensitive.
- **Risk of ignoring it:** $1,200 of pure lost revenue that might've been fully recovered — multiplied across hundreds of denials = the AR backlog (tens of thousands stuck/lost). The opposite mistake — billing the *patient* for a contractual write-off — is a compliance violation with fines. Both directions cost real money; that's why denials are where the money and the risk both live.

---

## A. The 2-minute spoken script (practice this verbatim, then make it your own)

> "So one project I'm proud of is **Remit** — it's an AI engine for the dental revenue cycle, which is the exact problem space your team works in.
>
> The setup is: when a dental office bills insurance, the payer sends back a remittance — either a structured `835` file or a scanned EOB PDF — that says, line by line, what they paid, what they adjusted, and what they denied, all in terse codes like `CO-45` or a `197` denial. Someone has to read every one of those, figure out the right action — write it off, bill the patient, bill a secondary insurer, or appeal — and then prove the totals match the actual deposit. It's the most labor-intensive, judgment-heavy task in the whole billing cycle.
>
> My core insight was that the hard, valuable part isn't the bookkeeping — it's the **interpretation**. So I treated it as a knowledge problem and built it around one principle: *don't let an AI guess about money.*
>
> Decisions cascade through three tiers. The routine codes resolve through **deterministic rules** — no model, zero risk, and that's most of the volume. Anything trickier goes through **retrieval** — it looks up the real code definitions and payer rules from a knowledge base, so every decision is **grounded and cited**, not recalled from the model's memory. Only genuine denials and ambiguity reach the LLM, and even then it's on a leash: it returns a structured decision, and three guardrails catch it — it can't cite a source that wasn't retrieved, it can never bill a patient for a contractual adjustment, and if it's not confident it escalates to a human with its draft attached.
>
> Then it settles each line so every billed dollar is accounted for, and **reconciles to the cent** against the deposit — a one-cent mismatch holds the whole remittance rather than committing something wrong.
>
> I built it end-to-end — both ingestion paths, the RAG decision layer, settlement, reconciliation, an exception queue, an eval harness with a regression gate, an API, and a dashboard — with about eighty automated tests. It's on synthetic data with a representative knowledge base, so it's a portfolio piece, but the architecture and the safety design are exactly how I'd approach it for real. The thing I care most about is that it's **grounded, fails closed, and never silently gets the money wrong.**"

**Delivery notes:** ~320 words, lands around 2 minutes at a calm pace. Pause after "*don't let an AI guess about money*" — that's your hook. If they look engaged, offer to draw the flow.

---

## B. Résumé / LinkedIn bullets (pick 3–4)

**Project line:**
> **Remit — AI Remittance Adjudication Engine** · Python, FastAPI, PostgreSQL/pgvector, LangChain, Claude, React

- Built an end-to-end AI engine that ingests dental insurance remittances (X12 **835** + vision-extracted EOB PDFs) and interprets every coded adjustment/denial into the correct financial action using **retrieval-augmented generation** — grounded, cited, and confidence-scored.
- Designed a **rules-first → retrieval → LLM cascade** so deterministic rules resolve the routine majority at zero model risk and only denials/ambiguity reach the model — cutting cost, latency, and error blast-radius.
- Implemented **fail-closed guardrails** (citation containment, contractual-balance-bill block, confidence gate) so the system escalates uncertain cases to a human instead of mis-settling money.
- Guaranteed money correctness with per-line settlement accounting and **reconciliation to the cent** against the EFT deposit, enforced by a versioned golden-set eval and a CI regression gate.
- Shipped the full pipeline (ingest → parse/extract → match → decide → settle → reconcile → exception queue), a FastAPI service, and a read-only React dashboard, with **80+ automated tests** including a parser round-trip and per-guardrail coverage.

**Tight one-liner (for a summary section):**
> Built Remit, an AI remittance-adjudication engine that uses grounded, cited RAG to interpret insurance EOBs, handles routine codes with deterministic rules, reconciles payments to the cent, and fails closed to a human on anything uncertain.

---

## C. Cover-letter / "why I'm a fit" sentence
> "I recently built Remit, an AI engine for exactly this problem — interpreting insurance remittances with grounded, cited RAG and fail-closed guardrails — so I come in already fluent in the domain (835s, CARC/RARC, COB, reconciliation) and, more importantly, in how to apply AI responsibly to money-moving decisions."
