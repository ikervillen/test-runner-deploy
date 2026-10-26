import json
import sqlite3
from dataclasses import replace

import pytest

from ingesta_delta_lake import associations as a
from ingesta_delta_lake import catalog
from ingesta_delta_lake.association_service import inspect
from ingesta_delta_lake.association_storage import (
    AssociationControl,
    AssociationStore,
    load_bundle,
    read_artifact,
)
from ingesta_delta_lake.common import IngestaError, IntegrityError, digest
from ingesta_delta_lake.service import run


@pytest.fixture
def config(config):
    return replace(
        config,
        family="ASIGNACION_PRODUCCION_RAMAL",
        timestamp_policy="local_naive_test_v1",
        line_by_origin=(("AV21", "AV21"),),
    )


def pid(root):
    return catalog.production_id("AIALA", "AV21", root)


def decision(
    identity="old",
    kind="ZF_ASSOCIATION",
    production=None,
    start="2026-10-14T06:00:00",
    end="2026-10-14T07:23:00",
    **extra,
):
    d = {
        "id": identity,
        "kind": kind,
        "origin": "AV21",
        "start": start,
        "end": end,
        "revision": 1,
        "actor": "test reviewer",
        "reason": "verified synthetic effective interval",
        "evidence": [{"external": "TEST-EVIDENCE", "note": "Synthetic approved observation"}],
    }
    if kind.startswith("ZF"):
        d["branch"] = 1
    else:
        d["machine"] = 23
    if not kind.endswith("PREPARATION"):
        d["production"] = production or pid(10)
    return {**d, **extra}


@pytest.fixture
def foundation(config):
    p = config.input_root / "AV21"
    files = {
        "SIP_MODELO": "1;1000/001;1;\n2;2000/002;1;\n3;3000/003;1;\n",
        "SIP_DEFECTO": "501;129;129: Pliegue;Pliegue;0;\n",
        "SIP_DEFECTO_STATUS": "21;1;Tiempo Real;1;TR;2;1;\n",
        "SIP_DEFECTO_AR": "71;3;Aviso ZF;2;\n",
        "MP9_CAMBIO_MODELO": "10;1;01/10/2026 6:00:00;14/10/2026 7:23:00;7;1;1;;1;1;\n"
        "11;1;01/10/2026 6:00:00;14/10/2026 7:23:00;7;1;2;10;1;1;\n"
        "20;2;14/10/2026 7:23:00;;7;1;1;;1;1;\n21;2;14/10/2026 7:23:00;;7;1;2;20;1;1;\n"
        "30;3;01/10/2026 6:00:00;;8;1;1;;1;1;\n31;3;01/10/2026 6:00:00;;8;1;2;30;1;1;\n",
        "LISTA_AR": "129;14/10/2026 6:00:00;21;2F;1;3;1000/001;14/10/2026 7:23:00;O;PS;34;1;\n"
        "129;14/10/2026 8:00:00;21;3F;1;3;2000/002;14/10/2026 9:00:00;O;PS;35;2;\n",
        "LISTA_RECH": "14/10/2026 7:30:00;34;1;13;96;2;0;2F;;\n"
        "14/10/2026 8:10:00;35;1;13;90;1;0;3F;;\n",
        "LISTA_DEFECTOS": "14/10/2026 7:30:00;34;1;13;15;96;2;2;0;0;0;0;0;0;0;0;0;2F;\n"
        "14/10/2026 8:10:00;35;1;13;15;90;1;1;0;0;0;0;0;0;0;0;0;3F;\n",
        "CONT_ZC": "23;14/10/2026 7:10:00;200;0;200;0;0;100;0;200;0;200;0;80.00;0.00;0;1;\n"
        "23;14/10/2026 8:10:00;100;0;100;0;0;80;0;100;0;100;0;80.00;0.00;0;1;\n",
    }
    for family, text in files.items():
        (p / f"{family}.csv").write_text(text, encoding="utf-8")
    for family in (*list(files)[:5], "CATALOGO_PRODUCCIONES", *list(files)[5:]):
        result = run(replace(config, family=family))
        assert result.get("complete", True) and result["status"] == "APPLIED"
    return p


def write_decisions(config, items):
    config.control_root.mkdir(parents=True, exist_ok=True)
    path = config.control_root / "decisions.json"
    path.write_text(json.dumps({"version": 1, "decisions": items}), encoding="utf-8")
    return replace(config, association_decisions=path)


def query(config, sql):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(sql).fetchall()


def test_confirmed_snapshot_remains_readable_after_new_source_commit(config, foundation):
    from ingesta_delta_lake.association_storage import source_snapshot

    before, _, descriptor, _ = source_snapshot(config, "LISTA_RECH")
    path = foundation / "LISTA_RECH.csv"
    with path.open("a", encoding="utf-8") as stream:
        stream.write("14/10/2026 8:20:00;35;1;13;91;1;0;3F;;\n")
    run(replace(config, family="LISTA_RECH"))
    fixed, _, actual, _ = source_snapshot(config, "LISTA_RECH", expected=descriptor)
    assert fixed == before and actual == descriptor
    assert len(source_snapshot(config, "LISTA_RECH")[0]) == len(before) + 1
    with pytest.raises(IntegrityError, match="control"):
        source_snapshot(config, "LISTA_RECH", expected={**descriptor, "control_id": "another"})


def test_catalog_publication_and_composition_are_pinned(config, foundation):
    from ingesta_delta_lake.association_storage import source_snapshot

    _, _, descriptor, composition = source_snapshot(config, "CATALOGO_PRODUCCIONES")
    fixed = load_bundle(
        config,
        manifest={
            family: source_snapshot(config, family, read=False)[2]
            for family in a.SOURCE_KEYS
            if family not in {"SIP_MAQUINA_MOLDEO", "SIP_LINEA_ZF"}
        },
    )
    assert fixed["manifest"]["CATALOGO_PRODUCCIONES"] == descriptor
    assert fixed["catalog_composition"] == composition["composition"]


def files(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "items,expected",
    [
        ([decision()], "PENDING_COVERAGE"),
        (
            [
                decision(),
                decision(
                    "prep",
                    kind="ZF_PREPARATION",
                    start="2026-10-14T07:23:00",
                    end="2026-10-14T08:00:00",
                ),
            ],
            "OLD_PLUS_PREPARATION",
        ),
        (
            [
                decision(
                    "prep",
                    kind="ZF_PREPARATION",
                    start="2026-10-14T07:20:00",
                    end="2026-10-14T07:23:00",
                ),
                decision(start="2026-10-14T07:23:00", end="2026-10-14T08:00:00"),
            ],
            "NEW_PLUS_PREPARATION",
        ),
        ([decision(end="2026-10-14T08:00:00")], "ASSIGNED"),
        (
            [
                decision(
                    "prep",
                    kind="ZF_PREPARATION",
                    start="2026-10-14T07:20:00",
                    end="2026-10-14T07:30:00",
                )
            ],
            "PREPARATION",
        ),
        (
            [
                decision(),
                decision("new", production=pid(20), start="2026-10-14T07:23:00", end=None),
            ],
            "MIXED_MODELS",
        ),
    ],
)
def test_full_period_rules_never_use_only_end_timestamp_or_prorate(items, expected):
    result = a.interval_decision(items, "2026-10-14T07:20:00", "2026-10-14T07:30:00")
    assert result[0] == expected
    if expected in {"MIXED_MODELS", "PREPARATION", "PENDING_COVERAGE"}:
        assert not result[1]


@pytest.mark.parametrize(
    "first_end,second_start,conflict",
    [
        (None, "2026-10-14T07:00:00", True),
        ("2026-10-14T07:23:00", "2026-10-14T07:23:00", False),
        ("2026-10-14T07:23:00", "2026-10-14T07:20:00", True),
    ],
)
def test_overlap_checks_open_and_touching_episodes(first_end, second_start, conflict):
    items = {
        "a": decision("a", end=first_end),
        "b": decision("b", production=pid(20), start=second_start, end=None),
    }
    assert bool(a.validate_registry(items)) == conflict


def test_proposals_do_not_invent_accepted_start_or_end(config, foundation):
    result = run(config)
    assert result["status"] == "PARTIAL" and result["accepted_associations"] == 0
    report = inspect(config)
    assert report["fresh"] and len(report["candidates"]) == 2
    assert {c["status"] for c in report["candidates"]} == {"NEEDS_REVIEW"}
    assert not config.table_path.exists()
    assert all(c["evidence"] for c in report["candidates"])
    for count in report["counts"].values():
        assert count["source_rows"] == count["assigned_rows"] + count["unassigned_rows"]


def test_published_episodes_assign_old_plus_preparation_and_new_without_altering_sources(
    config, foundation
):
    original = files(config.silver_root)
    cfg = write_decisions(
        config,
        [
            decision(),
            decision(
                "prep",
                kind="ZF_PREPARATION",
                start="2026-10-14T07:23:00",
                end="2026-10-14T08:00:00",
            ),
            decision("new", production=pid(20), start="2026-10-14T08:00:00", end=None),
        ],
    )
    result = run(cfg)
    assert result["accepted_associations"] == 2 and result["inserted_this_attempt"] == 2
    report = inspect(cfg)
    assert report["counts"]["LISTA_RECH"]["assigned_rows"] == 2
    assert report["counts"]["LISTA_RECH"]["by_status"] == {"OLD_PLUS_PREPARATION": 1, "ASSIGNED": 1}
    for path, data in original.items():
        assert (config.silver_root / path).read_bytes() == data
    own = files(cfg.table_path)
    attempts = query(cfg, "SELECT COUNT(*) FROM attempts")
    inspect(cfg)
    assert query(cfg, "SELECT COUNT(*) FROM attempts") == attempts and files(cfg.table_path) == own
    assert run(cfg)["inserted_this_attempt"] == 0 and files(cfg.table_path) == own


def test_bad_overlap_is_reviewed_not_published_and_omission_is_not_resolution(config, foundation):
    cfg = write_decisions(
        config,
        [
            decision(end=None),
            decision("new", production=pid(20), start="2026-10-14T07:23:00", end=None),
        ],
    )
    result = run(cfg)
    assert result["accepted_associations"] == 0 and result["open_reviews"] == 1
    cfg = write_decisions(cfg, [])
    assert run(cfg)["open_reviews"] == 1


def test_retroactive_revision_keeps_id_and_changes_ledger_with_before_after_audit(
    config, foundation
):
    cfg = write_decisions(config, [decision(end="2026-10-14T08:00:00")])
    run(cfg)
    report = inspect(cfg)
    identity = report["associations"][0]["id_asignacion"]
    with sqlite3.connect(cfg.control_path) as db:
        pub = json.loads(
            db.execute("SELECT payload_json FROM publications ORDER BY no DESC LIMIT 1").fetchone()[
                0
            ]
        )
    previous_bundle = read_artifact(cfg, pub["artifacts"]["bundle"])
    old = previous_bundle["registry"]["old"]
    updated = decision(
        end="2026-10-14T07:23:00",
        revision=2,
        previous_hash=digest(old),
        reason="Corrected effective end from reviewed test evidence",
    )
    cfg = write_decisions(cfg, [updated])
    result = run(cfg)
    assert result["updated_this_attempt"] == 1 and result["delta_version"] == 1
    new = inspect(cfg)
    assert new["associations"][0]["id_asignacion"] == identity
    assert new["counts"]["LISTA_RECH"]["assigned_rows"] == 0
    assert previous_bundle["registry"]["old"]["end"] == "2026-10-14T08:00:00"
    assert query(cfg, "SELECT COUNT(*) FROM association_intents WHERE before_json IS NOT NULL") == [
        (1,)
    ]
    assert result["counts"]["LISTA_RECH"]["unassigned_rows"] == 2


def test_correction_requires_previous_hash_and_does_not_resolve_by_reverting_file(
    config, foundation
):
    cfg = write_decisions(config, [decision(end="2026-10-14T08:00:00")])
    run(cfg)
    own = files(cfg.table_path)
    cfg = write_decisions(
        cfg, [decision(end="2026-10-14T07:23:00", revision=2, previous_hash="wrong")]
    )
    result = run(cfg)
    assert result["open_reviews"] == 1 and files(cfg.table_path) == own
    assert result["counts"]["LISTA_RECH"]["by_status"] == {"DECISION_REVIEW": 2}
    cfg = write_decisions(cfg, [decision(end="2026-10-14T08:00:00")])
    assert run(cfg)["open_reviews"] == 1


def test_zc_shared_targets_are_distinct_from_zf_and_joint_query_does_not_duplicate_wheel(
    config, foundation
):
    cfg = write_decisions(
        config,
        [
            decision(
                "z1", kind="ZC_PRESENCE", production=pid(20), start="2026-10-14T08:00:00", end=None
            ),
            decision(
                "z2", kind="ZC_PRESENCE", production=pid(30), start="2026-10-14T08:00:00", end=None
            ),
        ],
    )
    run(cfg)
    left = inspect(cfg, productions=[pid(20)])
    right = inspect(cfg, productions=[pid(30)])
    joint = inspect(cfg, productions=[pid(20), pid(30)])
    assert (
        left["selection"]["zc_yield"]["rate_percent"]
        == right["selection"]["zc_yield"]["rate_percent"]
        == "80.0000"
    )
    assert joint["selection"]["physical_zc_rows"] == 1
    assert joint["selection"]["zc_yield"]["eligible_wheel"] == 80
    assert joint["counts"]["LISTA_RECH"]["assigned_rows"] == 0


@pytest.mark.parametrize("phase", ["before", "after", "ack"])
def test_publication_recovery_does_not_duplicate_episodes_or_intents(
    config, foundation, monkeypatch, phase
):
    cfg = write_decisions(config, [decision()])
    original = AssociationStore.apply_changes

    def fail(self, *args):
        if phase == "after":
            original(self, *args)
        raise OSError("simulated")

    with monkeypatch.context() as patch:
        if phase == "ack":
            patch.setattr(
                AssociationControl, "confirm", lambda *_: (_ for _ in ()).throw(OSError("ack"))
            )
        else:
            patch.setattr(AssociationStore, "apply_changes", fail)
        with pytest.raises(IngestaError):
            run(cfg)
    result = run(cfg)
    assert (
        result["accepted_associations"] == 1
        and result["delta_version"] == 0
        and result["recovered_operations"]
    )
    assert query(cfg, "SELECT state,COUNT(*) FROM association_intents GROUP BY state") == [
        ("CONFIRMED", 1)
    ]


def test_snapshot_and_decision_changes_mark_read_stale_without_implicit_rebuild(config, foundation):
    cfg = write_decisions(config, [decision()])
    run(cfg)
    cfg = write_decisions(cfg, [])
    assert inspect(cfg)["status"] == "STALE"
    cfg = write_decisions(cfg, [decision()])
    (foundation / "LISTA_RECH.csv").write_text(
        (foundation / "LISTA_RECH.csv").read_text() + "14/10/2026 8:20:00;35;1;13;90;1;0;3F;;\n",
        encoding="utf-8",
    )
    run(replace(cfg, family="LISTA_RECH"))
    before = query(cfg, "SELECT COUNT(*) FROM attempts")
    assert (
        inspect(cfg)["status"] == "STALE" and query(cfg, "SELECT COUNT(*) FROM attempts") == before
    )


def test_immutable_artifact_tampering_is_rejected(config, foundation):
    run(config)
    publication = json.loads(
        query(config, "SELECT payload_json FROM publications ORDER BY no DESC LIMIT 1")[0][0]
    )
    from pathlib import Path

    Path(publication["artifacts"]["ledger"]["path"]).write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        inspect(config)


def test_unchanged_sources_reuse_publication_without_loading_or_reassigning(
    config, foundation, monkeypatch
):
    from ingesta_delta_lake import association_service

    first = run(config)
    before = query(config, "SELECT COUNT(*) FROM publications")

    def forbidden(*_, **__):
        raise AssertionError("Unchanged historical facts must not be materialized again")

    monkeypatch.setattr(association_service, "load_bundle", forbidden)
    monkeypatch.setattr(association_service, "read_artifact", forbidden)
    monkeypatch.setattr(a, "assign", forbidden)
    replay = run(config)
    assert replay["recomputed_source_rows"] == 0
    assert replay["counts"] == first["counts"] and replay["batch_id"] == first["batch_id"]
    assert replay["inserted_this_attempt"] == replay["updated_this_attempt"] == 0
    assert query(config, "SELECT COUNT(*) FROM publications") == before


def test_changed_source_invalidates_publication_cache(config, foundation):
    run(config)
    (foundation / "LISTA_RECH.csv").write_text(
        "14/10/2026 8:20:00;35;1;13;90;1;0;3F;;\n", encoding="utf-8"
    )
    run(replace(config, family="LISTA_RECH"))
    assert run(config)["counts"]["LISTA_RECH"]["source_rows"] == 3


def test_previous_publication_without_registry_is_adopted_once(config, foundation):
    run(config)
    with sqlite3.connect(config.control_path) as db:
        no, text = db.execute(
            "SELECT no,payload_json FROM publications ORDER BY no DESC LIMIT 1"
        ).fetchone()
        publication = json.loads(text)
        for key in (
            "registry",
            "registry_hash",
            "input_fingerprint",
            "review_hash",
            "assignment_hash",
        ):
            publication.pop(key)
        db.execute(
            "UPDATE publications SET payload_json=? WHERE no=?", (json.dumps(publication), no)
        )
    db.close()
    # Same logical payload may reuse an existing completed batch. It must remain valid.
    assert run(config)["counts"]["LISTA_RECH"]["source_rows"] == 2


def test_tampered_registry_prevents_fast_replay(config, foundation):
    run(config)
    with sqlite3.connect(config.control_path) as db:
        no, text = db.execute(
            "SELECT no,payload_json FROM publications ORDER BY no DESC LIMIT 1"
        ).fetchone()
        publication = json.loads(text)
        publication["registry_hash"] = "0" * 64
        db.execute(
            "UPDATE publications SET payload_json=? WHERE no=?", (json.dumps(publication), no)
        )
    db.close()
    with pytest.raises(IntegrityError, match="decisiones"):
        run(config)


@pytest.fixture
def bundle(config, foundation):
    return load_bundle(config)


@pytest.mark.parametrize(
    "change",
    [
        {"id": ["invalid"]},
        {"origin": ["invalid"]},
        {"kind": ["invalid"]},
        {"kind": "OTHER"},
        {"origin": "MISSING"},
        {"branch": True},
        {"branch": 2**63},
        {"revision": True},
        {"revision": 0},
        {"start": "2026-10-14T06:00:00Z"},
        {"end": "2026-10-14T05:00:00"},
        {"production": "MISSING"},
        {"evidence": []},
        {"evidence": [{"family": "LISTA_RECH", "source_id": "absent"}]},
        {"evidence": [{"family": [], "source_id": "absent"}]},
        {"unknown": "field"},
        {"machine": 23},
        {"kind": "ZF_PREPARATION", "end": None},
    ],
)
def test_invalid_decisions_are_reviewed_without_publishing_or_crashing(bundle, change):
    accepted, issues = a.propose_registry(
        [{**decision(), **change}], {}, bundle, "AIALA", {"AV21": "AV21"}
    )
    assert not accepted and issues[0]["reason"] == "DECISION_REVIEW"


def test_reviewed_source_cannot_be_used_as_evidence_or_ar_crossing(bundle):
    row = bundle["sources"]["LISTA_AR"][0]
    evidence = [{"family": "LISTA_AR", "source_id": a.source_id("LISTA_AR", row)}]
    assert a.normalize_decision(decision(evidence=evidence), bundle, "AIALA", {"AV21": "AV21"})
    bundle["cases"]["SIP_DEFECTO_AR"] = [{"payload": {"detail": "dictionary conflict"}}]
    assert not a.prepare_context(bundle)
    with pytest.raises(ValueError, match="revisión"):
        a.normalize_decision(decision(evidence=evidence), bundle, "AIALA", {"AV21": "AV21"})
    ledger = a.assign(bundle, {}, {})
    assert {r["status"] for r in ledger if r["family"] == "LISTA_AR"} == {"SOURCE_REVIEW"}


def test_same_model_concurrent_productions_remain_ambiguous(bundle):
    from copy import deepcopy

    p = deepcopy(
        next(p for p in bundle["sources"]["CATALOGO_PRODUCCIONES"] if p["id_produccion"] == pid(10))
    )
    p["id_produccion"] = "second-root-same-model"
    bundle["sources"]["CATALOGO_PRODUCCIONES"].append(p)
    bundle["catalog_composition"][p["id_produccion"]] = deepcopy(
        bundle["catalog_composition"][pid(10)]
    )
    index = a.prepare_context(bundle)
    candidates = a.candidates(bundle, index)
    assert any(c["status"] == "AMBIGUOUS_PRODUCTION" for c in candidates)
    ledger = a.assign(bundle, {}, index)
    ambiguous = [r for r in ledger if r["status"] == "AMBIGUOUS_PRODUCTION"]
    assert ambiguous and all(not r["productions"] for r in ambiguous)


def test_unknown_mold_cavity_can_assign_by_approved_branch_and_positive_preparation_is_conflict(
    bundle,
):
    row = bundle["sources"]["LISTA_RECH"][0]
    row.update(MOLDE=0, CAVIDAD=None)
    approved = decision(end="2026-10-14T08:00:00")
    assert not any(c["model_code"] == "1000/001" for c in a.candidates(bundle, {}))
    ledger = a.assign(bundle, {"old": approved}, {})
    selected = next(r for r in ledger if r["source_id"] == a.source_id("LISTA_RECH", row))
    assert selected["status"] == "ASSIGNED" and selected["productions"] == [pid(10)]
    prep = decision("prep", kind="ZF_PREPARATION", end="2026-10-14T08:00:00")
    prep.pop("production", None)
    ledger = a.assign(bundle, {"prep": prep}, {})
    assert (
        next(r for r in ledger if r["source_id"] == selected["source_id"])["status"]
        == "FLOW_DURING_PREPARATION"
    )
    row.update(INSPECC=0, RECH=0)
    ledger = a.assign(bundle, {"prep": prep}, {})
    assert (
        next(r for r in ledger if r["source_id"] == a.source_id("LISTA_RECH", row))["status"]
        == "PREPARATION"
    )


def test_closed_zero_length_ar_at_boundary_belongs_to_new_zone_not_old(bundle):
    row = dict(bundle["sources"]["LISTA_AR"][0])
    row.update(FECHA_ON="2026-10-14T07:23:00", FECHA_OFF="2026-10-14T07:23:00")
    assert a.ar_productions(bundle, row) == []
    row["MODELO"] = "2000/002"
    assert a.ar_productions(bundle, row) == [pid(20)]


def test_conflicting_model_evidence_blocks_even_approved_branch(bundle):
    row = bundle["sources"]["LISTA_RECH"][0]
    ar = dict(bundle["sources"]["LISTA_AR"][1])
    ar.update(MOLDE=row["MOLDE"], CAVIDAD=row["CAVIDAD"], FECHA_ON="2026-10-14T07:20:00")
    bundle["sources"]["LISTA_AR"] = [ar]
    ledger = a.assign(
        bundle, {"old": decision(end="2026-10-14T08:00:00")}, a.prepare_context(bundle)
    )
    selected = next(r for r in ledger if r["source_id"] == a.source_id("LISTA_RECH", row))
    assert selected["status"] == "EVIDENCE_CONFLICT" and not selected["productions"]


def test_recovery_uses_frozen_artifacts_when_live_csv_changes(config, foundation, monkeypatch):
    cfg = write_decisions(config, [decision()])
    with monkeypatch.context() as patch:
        patch.setattr(
            AssociationStore, "apply_changes", lambda *_: (_ for _ in ()).throw(OSError("before"))
        )
        with pytest.raises(IngestaError):
            run(cfg)
    (foundation / "LISTA_RECH.csv").write_text("not the prepared data", encoding="utf-8")
    result = run(cfg)
    assert result["recovered_operations"] and result["accepted_associations"] == 1
    assert result["counts"]["LISTA_RECH"]["source_rows"] == 2


def test_valid_revision_resolves_case_and_explanation_references_published_decision(
    config, foundation
):
    cfg = write_decisions(config, [decision(end="2026-10-14T08:00:00")])
    run(cfg)
    pub = json.loads(
        query(cfg, "SELECT payload_json FROM publications ORDER BY no DESC LIMIT 1")[0][0]
    )
    old = read_artifact(cfg, pub["artifacts"]["bundle"])["registry"]["old"]
    cfg = write_decisions(cfg, [decision(revision=2, previous_hash="wrong")])
    assert run(cfg)["open_reviews"] == 1
    cfg = write_decisions(cfg, [decision(revision=2, previous_hash=digest(old))])
    assert run(cfg)["open_reviews"] == 0
    assert query(cfg, "SELECT COUNT(*) FROM review_events") == [(1,)]
    pub = json.loads(
        query(cfg, "SELECT payload_json FROM publications ORDER BY no DESC LIMIT 1")[0][0]
    )
    ledger = read_artifact(cfg, pub["artifacts"]["ledger"])
    explanation = inspect(cfg, source_id=ledger[0]["source_id"])["explanation"]
    assert len(explanation) == 1 and explanation[0]["rule"] == a.RULE


def test_cli_build_inspect_and_direct_ingest_guard(config, foundation, monkeypatch, capsys):
    from ingesta_delta_lake import cli

    monkeypatch.setattr(cli, "load_config", lambda _: config)
    assert cli.main(["associations", "inspect", "--config", "test.toml"]) == 4
    assert json.loads(capsys.readouterr().out)["status"] == "NO_ASSOCIATIONS"
    assert cli.main(["ingest", "--config", "test.toml"]) == 3
    assert "associations build" in capsys.readouterr().err
    assert cli.main(["associations", "build", "--config", "test.toml"]) == 2
    assert json.loads(capsys.readouterr().out)["candidate_associations"] == 2
    assert (
        cli.main(["associations", "inspect", "--config", "test.toml", "--production", pid(10)]) == 2
    )
    assert json.loads(capsys.readouterr().out)["selection"]["productions"] == [pid(10)]
