# syntax=docker/dockerfile:1

ARG VERSION=unknown
ARG REVISION=unknown

# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.12.13-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependency manifest before source — cache hit when only code changes
COPY requirements.txt .

# Cache mount stores wheels on the build host, not in the image layer.
# --no-cache-dir is intentionally omitted: it would disable the mount cache.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m venv /venv && \
    /venv/bin/pip install --no-warn-script-location \
        "setuptools==82.0.1" "wheel==0.47.0" && \
    /venv/bin/pip install --no-warn-script-location -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12.13-slim-bookworm AS runtime

ARG VERSION=unknown
ARG REVISION=unknown
ARG CREATED=unknown
ARG SOURCE=unknown

LABEL org.opencontainers.image.title="rate-limiter-agents" \
      org.opencontainers.image.description="FastAPI + Claude anomaly-detection / rate-limit intelligence service" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}" \
      org.opencontainers.image.source="${SOURCE}" \
      org.opencontainers.image.licenses="MIT"

# libpq5: runtime shared library only (not headers). tini: PID-1 signal forwarding.
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    libpq5 tini \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system appgroup && useradd --system --gid appgroup appuser

WORKDIR /app

# Installed packages from builder — no source code or build tools
COPY --from=builder --chown=appuser:appgroup /venv /venv

# Application source
COPY --chown=appuser:appgroup . .

ENV VIRTUAL_ENV=/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

# Python-native healthcheck — no wget/curl needed in the runtime image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import sys, urllib.request; r=urllib.request.urlopen('http://localhost:8000/health', timeout=3); sys.exit(0 if 200 <= r.status < 300 else 1)"

# tini as PID 1 forwards signals cleanly; CMD stays overridable at run time.
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "rate_limiter_agents.main:app", "--host", "0.0.0.0", "--port", "8000"]
