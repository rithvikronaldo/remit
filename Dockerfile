# Remit API image. The dashboard (web/) builds separately.
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ app/
COPY gen/ gen/
COPY eval/ eval/
COPY corpus/ corpus/
COPY fixtures/ fixtures/

EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
