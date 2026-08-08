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

# --extra-index-url is for torch, pulled in by sentence-transformers (local
# embeddings + the reranker). PyPI's default torch wheel bundles CUDA
# libraries — roughly 800MB of them — for a GPU this container will never
# have. The CPU index serves the same version built without them.
#
# It's an EXTRA index, not a replacement: every other dependency still
# resolves from PyPI normally.
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt

COPY backend/ /app/backend/
COPY ingestion/ /app/ingestion/

# The local embedding model is downloaded on first use, not baked into the
# image. docker-compose mounts a named volume here so it survives restarts.
#
# The directory must exist in the IMAGE, owned by appuser, before that
# volume is ever mounted: Docker seeds an empty named volume from whatever
# is at the image path underneath it, ownership included — but if the path
# doesn't exist it creates the mountpoint root-owned instead. The container
# runs as appuser, so the model download would then fail on a read-only
# cache directory, on the first query, long after startup looked fine.
ENV HF_HOME=/cache/huggingface
RUN mkdir -p /cache/huggingface

# Run as a non-root user — the default root user in a container is an
# unnecessary blast radius for a process that only needs to read its own code.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app /cache
USER appuser

ENV PYTHONPATH=/app/backend:/app/ingestion \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
