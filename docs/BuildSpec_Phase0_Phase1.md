# Remit — Build Spec: Phase 0 & Phase 1

Companion to the Remit PRD (v3.0). Covers the two foundational phases: the synthetic data generator and the knowledge base + retrieval. Build these first — everything downstream tests against them.

---

## Canonical model (shared by both phases)

The single in-memory schema every ingestion path converges on, and the shape the golden set is labelled in. Monetary values are `Decimal`, never float.

```python
from decimal import Decimal
from datetime import date
from typing import Literal
from pydantic import BaseModel

class Adjustment(BaseModel):
    group_code: Literal["CO", "PR", "OA", "PI"]   # CO=contractual, PR=patient, OA/PI=other/payer
    reason_code: str                               # CARC, e.g. "45"
    amount: Decimal

class ClaimLine(BaseModel):
    cdt_code: str                                  # e.g. "D1110"
    billed: Decimal
    allowed: Decimal
    paid: Decimal
    adjustments: list[Adjustment]
    patient_responsibility: Decimal
    extraction_confidence: float = 1.0             # 1.0 for 835; model score for PDF

class Claim(BaseModel):
    claim_id: str
    patient_ref: str
    date_of_service: date
    clp_status_code: str                           # 1=primary, 2=secondary, 4=denied, 22=reversal
    billed_total: Decimal
    paid_total: Decimal
    lines: list[ClaimLine]

class Remittance(BaseModel):
    payer: str
    trn: str                                       # reassociation trace number
    payment_method: Literal["ACH", "CHK", "NON"]
    eft_amount: Decimal
    paid_date: date
    claims: list[Claim]
```

**Invariants (enforced everywhere, asserted by the generator at construction):**
- Per line: `billed == paid + sum(adj.amount for all adjustments)`
- Per line: `allowed == billed - sum(adj.amount for adj in CO/PI)`
- Per line: `patient_responsibility == sum(adj.amount for adj in PR)`
- Per remittance: `eft_amount == sum(claim.paid_total)`  ← this is reassociation

The settlement step later records, per line: `insurance_paid = paid`, `contractual_writeoff = Σ CO`, `patient_responsibility = Σ PR`, `secondary_responsibility` (COB only). The check is arithmetic, not bookkeeping: `paid + Σ CO + Σ PR (+ secondary) == billed` — every billed dollar is accounted for.

---

# Phase 0 — Synthetic data generator

## Goal
Emit reproducible, conservation-valid triples: an **835 file**, an optional **PDF EOB** of the same data, and a **ground-truth JSON** (the golden-set seed). Because Remit can't touch real PHI, this generator *is* the data supply and the test oracle.

## Module layout
```
gen/
  fee_schedule.py    # per-payer, per-CDT allowed amounts
  catalog.py         # CDT codes + realistic billed ranges; CARC/group code pools
  claim_factory.py   # build claims/lines with billed amounts, patients, DOS
  adjudicator.py     # the core: compute allowed/paid/adjustments, inject edge cases
  x12_writer.py      # serialize Remittance -> valid X12 835
  eob_pdf.py         # render Remittance -> human-readable PDF (reportlab)
  golden.py          # emit ground-truth JSON (expected decisions + settlements)
  cli.py             # python -m gen --claims 20 --denial-rate 0.1 --seed 42 --out fixtures/run01
```

## The adjudicator (the heart of Phase 0)
For each line: look up `allowed` from the payer fee schedule, derive `CO-45 = billed - allowed` (contractual), split the remainder into `paid` and patient responsibility (`PR-1` deductible / `PR-2` coinsurance / `PR-3` copay) per a configurable benefit design. Then optionally mutate the line/claim to inject an edge case. Every path ends with an assertion that the invariants hold — invalid fixtures must be impossible by construction.

## Edge-case injection knobs (each maps to a §14 PRD case)
| Flag | Effect |
|---|---|
| `--denial-rate` | Some lines get `CLP` status 4 + a denial CARC (e.g. `197` precert absent, `29` timely-filing) |
| `--reversal-rate` | Negative `CLP` reversing a prior payment |
| `--split-rate` | One claim paid across two remittances |
| `--cob-rate` | `CLP` status 2 → patient responsibility routes to a secondary payer |
| `--overpayment-rate` | `paid > allowed` (must be flagged, never absorbed) |
| `--plb` | Add a provider-level (`PLB`) adjustment affecting EFT reconciliation |
| `--pdf` | Also render the PDF EOB for the extraction path |
| `--seed` | Seed *everything* — fixtures and golden set must be reproducible |

## Example 835 output (one claim, two lines, balanced)
```
ISA*00*          *00*          *ZZ*PAYER123      *ZZ*PRACTICE456    *260601*1200*^*00501*000000001*0*P*:~
GS*HP*PAYER123*PRACTICE456*20260601*1200*1*X*005010X221A1~
ST*835*0001~
BPR*I*136.00*C*ACH*CCP*01*999988880*DA*123456789*1512345678**01*999988880*DA*987654321*20260601~
TRN*1*EFT20260601001*1512345678~
N1*PR*DELTA DENTAL OF EXAMPLE~
N1*PE*BRIGHT SMILE DENTAL*XX*1234567890~
LX*1~
CLP*CLAIM001*1*200.00*136.00*34.00*12*ICN998877*11~
NM1*QC*1*DOE*JANE****MI*MEMBER001~
SVC*AD:D1110*120.00*80.00**1~
CAS*CO*45*20.00~
CAS*PR*2*20.00~
DTM*472*20260515~
SVC*AD:D0120*80.00*56.00**1~
CAS*CO*45*10.00~
CAS*PR*2*14.00~
DTM*472*20260515~
SE*16*0001~
GE*1*1~
IEA*1*000000001~
```
Check the math: D1110 → 80 paid + 20 CO + 20 PR = 120 billed; D0120 → 56 + 10 + 14 = 80. Claim paid 136 = `CLP04` = `BPR` total = EFT. ✔

## Matching ground-truth JSON (golden-set seed)
```json
{
  "trn": "EFT20260601001",
  "eft_amount": "136.00",
  "claims": [{
    "claim_id": "CLAIM001",
    "lines": [{
      "cdt_code": "D1110", "billed": "120.00", "allowed": "100.00", "paid": "80.00",
      "patient_responsibility": "20.00",
      "adjustments": [
        {"group": "CO", "reason": "45", "amount": "20.00",
         "expected_action": "contractual_writeoff", "billable_to_patient": false},
        {"group": "PR", "reason": "2", "amount": "20.00",
         "expected_action": "bill_patient", "billable_to_patient": true}
      ],
      "expected_settlement": {
        "insurance_paid": "80.00",
        "contractual_writeoff": "20.00",
        "patient_responsibility": "20.00",
        "secondary_responsibility": "0.00"
      }
    }]
  }]
}
```

## Definition of done (Phase 0)
- [ ] Generates N claims as a structurally valid 835 (correct envelope + segment order).
- [ ] Every fixture satisfies all model invariants by construction (generator asserts).
- [ ] Each edge-case knob produces its case and labels it in the ground truth.
- [ ] Ground-truth JSON emitted alongside every run; runs are seed-reproducible.
- [ ] Optional PDF EOB renders the same numbers for the extraction path.

---

# Phase 1 — Knowledge base & retrieval

## Goal
Build the corpus the decision layer reasons over, and a retrieval function that, given an adjustment, returns the canonical code definition **plus** any relevant payer rules — cited, filterable, and with a clear "nothing found" signal that triggers escalation. This is the core of the project: **competence is added by growing this corpus, not by writing rules.**

## Corpus sources (chunk types)
| `chunk_type` | Content | Example |
|---|---|---|
| `carc` | One chunk per Claim Adjustment Reason Code: code, title, gloss, default action, billable-to-patient flag | `45 → charge exceeds fee schedule → contractual_writeoff` |
| `rarc` | One per Remittance Advice Remark Code (supplements a CARC) | `N130 → consult plan benefit docs` |
| `group_code` | Semantics of `CO`/`PR`/`OA`/`PI` | `CO → provider absorbs; never balance-bill` |
| `payer_rule` | Payer-specific adjudication notes, fee-schedule policies, appeal windows | `Delta: timely-filing 90 days` |
| `playbook` | Denial → next-action procedures | `197 (no precert) → appeal with auth on file, else write-off` |

## Chunk shape
```json
{
  "id": "carc-45",
  "chunk_type": "carc",
  "code": "45",
  "group_applicable": ["CO", "PI"],
  "payer": null,
  "content": "CARC 45 — Charge exceeds fee schedule / maximum allowable or contracted fee arrangement. Under group code CO, the provider absorbs the difference as a contractual write-off and must not balance-bill the patient.",
  "metadata": {"default_action": "contractual_writeoff", "billable_to_patient": false}
}
```

## Storage (pgvector)
```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE knowledge_chunk (
  id                text PRIMARY KEY,
  chunk_type        text NOT NULL,           -- carc | rarc | group_code | payer_rule | playbook
  code              text,                    -- exact-match key for carc/rarc
  group_applicable  text[],
  payer             text,
  content           text NOT NULL,
  metadata          jsonb,
  embedding         vector(1536)
);

CREATE INDEX kc_embedding_idx ON knowledge_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX kc_code_idx      ON knowledge_chunk (code);
CREATE INDEX kc_payer_idx     ON knowledge_chunk (payer);
```

## Retrieval — hybrid, not naive semantic
A two-character code like `45` is a terrible thing to embed and vector-search; you'll get noise. So retrieval is **two tiers merged**:

1. **Exact tier (deterministic anchor):** fetch the canonical `carc`/`rarc` chunk by `code`. This *always* grounds the decision in the right definition.
2. **Semantic tier (judgement):** embed a natural-language query, filter by `payer` and `group_applicable`, vector-search the `payer_rule` / `playbook` chunks for context the code alone doesn't carry.

```python
def retrieve(group_code, reason_code, payer, cdt_code, k=5, floor=0.30):
    # Tier 1 — exact: the canonical definition (must exist for a known code)
    anchor = db.fetch_one(
        "SELECT * FROM knowledge_chunk WHERE code = %s AND chunk_type IN ('carc','rarc')",
        [reason_code])

    # Tier 2 — semantic: payer rules + playbook for this situation
    query = (f"Adjustment {group_code}-{reason_code} on dental procedure {cdt_code} "
             f"from payer {payer}. What does it mean and what should the practice do?")
    qvec = embed(query)
    rules = db.fetch_all("""
        SELECT *, 1 - (embedding <=> %s) AS score
        FROM knowledge_chunk
        WHERE chunk_type IN ('payer_rule','playbook')
          AND (payer = %s OR payer IS NULL)
        ORDER BY embedding <=> %s
        LIMIT %s
    """, [qvec, payer, qvec, k])
    rules = [r for r in rules if r["score"] >= floor]

    hits = ([anchor] if anchor else []) + rules
    if not hits:
        return RetrievalResult(escalate=True, reason="no_grounding")  # → exception, never guess
    return RetrievalResult(chunks=hits, citations=[h["id"] for h in hits])
```

The decision chain (Phase 2) consumes `citations` directly — and an uncited decision is rejected, so an empty semantic tier on a *known* code still grounds on the exact anchor, while a truly unknown code (no anchor, no rules) escalates.

## Definition of done (Phase 1)
- [ ] Corpus loaded: full CARC + RARC + the four group codes + ≥1 payer ruleset + denial playbook.
- [ ] `knowledge_chunk` table + HNSW + code/payer indexes in place.
- [ ] `retrieve()` returns the correct canonical chunk for any known code (exact tier = 100% on a code→chunk test set).
- [ ] Semantic tier returns relevant payer rules above the similarity floor; below floor is dropped.
- [ ] Unknown code with no rules returns the escalation signal rather than an empty/false decision.
- [ ] A tiny retrieval eval (≈30 cases) reports exact-tier hit rate and semantic-tier precision@k.

---

## Why this order
Phases 0 and 1 together give you a labelled data supply and a grounded knowledge base — which means the moment you start Phase 2 (the decision layer), you can evaluate it against the golden set on the first run instead of eyeballing outputs. That feedback loop is the whole point: it's what lets you tune prompts and grow the corpus with evidence, and it's the story that separates this from a weekend RAG demo.
