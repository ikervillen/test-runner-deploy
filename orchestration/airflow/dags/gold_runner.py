"""Runs a Gold CLI command inside a task: permanent failures fail at once, the rest retry."""

from __future__ import annotations

import json
import subprocess
from typing import Any

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
    _check(argv, code)


def gold_report(argv: list[str]) -> dict[str, Any]:
    """Runs a read-only CLI command and returns its JSON; diagnostics still reach the log."""
    completed = subprocess.run(argv, stdout=subprocess.PIPE, text=True)
    print(completed.stdout, end="", flush=True)
    _check(argv, completed.returncode)
    return json.loads(completed.stdout)


def _check(argv: list[str], code: int) -> None:
    if code in PERMANENT_EXIT_CODES:
        raise AirflowFailException(f"{argv[1]} rejected its input (exit {code}); not retrying")
    if code:
        raise RuntimeError(f"{argv[1]} failed with exit {code}; Airflow will retry")
