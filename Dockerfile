FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install daemon deps only (no [tui] extra). --frozen ensures uv.lock is respected.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy daemon source. tui/ is deliberately excluded via .dockerignore.
COPY forwarder.py paths.py ./

ENV TELE_FORWARDER_DATA_DIR=/data
# Make the uv-managed venv available without `uv run`.
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "forwarder.py"]
