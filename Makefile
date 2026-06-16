# Remit — developer entrypoints. Targets fill in as phases land.
.DEFAULT_GOAL := help
SEED   ?= 42
CLAIMS ?= 20
DENIAL ?= 0.1
OUT    ?= fixtures/run-$(SEED)

.PHONY: help
help:  ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install:  ## Install Python deps
	pip install -r requirements.txt

.PHONY: db
db:  ## Bring up Postgres (+pgvector)
	docker compose up -d db

.PHONY: gen
gen:  ## Generate a reproducible synthetic remittance + golden truth (SEED, CLAIMS, DENIAL)
	python -m gen --seed $(SEED) --claims $(CLAIMS) --denial-rate $(DENIAL) --pdf --out $(OUT)

.PHONY: scan-sample
scan-sample:  ## Synthesize a degraded, image-only scanned EOB → fixtures/scanned_eob.pdf (exercises the vision path)
	python -m gen.eob_scan --out fixtures/scanned_eob.pdf

.PHONY: corpus eval pipeline demo
corpus:    ## (Phase 1) build + embed the knowledge base; print a summary
	python -m app.kb.build
eval:      ## (Phase 2) score the decision layer against a fixture's golden set (FIXTURE=...)
	python -m eval.run_eval --fixture $(OUT)
pipeline:  ## (Phase 9) run + score the whole pipeline on a fixture; writes eval/last_pipeline_report.{json,html}
	python -m eval.pipeline_eval --fixture $(OUT)
demo:      ## (Phase 10) scripted end-to-end narration
	python -m scripts.demo

.PHONY: api web
api:       ## Run the FastAPI service (http://localhost:8000/docs)
	uvicorn app.api:app --reload --port 8000
web:       ## Run the React/Vite dashboard (http://localhost:5173)
	cd web && npm install && npm run dev

.PHONY: test lint
test:  ## Run tests
	pytest -q
lint:  ## Lint
	ruff check .
