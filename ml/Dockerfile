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
ENV HF_HOME=/cache/huggingface
RUN mkdir -p /cache/huggingface
RUN mkdir -p /app/ml/checkpoints
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app /cache
USER appuser
ENV PYTHONPATH=/app/ml:/app/ingestion:/app/backend \
    PYTHONUNBUFFERED=1
WORKDIR /app/ml
CMD ["python", "scheduler.py"]