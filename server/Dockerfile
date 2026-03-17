FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── Dependencies layer (cached unless pyproject.toml / uv.lock changes) ──────
FROM base AS deps

WORKDIR /app/server

# Install uv via pip (Tsinghua mirror)
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple/ uv

# Sync production dependencies only (uses lockfile for reproducibility)
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --no-dev -i https://pypi.tuna.tsinghua.edu.cn/simple/

# ── Final image ───────────────────────────────────────────────────────────────
FROM base

COPY --from=deps /app/server/.venv /app/server/.venv

# Copy application code (no frontend files)
COPY server/ /app/server/

WORKDIR /app/server

# Activate venv
ENV PATH="/app/server/.venv/bin:$PATH"

# Create directories for logs and media uploads
RUN mkdir -p logs media

EXPOSE 8765

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health')" || exit 1

# Start the server (migrations run automatically on startup)
CMD ["python", "run.py"]
