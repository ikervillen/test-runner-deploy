FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /opt/app
RUN python -m pip install --no-cache-dir uv==0.11.6
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python
COPY src ./src
RUN uv sync --locked --no-dev --no-editable --python /usr/local/bin/python

FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ARG REVISION
ARG SOURCE
ARG BRANCH
LABEL org.opencontainers.image.revision=$REVISION org.opencontainers.image.source=$SOURCE
LABEL es.fenix.ingesta.branch=$BRANCH
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/app
COPY --from=build /opt/venv /opt/venv
RUN groupadd --gid 10001 ingesta && useradd --uid 10001 --gid 10001 --no-create-home ingesta
RUN python -m ingesta_delta_lake.runtime describe > /opt/app/contracts.json
USER 10001:10001
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=20s --timeout=10s --start-period=60s --retries=3 CMD ["python", "-m", "ingesta_delta_lake.runtime", "health"]
ENTRYPOINT ["python", "-m", "ingesta_delta_lake.runtime"]
CMD ["worker"]
