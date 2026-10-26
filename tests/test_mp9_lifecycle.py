"""Lifecycle transitions, stale snapshots and recovery of the old false conflicts."""

import json

import pytest
from test_mp9 import config as config
from test_mp9 import files, line, query, rows
from test_mp9 import models as models
from test_mp9 import source as source

from ingesta_delta_lake import mp9
from ingesta_delta_lake.service import run


@pytest.mark.parametrize("close", [False, True])
@pytest.mark.parametrize("confirmed", [False, True])
def test_lifecycle_update_replaces_same_row_and_replay_is_noop(config, source, close, confirmed):
    source.write_text(line(ok=0), encoding="utf-8")
    run(config)
    source.write_text(
        line(ok=int(confirmed), off="14/10/2026 6:00:00" if close else ""), encoding="utf-8"
    )
    result = run(config)
    expected_updates = int(close or confirmed)
    assert result["updated_this_attempt"] == expected_updates
    assert result["published_records"] == 1 and result["open_reviews"] == 0
    assert result["open_dependencies"] == 0
    accepted = rows(config)[("AV21", 1)]
    assert accepted["ZF_OK"] == int(confirmed)
    assert accepted["FECHA_OFF"] == ("2026-10-14T06:00:00" if close else None)
    before = files(config)
    assert run(config)["updated_this_attempt"] == 0
    assert files(config) == before


def test_stale_open_and_zero_snapshots_never_reopen_or_downgrade(config, source):
    source.write_text(line(off="14/10/2026 6:00:00", ok=1), encoding="utf-8")
    run(config)
    source.write_text(line(ok=0), encoding="utf-8")
    result = run(config)
    assert result["complete"] and result["updated_this_attempt"] == 0
    assert rows(config)[("AV21", 1)]["ZF_OK"] == 1
    assert rows(config)[("AV21", 1)]["FECHA_OFF"] == "2026-10-14T06:00:00"


def test_upgrade_retries_legacy_ok_conflict_without_original_csv(config, source, monkeypatch):
    source.write_text(line(ok=0) + line(ok=1) + line(2, zone=2, rel=1), encoding="utf-8")
    normal = mp9.consolidate

    def legacy(rows):
        candidate, changed = normal(rows)
        if len({r["ZF_OK"] for r in rows}) > 1:
            changed.append("ZF_OK")
        return candidate, changed

    with monkeypatch.context() as patch:
        patch.setattr(mp9, "consolidate", legacy)
        patch.setattr(mp9, "LIFECYCLE_RULE", "legacy_policy")
        assert not run(config)["complete"]
    assert query(config, "SELECT COUNT(*) FROM issues WHERE status != 'RESOLVED'") == [(2,)]
    source.unlink()
    result = run(config)
    assert result["complete"] and result["inserted_this_attempt"] == 2
    assert rows(config)[("AV21", 1)]["ZF_OK"] == 1
    assert query(config, "SELECT COUNT(*) FROM issues WHERE status != 'RESOLVED'") == [(0,)]
    for (text,) in query(config, "SELECT payload_json FROM issues"):
        payload = json.loads(text)
        assert payload["evidence"]
        assert payload["resolution"]["rule"] == mp9.LIFECYCLE_RULE
    version = result["delta_version"]
    assert run(config)["delta_version"] == version


def test_existing_cold_row_closes_even_if_parent_is_under_review(config, source):
    run(config)
    # A real parent conflict must stay pending, without preventing the accepted ZF closure.
    source.write_text(
        line(machine=8) + line(2, zone=2, rel=1, on="01/10/2026 7:00:00", off="14/10/2026 7:23:14"),
        encoding="utf-8",
    )
    result = run(config)
    assert result["updated_this_attempt"] == 1
    assert rows(config)[("AV21", 2)]["FECHA_OFF"] == "2026-10-14T07:23:14"
    assert result["open_reviews"] == 1 and result["open_dependencies"] == 0


def test_old_zc_pending_closure_is_recovered_and_audited_without_csv(config, source, monkeypatch):
    run(config)
    source.write_text(
        line(machine=8) + line(2, zone=2, rel=1, on="01/10/2026 7:00:00", off="26/10/2026 8:29:07"),
        encoding="utf-8",
    )
    normal = mp9.decide

    def legacy(*args):
        inserts, updates, issues, matches = normal(*args)
        for row in updates:
            issues.append(
                {
                    "reason": "ZC_PENDING",
                    "ID": row["ID"],
                    "origen_fenix": row["origen_fenix"],
                    "accepted": row["before"],
                    "candidate": {f: row[f] for f in mp9.SCHEMA.names},
                    "evidence": row["evidence"],
                }
            )
        return inserts, [], issues, matches

    with monkeypatch.context() as patch:
        patch.setattr(mp9, "decide", legacy)
        patch.setattr(mp9, "LIFECYCLE_RULE", "legacy_policy")
        assert run(config)["open_dependencies"] == 1
    source.unlink()
    result = run(config)
    assert result["updated_this_attempt"] == 1 and result["open_dependencies"] == 0
    assert result["open_reviews"] == 1  # Keep the unrelated true parent conflict.
    assert rows(config)[("AV21", 2)]["FECHA_OFF"] == "2026-10-26T08:29:07"
    status, text = query(
        config, "SELECT status,payload_json FROM issues WHERE reason='ZC_PENDING'"
    )[0]
    assert status == "RESOLVED" and json.loads(text)["resolution"]["rule"] == mp9.LIFECYCLE_RULE


@pytest.mark.parametrize("phase", ["before", "after", "ack"])
def test_zf_confirmation_recovers_once(config, source, monkeypatch, phase):
    from ingesta_delta_lake.common import IngestaError
    from ingesta_delta_lake.mp9_control import Mp9Control
    from ingesta_delta_lake.mp9_storage import Mp9Store

    source.write_text(line(ok=0), encoding="utf-8")
    run(config)
    source.write_text(line(ok=1), encoding="utf-8")
    apply = Mp9Store.apply_changes

    def fail(self, *args):
        if phase == "after":
            apply(self, *args)
        raise OSError("Interrupted confirmation")

    with monkeypatch.context() as patch:
        if phase == "ack":
            patch.setattr(Mp9Control, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack")))
        else:
            patch.setattr(Mp9Store, "apply_changes", fail)
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["delta_version"] == 1 and result["complete"]
    assert rows(config)[("AV21", 1)]["ZF_OK"] == 1
    assert result["recovered_operations"][0]["wrote_this_attempt"] == (phase == "before")
