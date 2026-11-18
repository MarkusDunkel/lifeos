# syntax=docker/dockerfile:1

# --- 1. Build the React frontend -------------------------------------------
FROM node:24-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Python runtime -------------------------------------------------------
FROM python:3.13-slim AS app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

# Claude Code for agents with `billing: subscription` (`claude -p`, see lifeos.agents.claude_code).
# Pinned: the agents rely on its stream-json output and on flags that may change between
# versions. Update deliberately and run the subscription smoke test afterwards.
ARG CLAUDE_CODE_VERSION=2.1.326
RUN curl -fsSL https://claude.ai/install.sh | bash -s "${CLAUDE_CODE_VERSION}" \
    && install -m 0755 "$(readlink -f /root/.local/bin/claude)" /usr/local/bin/claude \
    && rm -rf /root/.local/share/claude /root/.local/bin/claude /root/.claude /root/.claude.json \
    && claude --version
ENV DISABLE_AUTOUPDATER=1

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev

COPY --from=web /web/dist /app/static

ENV LIFEOS_STATIC_DIR=/app/static \
    LIFEOS_VAULT_ROOT=/vault

# The vault is bind-mounted; run as the host user via `user:` in compose.
# ssh (vault git sync) needs a passwd entry for that uid; 1000 is the compose default.
RUN git config --system --add safe.directory /vault \
    && groupadd --gid 1000 lifeos \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin lifeos
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["lifeos", "serve", "--host", "0.0.0.0", "--port", "8000"]

# --- 3. Tests (built by ops/deployer.py before a release; not the running image) ---
FROM app AS test
RUN uv sync --frozen
CMD ["pytest", "-q", "-p", "no:cacheprovider"]
