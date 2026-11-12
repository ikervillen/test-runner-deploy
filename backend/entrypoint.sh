#!/bin/sh
# Container entrypoint for the FastAPI backend.
#
# `exec` replaces the shell with uvicorn so PID 1 receives SIGTERM directly
# and Compose's stop_grace_period applies to the server, not to a wrapper.
#
# APP_WEB_CONCURRENCY defaults to one worker. The score cache and the query
# semaphore (APP_SCORE_QUERY_MAX_CONCURRENCY) are per worker, so each extra
# worker multiplies memory and the number of concurrent heavy queries.
#
# Prometheus metrics stay correct with several workers: each one writes to
# PROMETHEUS_MULTIPROC_DIR and /api/metrics aggregates them. The directory is
# recreated empty on every start; stale files from a previous run would be
# added to the new counters.
set -eu

workers="${APP_WEB_CONCURRENCY:-1}"
case "$workers" in
  '' | *[!0-9]* | 0*)
    echo "APP_WEB_CONCURRENCY must be a positive integer, got '$workers'" >&2
    exit 64
    ;;
esac

if [ "$workers" -gt 1 ]; then
  export PROMETHEUS_MULTIPROC_DIR="${PROMETHEUS_MULTIPROC_DIR:-/tmp/prometheus-multiproc}"
fi
if [ -n "${PROMETHEUS_MULTIPROC_DIR:-}" ]; then
  rm -rf "$PROMETHEUS_MULTIPROC_DIR"
  mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
fi

exec uv run --no-dev uvicorn main:app \
  --app-dir backend \
  --host "${APP_HOST:-0.0.0.0}" \
  --port "${APP_PORT:-8001}" \
  --workers "$workers" \
  "$@"
