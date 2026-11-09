import json

import portalocker
import pytest
from portalocker.exceptions import AlreadyLocked

from app.gold.cli import main
from app.gold.common import IntegrityError
from app.gold.failures import (
    EXIT_DATA,
    EXIT_IO,
    EXIT_SOFTWARE,
    EXIT_TEMPORARY,
    PERMANENT,
    exit_code,
)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (IntegrityError("contract"), EXIT_DATA),
        (AlreadyLocked("busy"), EXIT_TEMPORARY),
        (PermissionError("mount"), EXIT_IO),
        (TimeoutError("share"), EXIT_IO),
        (RuntimeError("bug"), EXIT_SOFTWARE),
    ],
)
def test_failures_map_to_sysexits(error, code):
    assert exit_code(error) == code


def test_only_data_and_usage_errors_are_permanent():
    assert EXIT_DATA in PERMANENT
    assert {EXIT_TEMPORARY, EXIT_IO, EXIT_SOFTWARE}.isdisjoint(PERMANENT)


def test_busy_store_is_reported_as_retryable(tmp_path, capsys):
    store = tmp_path / "gold"
    store.mkdir()
    with portalocker.Lock(store / "pipeline.lock", timeout=0):
        code = main(["backup", "--source", str(store), "--destination", str(tmp_path / "copy")])
    failure = json.loads(capsys.readouterr().err)
    assert code == EXIT_TEMPORARY
    assert failure["retryable"] is True and failure["error_type"] == "AlreadyLocked"
