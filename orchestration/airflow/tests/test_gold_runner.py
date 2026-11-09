import sys

import pytest
from airflow.sdk.exceptions import AirflowFailException
from gold_runner import run_gold


def cli(code: int, message: str = "") -> list[str]:
    script = f"import sys; print({message!r}); print('err', file=sys.stderr); sys.exit({code})"
    return [sys.executable, "-c", script]


def test_success_streams_stdout_and_stderr_into_the_log(capsys):
    run_gold(cli(0, '{"status": "PUBLISHED"}'))
    assert set(capsys.readouterr().out.splitlines()) == {'{"status": "PUBLISHED"}', "err"}


@pytest.mark.parametrize("code", [2, 65])
def test_usage_and_data_errors_fail_without_retry(code):
    with pytest.raises(AirflowFailException, match=f"exit {code}"):
        run_gold(cli(code))


@pytest.mark.parametrize("code", [70, 74, 75, 1])
def test_other_failures_are_left_to_airflow_retries(code):
    with pytest.raises(RuntimeError, match=f"exit {code}"):
        run_gold(cli(code))
