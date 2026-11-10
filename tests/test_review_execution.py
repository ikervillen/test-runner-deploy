import json
import uuid
from contextlib import closing
from dataclasses import replace

import pytest
from deltalake import DeltaTable
from test_lista_ar import line as ar_line
from test_lista_defectos import line as mia_line
from test_lista_rech import line as rech_line
from test_mp9 import line as mp9_line
from test_topology import branch, machine

from ingesta_delta_lake.ar_control import ArControl
from ingesta_delta_lake.common import IntegrityError, digest, now
from ingesta_delta_lake.control import Control
from ingesta_delta_lake.defects_control import DefectsControl
from ingesta_delta_lake.mp9_control import Mp9Control
from ingesta_delta_lake.rejections_storage import RejectionsControl
from ingesta_delta_lake.review_adapters import (
    ADMISSION_VERSION,
    ADMIT,
    KEEP,
    REPLACE,
    adapter,
    offer,
)
from ingesta_delta_lake.reviews import apply_command, atomic
from ingesta_delta_lake.service import run
from ingesta_delta_lake.topology_storage import TopologyControl


# A later CONT_ZC row for the same minute replaces the stored one, so it is not a review.
@pytest.fixture(
    params=[
        "SIP_MODELO",
        "SIP_DEFECTO",
        "SIP_DEFECTO_STATUS",
        "SIP_DEFECTO_AR",
        "MP9_CAMBIO_MODELO",
        "LISTA_DEFECTOS",
        "LISTA_RECH",
        "LISTA_AR",
        "SIP_MAQUINA_MOLDEO",
        "SIP_LINEA_ZF",
    ]
)
def conflict(config, request):
    family = request.param
    config = replace(config, family=family, timestamp_policy="local_naive_test_v1")
    path = config.sources[0][1] / (family + ".csv")
    if family == "SIP_MODELO":
        first, second, control_type = "754;3081/360;1;\n", "754;3081/361;1;\n", Control
    elif family == "MP9_CAMBIO_MODELO":
        model = path.with_name("SIP_MODELO.csv")
        model.write_text("754;3081/360;1;\n", encoding="utf-8")
        run(replace(config, family="SIP_MODELO"))
        first, second, control_type = mp9_line(), mp9_line(machine=8), Mp9Control
    elif family in {"SIP_DEFECTO", "SIP_DEFECTO_STATUS", "SIP_DEFECTO_AR"}:
        first = {
            "SIP_DEFECTO": "501;129;129: Pliegue cuello;Pliegue;0;\n",
            "SIP_DEFECTO_STATUS": "21;1;Tiempo Real;1;TR;2;1;\n",
            "SIP_DEFECTO_AR": "71;3;Aviso ZF;2;\n",
        }[family]
        second, control_type = (
            first.replace("Pliegue", "Otro")
            .replace("Tiempo Real", "Corregido")
            .replace("Aviso ZF", "Aviso corregido"),
            Control,
        )
    elif family == "LISTA_AR":
        for dictionary, text in {
            "SIP_DEFECTO": "501;129;129: Pliegue cuello;Pliegue;0;\n",
            "SIP_DEFECTO_STATUS": "21;1;Tiempo Real;1;TR;2;1;\n",
            "SIP_DEFECTO_AR": "71;3;Aviso ZF;2;\n",
        }.items():
            path.with_name(dictionary + ".csv").write_text(text, encoding="utf-8")
            run(replace(config, family=dictionary))
        first, second, control_type = (
            ar_line(off="14/10/2026 10:00:00"),
            ar_line(off="14/10/2026 11:00:00"),
            ArControl,
        )
    elif family in {"SIP_MAQUINA_MOLDEO", "SIP_LINEA_ZF"}:
        config = replace(config, topology_enabled=True, line_code_by_origin=(("AV21", 21),))
        if family == "SIP_LINEA_ZF":
            path.with_name("SIP_MAQUINA_MOLDEO.csv").write_text(
                machine(7, "AV21A", code=21) + machine(8, "AV21B", code=21), encoding="utf-8"
            )
            run(replace(config, family="SIP_MAQUINA_MOLDEO"))
            first, second = branch(number=1), branch(number=2)
        else:
            first, second = machine(7, "AV21A", code=21), machine(7, "AV21B", code=21)
        control_type = TopologyControl
    elif family == "LISTA_RECH":
        first, second, control_type = rech_line(), rech_line(rejected=20), RejectionsControl
    else:
        first, second, control_type = mia_line(), mia_line(rejected=8), DefectsControl
    path.write_text(first, encoding="utf-8")
    assert run(config)["status"] == "APPLIED"
    path.write_text(second, encoding="utf-8")
    assert run(config)["status"] in {"BLOCKED", "PARTIAL"}
    return config, control_type, path, first, second


def command(config, control, action):
    issue = control.db.execute("SELECT * FROM issues WHERE status!='RESOLVED' LIMIT 1").fetchone()
    choice = offer(config, json.loads(issue["payload_json"]))
    assert choice["actions"] == [KEEP, REPLACE]
    return {
        "schema_version": 1,
        "decision_id": str(uuid.uuid4()),
        "projection_issue_id": "a" * 64,
        "source_issue_id": issue["issue_id"],
        "environment": config.environment,
        "plant": config.plant,
        "family": config.family,
        "control_id": control.control_id,
        "issue_hash": choice["issue_hash"],
        "action_id": action,
        "action_version": "1.0.0",
        "candidate_hash": choice["candidates"][0]["hash"] if action == REPLACE else None,
        "proposed_by": "responsable",
        "authorized_by": "iker",
        "authorized_at": now(),
    }


@pytest.mark.parametrize("action", [KEEP, REPLACE])
def test_real_delta_resolution_replay_and_incoming_rejected_variant(conflict, action):
    config, control_type, path, first, second = conflict
    before = DeltaTable(str(config.table_path)).version()
    with closing(control_type(config)) as control:
        decision = command(config, control, action)
        result = apply_command(config, control, decision)
        assert result["status"] == "VERIFIED"
        assert result["delta_version"] == before + (action == REPLACE)
        assert (
            result["after"] == result["before"]
            if action == KEEP
            else result["after"] != result["before"]
        )
        assert (
            control.db.execute("SELECT count(*) FROM issues WHERE status!='RESOLVED'").fetchone()[0]
            == 0
        )
        assert apply_command(config, control, decision) == result
        assert control.db.execute("SELECT count(*) FROM review_operations").fetchone()[0] == 1
        with pytest.raises(IntegrityError, match="identity reused"):
            apply_command(config, control, {**decision, "authorized_by": "another-user"})
    # A later file reintroduces BOTH versions. Only the explicitly rejected one
    # is suppressed; no extra publication, hold or duplicate may be produced.
    path.write_text(first + second, encoding="utf-8")
    replay = run(config)
    assert replay["status"] in {"NO_CHANGE", "NO_INPUTS"}
    assert DeltaTable(str(config.table_path)).version() == result["delta_version"]


def test_stale_accepted_or_scope_cannot_write(conflict):
    config, control_type, *_ = conflict
    before = DeltaTable(str(config.table_path)).version()
    with closing(control_type(config)) as control:
        decision = command(config, control, REPLACE)
        for changed in (
            {"control_id": "other-generation"},
            {"issue_hash": "0" * 64},
            {"candidate_hash": "f" * 64},
            {"plant": "other-plant"},
        ):
            with pytest.raises(IntegrityError):
                apply_command(config, control, {**decision, **changed})
        assert DeltaTable(str(config.table_path)).version() == before


def test_commit_then_crash_recovers_without_double_write(conflict, monkeypatch):
    config, control_type, *_ = conflict
    original = DeltaTable.merge

    def crash_merge(self, *args, **kwargs):
        merger = original(self, *args, **kwargs)

        class Crash:
            def when_matched_update(self, values):
                self.merger = merger.when_matched_update(values)
                return self

            def execute(self):
                self.merger.execute()
                raise RuntimeError("Simulated process loss after Delta commit")

        return Crash()

    with closing(control_type(config)) as control:
        decision = command(config, control, REPLACE)
        before = DeltaTable(str(config.table_path)).version()
        monkeypatch.setattr(DeltaTable, "merge", crash_merge)
        with pytest.raises(RuntimeError, match="process loss"):
            apply_command(config, control, decision)
        assert control.db.execute("SELECT state FROM review_operations").fetchone()[0] == "PREPARED"
        assert DeltaTable(str(config.table_path)).version() == before + 1
        monkeypatch.setattr(DeltaTable, "merge", original)
        result = apply_command(config, control, decision)
        assert result["status"] == "VERIFIED" and result["delta_version"] == before + 1


def test_file_transport_executes_with_normal_writer_and_reexports(conflict, tmp_path, monkeypatch):
    config, control_type, *_ = conflict
    root = tmp_path / "review-transport"
    monkeypatch.setenv("INGESTA_REVIEW_ROOT", str(root))
    with closing(control_type(config)) as control:
        decision = command(config, control, KEEP)
    atomic(root / "commands" / config.family / (decision["decision_id"] + ".json"), decision)
    run(config)
    result = json.loads(
        (root / "results" / config.family / (decision["decision_id"] + ".json")).read_text()
    )
    assert result["status"] == "VERIFIED" and result["command_hash"] == digest(decision)


def test_dependencies_never_offer_unchecked_overwrite(config):
    assert not offer(config, {"reason": "ZC_PENDING"})["actions"]
    assert not offer(config, {"reason": "MACHINE_PENDING"})["actions"]
    with pytest.raises(IntegrityError, match="regenerated"):
        adapter(replace(config, family="CATALOGO_PRODUCCIONES"), "control")


def test_counter_revision_can_replace_the_observed_primary_key(config):
    config = replace(config, family="LISTA_DEFECTOS", timestamp_policy="local_naive_test_v1")
    from ingesta_delta_lake.lista_defectos import FIELDS

    values = ["2026-10-14T00:00:00", 0, 1, 13, 15, 96, *([0] * 11), None]
    row = {"origen_fenix": "AV21", **dict(zip(FIELDS, values, strict=True))}
    result = offer(
        config,
        {"reason": "COUNTER_REVISION", "accepted": [row], "candidate": {**row, "INSPECC": 97}},
    )
    assert result["actions"] == [KEEP, REPLACE]


def test_malformed_transport_does_not_stop_ordinary_ingestion(
    config, source, tmp_path, monkeypatch
):
    run(config)
    root = tmp_path / "reviews"
    monkeypatch.setenv("INGESTA_REVIEW_ROOT", str(root))
    directory = root / "commands" / config.family
    directory.mkdir(parents=True)
    identifier = str(uuid.uuid4())
    (directory / (identifier + ".json")).write_text("{broken json", encoding="utf-8")
    assert run(config)["status"] == "NO_CHANGE"
    receipt = json.loads((root / "results" / config.family / (identifier + ".json")).read_text())
    assert receipt["status"] == "REJECTED"


def test_rejected_generation_command_is_audited_and_receipt_can_be_reissued(
    conflict, tmp_path, monkeypatch
):
    config, control_type, *_ = conflict
    root = tmp_path / "reviews"
    monkeypatch.setenv("INGESTA_REVIEW_ROOT", str(root))
    with closing(control_type(config)) as control:
        decision = {**command(config, control, REPLACE), "control_id": "old-generation"}
    atomic(root / "commands" / config.family / (decision["decision_id"] + ".json"), decision)
    before = DeltaTable(str(config.table_path)).version()
    run(config)
    path = root / "results" / config.family / (decision["decision_id"] + ".json")
    result = json.loads(path.read_text())
    assert result["status"] == "REJECTED" and result["control_id"] == "old-generation"
    assert result["writer_control_id"] != result["control_id"]
    with closing(control_type(config)) as control:
        assert control.db.execute("SELECT state FROM review_operations").fetchone()[0] == "REJECTED"
    path.unlink()
    run(config)
    assert json.loads(path.read_text()) == result
    assert DeltaTable(str(config.table_path)).version() == before


@pytest.mark.parametrize("crash_after_commit", [False, True])
def test_admission_of_conflicting_candidates_without_an_accepted_row(
    conflict, tmp_path, monkeypatch, crash_after_commit
):
    original, control_type, path, first, second = conflict
    config = replace(
        original,
        silver_root=tmp_path / "admission-silver",
        control_root=tmp_path / "admission-control",
        bronze_root=tmp_path / "admission-bronze",
    )
    for family in (
        "SIP_MODELO",
        "SIP_DEFECTO",
        "SIP_DEFECTO_STATUS",
        "SIP_DEFECTO_AR",
        "SIP_MAQUINA_MOLDEO",
    ):
        if family != config.family and path.with_name(family + ".csv").exists():
            run(replace(config, family=family))
    path.write_text(first + second, encoding="utf-8")
    run(config)
    with closing(control_type(config)) as control:
        row = control.db.execute("SELECT * FROM issues WHERE status!='RESOLVED' LIMIT 1").fetchone()
        from ingesta_delta_lake.review_adapters import contextual_offers

        choices = contextual_offers(
            config,
            [
                json.loads(r[0])
                for r in control.db.execute(
                    "SELECT payload_json FROM issues WHERE status!='RESOLVED'"
                )
            ],
        )
        choice = choices[digest(json.loads(row["payload_json"]))]
        assert choice["accepted"] is None
        assert choice["actions"] == [ADMIT]
        decision = {
            "schema_version": 1,
            "decision_id": str(uuid.uuid4()),
            "projection_issue_id": "c" * 64,
            "source_issue_id": row["issue_id"],
            "environment": config.environment,
            "plant": config.plant,
            "family": config.family,
            "control_id": control.control_id,
            "issue_hash": choice["issue_hash"],
            "action_id": ADMIT,
            "action_version": ADMISSION_VERSION,
            "candidate_hash": choice["candidates"][0]["hash"],
            "proposed_by": "responsable",
            "authorized_by": "iker",
            "authorized_at": now(),
        }
        if crash_after_commit:
            from ingesta_delta_lake import reviews

            original_write = reviews.write_deltalake

            def commit_then_crash(*args, **kwargs):
                original_write(*args, **kwargs)
                raise RuntimeError("Process lost after admitting candidate")

            monkeypatch.setattr(reviews, "write_deltalake", commit_then_crash)
            with pytest.raises(RuntimeError, match="lost after"):
                apply_command(config, control, decision)
            monkeypatch.setattr(reviews, "write_deltalake", original_write)
        result = apply_command(config, control, decision)
        assert result["before"] is None and result["after"] == choice["candidates"][0]["row"]
        assert result["status"] == "VERIFIED" and result["operation_id"]
        assert apply_command(config, control, decision) == result
        assert DeltaTable(str(config.table_path)).to_pyarrow_table().num_rows == 1
        assert (
            control.db.execute("SELECT count(*) FROM issues WHERE status!='RESOLVED'").fetchone()[0]
            == 0
        ), [
            dict(r)
            for r in control.db.execute(
                "SELECT reason,payload_json FROM issues WHERE status!='RESOLVED'"
            )
        ]
    assert run(config)["status"] in {"NO_CHANGE", "NO_INPUTS"}
