import sys

import pytest
from airflow.sdk.exceptions import AirflowFailException
from gold_runner import gold_report, run_gold


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


def test_report_parses_stdout_and_leaves_stderr_to_the_log(capfd):
    assert gold_report(cli(0, '{"status": "BLOCKED"}')) == {"status": "BLOCKED"}
    captured = capfd.readouterr()
    assert captured.out.strip() == '{"status": "BLOCKED"}'
    assert captured.err.strip() == "err"


def test_report_applies_the_same_exit_code_policy():
    with pytest.raises(AirflowFailException):
        gold_report(cli(65, "{}"))
    with pytest.raises(RuntimeError):
        gold_report(cli(75, "{}"))
