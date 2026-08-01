# Build context is the REPO ROOT, not ./backend — see docker-compose.yml.
# The backend imports ingestion helpers, and the eval harness imports the
# backend's rag module, so the image needs both directories. Building from
# ./backend alone is what made /query fail at runtime with a missing
# module even though requirements were satisfied.
FROM python:3.12-slim

WORKDIR /app

# psycopg2 needs libpq at build time; keep the image slim by cleaning
# apt cache in the same layer rather than a separate RUN
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/backend/
COPY ingestion/ /app/ingestion/

# Run as a non-root user — the default root user in a container is an
# unnecessary blast radius for a process that only needs to read its own code.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONPATH=/app/backend:/app/ingestion \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
