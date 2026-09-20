# Dockerfile for WhatsApp B2B Agent Backend Service
FROM python:3.12-slim

# Optimize Python execution in container
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install curl for container health check
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY . .

# Ensure data directory exists for SQLite database and backups
RUN mkdir -p /app/data /app/data/backups

# Expose FastAPI HTTP port
EXPOSE 8000

# Container health check endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production Uvicorn command (1 worker for SQLite WAL mode concurrency resilience)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
