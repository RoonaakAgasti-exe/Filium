# Build context is the REPO ROOT, not ./ml — see docker-compose.yml.
# The daily job ingests prices and news before predicting, and those
# fetchers live in ingestion/. Its last two steps (portfolio snapshots and
# alert evaluation) reuse backend/wallet.py and backend/alerts_engine.py,
# so backend/ is needed here too — those modules only pull psycopg2 and
# python-dotenv, both already in ml/requirements.txt, so no FastAPI stack
# comes along with them.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY ml/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ml/ /app/ml/
COPY ingestion/ /app/ingestion/
COPY backend/ /app/backend/

# FinBERT is downloaded on first run; without a writable cache directory
# it re-downloads on every container restart.
ENV HF_HOME=/cache/huggingface
RUN mkdir -p /cache/huggingface

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app /cache
USER appuser

ENV PYTHONPATH=/app/ml:/app/ingestion:/app/backend \
    PYTHONUNBUFFERED=1

WORKDIR /app/ml

CMD ["python", "scheduler.py"]
