FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    UV_NO_SYNC=1

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.6 \
    && useradd --create-home --uid 10001 appuser
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra gold
COPY --chown=appuser:appuser backend/app ./backend/app
USER appuser
ENTRYPOINT ["uv", "run", "--no-sync", "python", "-m", "app.gold"]
CMD ["--help"]
