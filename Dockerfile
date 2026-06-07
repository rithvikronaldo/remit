# Single-container Remit: FastAPI API that also serves the built dashboard.
#   fly deploy   (scale-to-zero → ~$0 idle; see fly.toml)

# --- stage 1: build the React dashboard ---
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
ENV VITE_API_BASE=
RUN npm run build

# --- stage 2: API that also serves the built dashboard ---
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ app/
COPY gen/ gen/
COPY eval/ eval/
COPY corpus/ corpus/
COPY fixtures/sample/ fixtures/sample/
COPY --from=web /web/dist web/dist

# Seed the startup batch (fixtures/run-42 is gitignored) + cache the embedding model into the image
RUN python -m gen --seed 42 --claims 20 --denial-rate 0.1 --out fixtures/run-42 \
 && python -m app.kb.build

ENV REMIT_SERVE_SPA=1
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
