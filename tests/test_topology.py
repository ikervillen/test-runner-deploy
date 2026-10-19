import json
import sqlite3
from dataclasses import replace

import pytest

from ingesta_delta_lake import topology as t
from ingesta_delta_lake.common import IngestaError
from ingesta_delta_lake.service import run
from ingesta_delta_lake.topology_storage import TopologyControl, TopologyStore, snapshot


@pytest.fixture
def config(config):
    folder = config.input_root / "AV31"
    folder.mkdir()
    return replace(
        config,
        sources=(("AV31", folder),),
        family=t.MACHINES,
        timestamp_policy="local_naive_test_v1",
        topology_enabled=True,
        line_by_origin=(("AV31", "AV31"),),
        line_code_by_origin=(("AV31", 31),),
    )


def machine(identity=7, name="AV31A", start="01/01/1900", end="", code=31):
    return f"{identity};{name};1;8;8;2;0;{start};{end};{code};\n"


def branch(identity=13, number=1, machine_id=8, start="01/01/1900", end=""):
    return f"{identity};{number};LV;LV;{machine_id};{start};{end};\n"


def put(config, family, text, name=None):
    p = config.sources[0][1] / (name or family + ".csv")
    p.write_text(text, encoding="utf-8")
    return p


def seed(config):
    put(config, t.MACHINES, machine() + machine(8, "AV31B"))
    assert run(config)["published_records"] == 2


def data(config, family=None):
    return snapshot(config, family or config.family)[0]


def files(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def sql(config, query):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(query).fetchall()


def test_minimal_fields_dates_and_replay_without_commits(config):
    seed(config)
    rows = data(config)
    assert len(rows) == 2 and rows[(8, "1900-01-01T00:00:00")]["nombre_maquina"] == "AV31B"
    assert list(next(iter(rows.values()))) == list(t.FIELDS[t.MACHINES])
    before = files(config.table_path)
    assert run(config)["inserted_this_attempt"] == 0
    assert files(config.table_path) == before
    assert sql(config, "SELECT COUNT(*) FROM topology_intents WHERE state='CONFIRMED'") == [(2,)]


def test_branches_resolve_codes_instead_of_internal_ids(config):
    seed(config)
    cfg = replace(config, family=t.BRANCHES)
    put(cfg, t.BRANCHES, branch() + branch(93, 2) + branch(14, 3, 7) + branch(15, 4, 7))
    result = run(cfg)
    assert result["complete"] and result["published_records"] == 4
    rows = data(cfg)
    assert rows[("AV31", 93, "1900-01-01T00:00:00")]["codigo_ramal"] == 2
    assert {r["codigo_ramal"]: r["id_maquina_fenix"] for r in rows.values()} == {
        1: 8,
        2: 8,
        3: 7,
        4: 7,
    }


def test_dependency_recovers_from_bronze_after_live_file_disappears(config):
    cfg = replace(config, family=t.BRANCHES)
    p = put(cfg, t.BRANCHES, branch())
    assert run(cfg)["status"] == "WAITING_DEPENDENCIES"
    p.unlink()
    seed(config)
    result = run(cfg)
    assert result["complete"] and result["published_records"] == 1
    assert sql(cfg, "SELECT COUNT(*) FROM topology_review_events") == [(1,)]


@pytest.mark.parametrize("reverse", [False, True])
def test_close_and_new_configuration_are_atomic_without_reopening(config, reverse):
    seed(config)
    cfg = replace(config, family=t.BRANCHES)
    put(cfg, t.BRANCHES, branch())
    run(cfg)
    rows = [
        branch(),
        branch(end="14/10/2026 7:23:14"),
        branch(machine_id=7, start="14/10/2026 7:23:14"),
    ]
    put(cfg, t.BRANCHES, "".join(reversed(rows) if reverse else rows))
    result = run(cfg)
    assert result["updated_this_attempt"] == result["inserted_this_attempt"] == 1
    assert result["complete"]
    before = files(cfg.table_path)
    put(cfg, t.BRANCHES, branch())
    assert run(cfg)["updated_this_attempt"] == 0 and files(cfg.table_path) == before


def test_overlap_is_not_resolved_by_first_last_or_omission(config):
    seed(config)
    cfg = replace(config, family=t.BRANCHES)
    p = put(cfg, t.BRANCHES, branch() + branch(93, 1, 7))
    assert run(cfg)["status"] == "BLOCKED" and not cfg.table_path.exists()
    p.write_text(branch(), encoding="utf-8")
    assert not run(cfg)["complete"] and not cfg.table_path.exists()


@pytest.mark.parametrize(
    "family,text",
    [
        (t.MACHINES, machine(name="")),
        (t.MACHINES, machine(end="01/01/1800")),
        (t.MACHINES, machine(identity=2**63)),
        (t.MACHINES, machine(code=32)),
        (t.BRANCHES, branch(end="01/01/1800")),
        (t.BRANCHES, "bad;row;\n"),
    ],
)
def test_invalid_or_incompatible_source_keeps_evidence(config, family, text):
    seed(config)
    cfg = replace(config, family=family)
    put(cfg, family, text)
    result = run(cfg)
    assert not result["complete"] and result["open_reviews"]
    payload = json.loads(sql(cfg, "SELECT payload_json FROM issues LIMIT 1")[0][0])
    assert payload.get("detail") or payload.get("candidates")
    assert sql(cfg, "SELECT COUNT(*) FROM issue_evidence")[0][0] > 0


def test_name_change_for_same_identity_is_reviewed_without_overwriting(config):
    seed(config)
    before = data(config)
    put(config, t.MACHINES, machine(name="OTHER"))
    assert not run(config)["complete"] and data(config) == before


def test_source_ignored_fields_and_versioned_names_do_not_write(config):
    seed(config)
    before = files(config.table_path)
    put(
        config,
        t.MACHINES,
        machine().replace(";1;8;8;2;0;", ";0;99;99;3;1;"),
        name="19102026_93000_SIP_MAQUINA_MOLDEO.csv",
    )
    assert run(config)["inserted_this_attempt"] == 0
    assert files(config.table_path) == before


@pytest.mark.parametrize("phase", ["before", "after", "ack"])
def test_recovery_around_commit_and_ack_preserves_unique_intents(config, monkeypatch, phase):
    put(config, t.MACHINES, machine())
    original = TopologyStore.apply_changes

    def fail(self, *args):
        if phase == "after":
            original(self, *args)
        raise OSError("test")

    with monkeypatch.context() as patch:
        if phase == "ack":
            patch.setattr(
                TopologyControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack"))
            )
        else:
            patch.setattr(TopologyStore, "apply_changes", fail)
        with pytest.raises(IngestaError):
            run(config)
    result = run(config)
    assert result["delta_version"] == 0 and result["published_records"] == 1
    assert result["recovered_operations"]
    assert sql(config, "SELECT COUNT(*) FROM topology_intents") == [(1,)]
