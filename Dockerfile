FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install production dependencies only
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Run as non-root user (claude-agent-sdk disallows root)
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

CMD ["uv", "run", "python", "bot.py"]
