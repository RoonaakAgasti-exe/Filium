FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r requirements.txt
COPY backend/ /app/backend/
COPY ingestion/ /app/ingestion/
ENV HF_HOME=/cache/huggingface
RUN mkdir -p /cache/huggingface
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app /cache
USER appuser
ENV PYTHONPATH=/app/backend:/app/ingestion \
    PYTHONUNBUFFERED=1
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]