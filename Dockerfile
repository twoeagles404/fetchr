FROM python:3.11-slim

LABEL maintainer="Fetchr"
LABEL description="Fetchr download agent — web UI + REST API"

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
      aria2 \
      ffmpeg \
      curl \
  && rm -rf /var/lib/apt/lists/*

# ── App setup ─────────────────────────────────────────────────────────────────
WORKDIR /app

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ .

# ── Persistent directories ────────────────────────────────────────────────────
# /downloads → mount your host download folder here
# /data      → SQLite DB + settings (mount for persistence across restarts)
RUN mkdir -p /downloads /data

# ── Expose port ───────────────────────────────────────────────────────────────
EXPOSE 9876

# ── Health check ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:9876/health || exit 1

# ── Environment ───────────────────────────────────────────────────────────────
ENV FETCHR_SAVE_PATH=/downloads \
    FETCHR_DATA_DIR=/data

# ── Entrypoint ───────────────────────────────────────────────────────────────
CMD ["python", "main.py"]
