FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared ./shared
COPY app ./app
# JWT PEMs are not in git (keys/ is gitignored).
# On Render set JWT_PRIVATE_KEY + JWT_PUBLIC_KEY (full PEM text).
# Locally you can still mount ./keys or set JWT_*_KEY_PATH.

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
