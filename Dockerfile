# syntax=docker/dockerfile:1
# Chic A Boo unified API — FastAPI/uvicorn container.

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# build-essential is needed to compile any deps without musl/manylinux wheels
# (argon2-cffi, cryptography, asyncpg all ship wheels, but keep it for safety).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY app ./app
# JWT PEMs are NOT in the image (keys/ is gitignored / dockerignored).
# Provide JWT_PRIVATE_KEY + JWT_PUBLIC_KEY (full PEM text) as env vars at runtime.

# Run as a non-root user.
RUN useradd --create-home --uid 1001 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
