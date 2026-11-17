import json
from contextlib import closing

from test_review_execution import command
from test_review_execution import conflict as conflict

from ingesta_delta_lake.review_adapters import KEEP
from ingesta_delta_lake.reviews import atomic, pending_families
from ingesta_delta_lake.service import run


def test_review_only_turn_does_not_scan_or_consume_new_inputs(conflict, tmp_path, monkeypatch):
    config, control_type, path, first, second = conflict
    root = tmp_path / "decision-channel"
    monkeypatch.setenv("INGESTA_REVIEW_ROOT", str(root))
    with closing(control_type(config)) as control:
        decision = command(config, control, KEEP)
    atomic(root / "commands" / config.family / (decision["decision_id"] + ".json"), decision)
    assert pending_families([config.family]) == {config.family}
    from ingesta_delta_lake import service

    def forbidden_capture(*args, **kwargs):
        raise AssertionError("A review-only turn must not scan the incoming data")

    monkeypatch.setattr(service, "_process", forbidden_capture)
    result = run(config, reviews_only=True)
    assert result["status"] == "REVIEWS_CHECKED"
    receipt = json.loads(
        (root / "results" / config.family / (decision["decision_id"] + ".json")).read_text()
    )
    assert receipt["status"] == "VERIFIED"
    assert not pending_families([config.family])
