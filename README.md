---
title: Remit
emoji: 🦷
colorFrom: indigo
colorTo: yellow
sdk: docker
app_port: 8000
pinned: false
---

# Remit

**AI remittance adjudication engine for dental revenue cycle.**

Remit ingests insurance remittances (electronic `835`/ERA files or scanned EOB PDFs), interprets what the payer did to every claim line — grounded in a retrieval-augmented knowledge base of the underlying coding and payer rules — decides the correct financial action for each adjustment and denial, and records how each line settled (insurance paid, write-off, patient responsibility) in a form that reconciles to the actual money received. Anything it can't settle with confidence is routed to a human with the evidence attached.

> The settlement is the easy tail end. The point of the project is the **interpretation**: turning coded adjustments (`CO-45`, `PR-2`) and denials into the right action, with a RAG system that is grounded, cited, confidence-scored, and fails closed.

---

## Why this exists

Processing insurance remittances — recording what each line settled to and reconciling it to the deposit — is the most labour-intensive task in a dental practice's revenue cycle and the main driver of the AR backlog. The hard part is *interpretation* — the adjustment codes are terse and context-dependent, and the right action depends on the code, the group, the payer, and sometimes the procedure. That is a knowledge-bound judgement task, which is exactly where grounded AI helps and brittle rules don't.

Two ideas drive every design decision:

1. **Applied RAG, done credibly near money.** Decisions are retrieved (not recalled from model memory), structured (a validated schema, never free text), cited (you can see *why*), confidence-gated, and safe — the engine escalates rather than guesses.
2. **Deep domain fluency.** RCM is dense and jargon-heavy. That knowledge lives *as the RAG corpus*, so the system grows by adding knowledge, not by adding `if` statements.

---

## What it does (pipeline)

```
upload ──► ingest ──► parse / extract ──► match ──► DECIDE (rules + RAG) ──► settle ──► reconcile
                                                          │                              │
                                                          └──────────► exception queue ◄─┘
```

| Stage | What happens |
|---|---|
| **Ingest** | Detect file type, persist, capture `TRN`/`BPR`, idempotency hash |
| **Parse / extract** | `835` → deterministic X12 parse · PDF → vision-LLM structured extraction (both arithmetic-gated) |
| **Match** | Link each line to its open claim; split payments accumulate; unmatched → exception |
| **Decide** | Rules for the trivial codes, **RAG for denials & ambiguity** — grounded, cited, confidence-scored |
| **Settle** | Record settled amounts per line (paid / write-off / patient / secondary); atomic per remittance |
| **Reconcile** | `Σ paid == BPR total == EFT` via `TRN`; any delta is a hard fail, held not committed |
| **Exceptions** | Every fail-closed path lands here with evidence + recommended action; human accepts or overrides |

---

## Architecture

Single AI-first service; one language, one repo.

```
        upload
          │
          ▼
   ┌──────────────────────────────────────────────────────┐
   │              Remit service (Python + FastAPI)           │
   │  ingest → parse/extract → match → DECIDE → settle →    │
   │  reconcile → exceptions                                 │
   │                                                         │
   │  decide layer:  rules cascade + RAG (LangChain LCEL) ───┼──► pgvector (CARC/RARC + payer corpus)
   └───────────────────────────┬─────────────────────────────┘
                               ▼
                       PostgreSQL  (remittances · claims · lines · settlements · exceptions · eval cases)
```

Why this shape: settlement never *depends* on a model being right — it only consumes a typed, validated, cited decision it can choose to trust or escalate. Settlement is a small internal step (a per-line record + a `SUM()` reconciliation check), not an accounting system — just enough to prove the money adds up.

---

## Tech stack

Python · FastAPI · PostgreSQL + `pgvector` · LangChain (LCEL) · Claude (decisions + vision extraction) + an embedding model · LangSmith (traces) · React + Vite (read-only dashboard) · Docker Compose.

---

## Repository layout

```
remit/
├─ app/
│  ├─ ingest/       # file-type detection, persistence, idempotency
│  ├─ parse/        # 835 X12 parser (Phase 3)
│  ├─ extract/      # PDF EOB vision extraction (Phase 4)
│  ├─ match/        # matching engine (Phase 5)
│  ├─ decide/       # rules + RAG decision layer (Phase 2)  ← core
│  ├─ settle/       # settlement recording (Phase 6)
│  ├─ reconcile/    # reconciliation (Phase 7)
│  ├─ exceptions/   # exception queue + resolve (Phase 8)
│  ├─ kb/           # corpus build + retrieve() (Phase 1)
│  ├─ models.py     # canonical Pydantic + SQLModel tables
│  └─ api.py        # FastAPI routes
├─ gen/             # synthetic data generator (Phase 0)
├─ corpus/          # CARC / RARC / group-code / payer-rule / playbook source docs
├─ eval/            # golden set + run_eval (Phases 2 & 9)
├─ fixtures/        # generated fixtures (gitignored; one sample committed)
├─ web/             # React/Vite dashboard (Phase 10)
├─ docs/            # PRD + build specs (see below)
├─ docker-compose.yml
└─ README.md
```

---

## Quickstart

**Prerequisites:** Docker + Docker Compose, an `ANTHROPIC_API_KEY`.

**Zero-cost by default.** With no `ANTHROPIC_API_KEY`, Remit runs fully offline: local embeddings (`fastembed`) for retrieval and a deterministic metadata-stub for the decision tier. Set the key to use Claude Haiku 4.5 for the RAG decisions (cached per `(adjustment, payer, cdt, corpus_version, prompt_version)`, so spend stays minimal).

```bash
# 1. install (Python 3.11+ recommended; 3.9 works)
pip install -r requirements.txt
cp .env.example .env          # optional: set ANTHROPIC_API_KEY / LANGSMITH

# 2. generate a reproducible synthetic remittance + open claims + ground truth
make gen SEED=42 CLAIMS=20 DENIAL=0.1     # → fixtures/run-42 (835 + PDF + golden.json)

# 3. scripted end-to-end narration (free): in → cited RAG → settled+reconciled → denial queued
make demo

# 4. score the whole pipeline against the golden set (writes eval/last_pipeline_report.html)
make pipeline OUT=fixtures/run-42

# 5. run it as a service + dashboard
make api                      # FastAPI at http://localhost:8000/docs
make web                      # React dashboard at http://localhost:5173
# …or the whole stack in containers:
docker compose up --build     # api :8000 · dashboard :5173 · postgres+pgvector

# tests
make test                     # 80+ tests, all offline
```

`make corpus` prints the knowledge-base summary; `make eval OUT=fixtures/run-42` runs the decision-only eval.

**Environment variables**

| Var | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Decisions + vision extraction |
| `DATABASE_URL` | Postgres connection (pgvector enabled) |
| `EMBEDDING_MODEL` | Embedding model for retrieval |
| `DECISION_CONFIDENCE_THRESHOLD` | Below this, decisions escalate (default `0.70`) |
| `LANGSMITH_API_KEY` | Optional — chain tracing |

---

## Documentation

| Doc | Contents |
|---|---|
| `docs/PRD.md` | Product requirements — problem, AI/RAG spec, domain reference, data model, NFRs, metrics |
| `docs/BuildSpec_Phase0_Phase1.md` | Synthetic data generator + knowledge base & retrieval |
| `docs/BuildSpec_Phase2.md` | The decision layer — rules-first + RAG cascade, schema, guardrails, eval |
| `docs/BuildSpec_Phase3_to_10.md` | Parser, extraction, matching, settlement, reconciliation, exceptions, eval, dashboard |

---

## Build status / roadmap

- [x] **Phase 0** — Foundations: schema + synthetic data generator ✅
- [x] **Phase 1** — Knowledge base + retrieval (the corpus) ✅
- [x] **Phase 2** — Decision layer (rules + RAG) ← *core* ✅
- [x] **Phase 3** — 835 parser ✅
- [x] **Phase 4** — PDF EOB extraction ✅
- [x] **Phase 5** — Matching engine ✅
- [x] **Phase 6** — Settlement recording ✅
- [x] **Phase 7** — Reconciliation ✅
- [x] **Phase 8** — Exception queue + human-in-the-loop ✅
- [x] **Phase 9** — Eval harness + observability ✅
- [x] **Phase 10** — Dashboard + polish ✅

Build order is `0 → 1 → 2` (the engine, provable on its own), then `3/4 → 5` (feed it real lines), then `6 → 7` (balance the books), then `8` (catch the rest), then `9 → 10` (prove and present).

---

## About this build

Remit is a portfolio project: an end-to-end demonstration of **applied RAG done responsibly near money**, in a dense, jargon-heavy domain (dental revenue cycle management).

The thesis is that the valuable, hard part of remittance processing isn't the bookkeeping — it's the **interpretation**: turning terse, context-dependent adjustment codes (`CO-45`, `PR-2`, a `CARC 197` denial) into the right financial action. So the design treats that as a knowledge-bound judgement task and builds for it deliberately:

- **Grounded, not recalled** — every interpretation is retrieved from a knowledge base and **cited**, never produced from model memory.
- **Rules first, model last** — deterministic rules resolve the routine bulk with zero model risk; the LLM only ever sees denials and genuine ambiguity.
- **Fail-closed** — three guardrails (citation containment, a hard "never balance-bill a contractual adjustment" rule, and a confidence gate) escalate to a human rather than mis-settle.
- **Provably correct on money** — settlement records account for every billed dollar and reconcile to the cent against the actual deposit, enforced by a golden-set eval with a regression gate.

It runs on **synthetic data** (ships with its own generator — no PHI) and a **representative** knowledge corpus, and is **$0 to run by default** (local embeddings + a deterministic decision baseline; Claude Haiku 4.5 and vision PDF extraction activate when an API key is set). See [`docs/INTERVIEW.md`](docs/INTERVIEW.md) for the design rationale and talking points, and [`docs/PRD.md`](docs/PRD.md) for the full product spec.

---

## Deploy

The API is a single container; the dashboard is a static Vite build. Cheapest path (scale-to-zero, ~$0 idle):

```bash
fly launch --no-deploy            # uses the committed fly.toml
fly secrets set ANTHROPIC_API_KEY=...   # optional — the demo runs without it
fly deploy
```

`fly.toml` sets `auto_stop_machines` / `min_machines_running = 0` so it sleeps when idle. The demo runs on synthetic fixtures with local embeddings, so no Postgres or API key is required to show it working; a production deployment would point `DATABASE_URL` at managed pgvector (Neon/Supabase) and load the corpus into it.

---

## A note on data & compliance

**All data is synthetic.** Remit handles what would be Protected Health Information (PHI) in production, but ships with a generator instead of real records. The PRD documents the controls a production deployment would require — a Business Associate Agreement, encryption in transit and at rest, least-privilege access, audit logging, and de-identification before any model call.
