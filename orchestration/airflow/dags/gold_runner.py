"""Runs a Gold CLI command inside a task: permanent failures fail at once, the rest retry."""

from __future__ import annotations

import subprocess

from airflow.sdk.exceptions import AirflowFailException
from gold_tasks import PERMANENT_EXIT_CODES


def run_gold(argv: list[str]) -> None:
    """Streams the CLI output into the task log; kills the CLI if the task is interrupted."""
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
        code = process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    if code in PERMANENT_EXIT_CODES:
        raise AirflowFailException(f"{argv[1]} rejected its input (exit {code}); not retrying")
    if code:
        raise RuntimeError(f"{argv[1]} failed with exit {code}; Airflow will retry")
