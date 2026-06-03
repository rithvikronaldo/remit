# Remit — The Business Logic, as a Scenario

No tech here. This explains **what problem we solve and why it's worth money**,
by following real claims through a real dental office. If you only read one doc to
understand the *business*, read this.

---

## 1. The cast and the money flow

- **The practice** — a dental office. It does work, then wants to get paid.
- **The patient** — has dental insurance; pays part, insurance pays part.
- **The payer** — the insurance company (Delta Dental, Cigna, etc.).

The flow: practice treats patient → sends a **bill (a "claim")** to the payer →
payer decides what it'll cover → payer sends back a **remittance** (a statement)
*and* deposits money in the bank → practice has to figure out, for every line:
**who owes what, and did the money add up?**

That last step — reading the statement and squaring it with the deposit — is the
**most labor-intensive, error-prone job in the whole office**. That's our target.

---

## 2. A concrete scenario — Jane visits the dentist

Jane comes in. The office does three things and **bills** these amounts (what they
*charge*):

| Line | Procedure | Billed |
|---|---|---|
| 1 | Cleaning (D1110) | $150 |
| 2 | Exam (D0120) | $70 |
| 3 | Crown (D2740) | $1,200 |
| | **Total billed** | **$1,420** |

The office sends this $1,420 claim to Delta Dental and waits.

A week later, two things arrive: **a $612 deposit** in the bank, and **a
remittance** explaining it. The remittance, in plain terms, says:

| Line | Billed | Insurance "allowed" | Insurance paid | The codes |
|---|---|---|---|---|
| Cleaning | $150 | $100 | $80 | `CO-45 $50`, `PR-2 $20` |
| Exam | $70 | $50 | $40 | `CO-45 $20`, `PR-2 $10` |
| Crown | $1,200 | $0 | $0 | `CO-197 $1,200` (DENIED) |

**This is the moment of pain.** A human now has to decode every one of those codes
and decide what to *do*. Let's translate them — because **this translation is
exactly what the office struggles with, and exactly what Remit automates.**

---

## 3. What those codes actually mean (the interpretation)

**"Allowed" amount:** the insurer has a contracted price list. The office *charged*
$150 for the cleaning, but Delta's contract says a cleaning is only worth $100. That
$100 is the "allowed" amount.

**`CO-45 $50` (on the cleaning):**
- `CO` = *Contractual Obligation* → the practice agreed to this discount in its
  contract.
- `45` = "charge exceeds the fee schedule."
- **Meaning:** the $50 difference ($150 charged − $100 allowed) must be **written
  off**. The practice eats it. **Critically, it is ILLEGAL to bill Jane for this $50**
  (that's called balance-billing — a compliance violation that gets practices fined).

**`PR-2 $20` (on the cleaning):**
- `PR` = *Patient Responsibility*.
- `2` = "coinsurance" (Jane's share).
- **Meaning:** Jane owes this $20. **Send Jane a bill.**

So the cleaning resolves as: **insurance paid $80 + write off $50 + Jane owes $20 =
$150.** Every dollar accounted for. ✓

**`CO-197 $1,200` (the crown) — the expensive one:**
- `197` = "precertification/authorization absent."
- **Meaning:** the crown needed *prior approval* from Delta before the work was done,
  and the office didn't get it. So Delta paid **$0**.
- **Now what?** This is a judgment call worth **$1,200**:
  - If the office *did* get authorization (just forgot to attach it) → **appeal**,
    and likely recover the $1,200.
  - If they never got authorization → it's probably a **write-off** (lost money),
    and again, **they cannot bill Jane for it**.

**This single decision is where real money lives.** Get it wrong and you either
leave $1,200 on the table (never appealing) or illegally bill the patient.

---

## 4. Now multiply this by reality

That was **one patient**. A practice gets **hundreds of these lines a week**, across
multiple payers, each with different rules, different codes, different denial
reasons. A billing specialist has to:

1. Read every line and decode every cryptic code.
2. Decide the action: write off / bill patient / bill a secondary insurer / appeal.
3. Make sure the totals match the deposit **to the cent**.
4. Actually *work* the denials (appeals have deadlines — miss them and the money is
   gone forever).

**What goes wrong at scale (this is the cost we attack):**
- **Denials don't get worked** → unrecovered money just sits there. This is the
  #1 driver of the "AR backlog" (Accounts Receivable = money owed to the practice
  that hasn't been collected). Practices routinely have tens of thousands stuck here.
- **Wrong actions** → billing a patient for a `CO` write-off (illegal), or writing
  off something that should've been appealed (lost revenue).
- **Slow** → it's tedious manual work, so it piles up; cash flow suffers.
- **The money doesn't tie out** → a deposit doesn't match the statement and nobody
  notices the discrepancy.

---

## 5. What Remit does — same scenario, automated

Feed Jane's remittance into Remit:

- **Cleaning & Exam (`CO-45`, `PR-2`)** → these are *routine, unambiguous* codes.
  Remit handles them **instantly with deterministic rules**: write off the $50 and
  $20, bill Jane her $30, record insurance's $120. No AI, no risk. **~90% of all
  lines are like this** — and they get cleared in milliseconds.

- **Crown (`CO-197` denial)** → this is the *judgment* case. Remit looks up the
  rule for code 197 in its knowledge base and the payer's rules, and recommends
  **"appeal — Delta requires prior auth on crowns; appeal if an authorization is on
  file"** — and it **shows the source** for that recommendation. Because $1,200 is at
  stake and there's genuine ambiguity (did they have auth or not?), it **routes this
  to a human** with all the evidence attached, instead of guessing.

- **The money check** → Remit confirms insurance paid ($80 + $40 + $0 = $120)…
  wait, the deposit was $612? In a real multi-claim remittance it reconciles the
  *whole batch* to the *whole deposit* to the cent. If it's off by even a penny, it
  **stops** and flags it rather than booking something wrong.

**Net result:** the office's billing person no longer reads 100 lines. They open
Remit, see that ~90 were auto-handled correctly, and spend their time on the **handful
of real decisions** (like Jane's $1,200 crown) — each pre-analyzed, with a
recommendation and its reasoning. The denials get *worked* instead of forgotten.

---

## 6. The business value, stated plainly

| Problem today | What Remit changes |
|---|---|
| Hours of manual decoding per day | ~90% auto-resolved; humans see only the hard few |
| Denials slip through → lost revenue | Every denial surfaced with a recommended action |
| Risk of illegally billing patients | Hard rule: contractual write-offs can never go to the patient |
| Money silently doesn't tie out | Reconciled to the cent, or held |
| Slow → cash-flow / AR backlog | Faster clearing → cash collected sooner |
| Knowledge lives in one expert's head | Knowledge lives in a corpus anyone can extend |

**The one-sentence business case:** *Remit clears the routine 90% automatically and
hands a biller the 10% that need judgment — with the reasoning shown — so the
practice collects more, faster, without compliance mistakes or money quietly going
missing.*

---

## 7. The single most important idea (say this in the interview)

> "The bookkeeping isn't the hard part — adding up numbers is easy. The hard,
> valuable part is the **interpretation**: turning a code like `CO-197` into the
> right *action* worth $1,200, where the answer depends on the code, the group, the
> payer, and the procedure. That's a knowledge-and-judgment problem — which is
> exactly where grounded AI helps and a pile of rigid rules doesn't."

That's the whole reason the product exists. Everything else — reading files, settling,
reconciling — is plumbing around that one valuable decision.

---

## 8. Quick glossary in business terms
- **Claim** — the bill the office sends the insurer.
- **Remittance / EOB** — the insurer's statement of what it did with that bill.
- **Allowed amount** — the contracted price the insurer recognizes (often < charged).
- **Write-off (CO)** — money the practice agreed to forgo by contract; *can't* bill the patient.
- **Patient responsibility (PR)** — the patient's share (deductible, coinsurance, copay).
- **Denial** — the insurer refused to pay a line; must be appealed, rerouted, or written off.
- **Appeal** — formally contesting a denial to recover the money (has deadlines).
- **Secondary payer (COB)** — a second insurance that may cover what the first didn't.
- **AR (Accounts Receivable)** — money owed to the practice not yet collected; the backlog.
- **Reconciliation** — proving the statement's numbers equal the actual bank deposit.
- **Balance-billing** — illegally charging a patient for a contractual write-off.
