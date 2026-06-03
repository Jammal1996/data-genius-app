# Dockerfile — Render-compatible
# Key difference from the generic Dockerfile:
# Render injects a $PORT environment variable — we must NOT hardcode port 8000.
# The CMD below uses $PORT so it works both locally (default 8000) and on Render.

# ─── Stage 1: Build ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

COPY app.py .
COPY requirements.txt .
COPY static/ static/

RUN mkdir -p static/uploads static/charts \
    && chmod -R 755 static/

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Render assigns a random PORT — expose it symbolically
EXPOSE 8000

# Render overrides this CMD via render.yaml startCommand (using $PORT).
# This default works fine for local `docker run` without -e PORT.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 4 --timeout 120 app:app"]
