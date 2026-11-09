# Airflow image that also carries the Gold CLI. Build from the repository root:
#   docker build -f orchestration/airflow/Dockerfile -t calidad-serie-airflow .
#
# Airflow and the application never share site-packages: Airflow keeps its
# constraint-pinned install, the Gold CLI runs from /opt/calidad/.venv built
# from uv.lock with the same Python minor version as gold/Dockerfile.
FROM python:3.11-slim AS uv
RUN python -m pip install --no-cache-dir uv==0.11.6

FROM apache/airflow:3.4.0-python3.11

USER root
COPY --from=uv /usr/local/bin/uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/calidad/.venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

WORKDIR /opt/calidad
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra gold --python /usr/local/bin/python3.11 \
    && rm /usr/local/bin/uv
COPY backend/app ./backend/app
COPY --chmod=755 orchestration/airflow/calidad-gold /usr/local/bin/calidad-gold
COPY orchestration/airflow/dags /opt/airflow/dags
RUN chmod -R a+rX /opt/calidad /opt/airflow/dags \
    && calidad-gold contracts > /dev/null

WORKDIR /opt/airflow
USER airflow
