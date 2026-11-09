FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_NO_SYNC=1

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.6
RUN useradd --create-home --uid 10001 appuser

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra gold

COPY --chown=appuser:appuser backend ./backend
COPY --chown=appuser:appuser config ./config
RUN chmod 0555 backend/entrypoint.sh

USER appuser

EXPOSE 8001

# Exec form: no intermediate shell, signals reach uvicorn. See backend/entrypoint.sh.
ENTRYPOINT ["./backend/entrypoint.sh"]
