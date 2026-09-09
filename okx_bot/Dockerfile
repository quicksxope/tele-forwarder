# Build from repo root:
#   docker build -f okx_bot/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install daemon deps only (no [tui] extra). --frozen ensures uv.lock is respected.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY okx_bot ./okx_bot
# Optional helper for one-shot Telegram user login on the host/VM.
COPY login_user.py ./login_user.py

ENV TELE_FORWARDER_DATA_DIR=/data
ENV PYTHONPATH=/app
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "okx_bot"]
