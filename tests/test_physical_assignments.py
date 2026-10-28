import json
import sqlite3
from dataclasses import replace

import pytest

from ingesta_delta_lake import catalog
from ingesta_delta_lake.association_service import inspect
from ingesta_delta_lake.association_storage import read_artifact
from ingesta_delta_lake.catalog_service import search
from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.service import run


@pytest.fixture
def config(config):
    directory = config.input_root / "AV32"
    directory.mkdir()
    return replace(
        config,
        sources=(("AV32", directory),),
        family="ASIGNACION_PRODUCCION_RAMAL",
        timestamp_policy="local_naive_test_v1",
        line_by_origin=(("AV32", "AV32"),),
        topology_enabled=True,
        line_code_by_origin=(("AV32", 32),),
    )


def pid(config, root):
    return catalog.production_id(config.plant, config.sources[0][0], root)


def sql(config, query):
    with sqlite3.connect(config.control_path) as db:
        return db.execute(query).fetchall()


def ledger(config):
    p = json.loads(
        sql(config, "SELECT payload_json FROM publications ORDER BY no DESC LIMIT 1")[0][0]
    )
    return read_artifact(config, p["artifacts"]["ledger"])


@pytest.fixture
def foundation(config, request):
    p = config.sources[0][1]
    sources = {
        "SIP_MODELO": "1;2001/477;1;\n2;2169/477;1;\n3;2168/242;1;\n",
        "SIP_DEFECTO": "501;129;129: Pliegue;Pliegue;0;\n",
        "SIP_DEFECTO_STATUS": "21;1;Tiempo Real;1;TR;2;1;\n",
        "SIP_DEFECTO_AR": "71;3;Aviso ZF;2;\n",
        "SIP_MAQUINA_MOLDEO": "9;AV32A;1;10;10;2;0;01/01/1900;;32;\n"
        "10;AV32B;1;9;10;2;1;01/01/1900;;32;\n",
        "SIP_LINEA_ZF": "16;1;LV;LV;10;01/01/1900;;\n256;2;LCV;LCV;10;01/01/2025;;\n"
        "17;3;LCM;LCM;9;01/01/1900;;\n18;4;LM;LM;9;01/01/1900;;\n",
        "MP9_CAMBIO_MODELO": "2003;1;28/09/2026 6:04:00;14/10/2026 6:23:00;10;1;1;;2;1;\n"
        "2004;1;28/09/2026 7:29:44;14/10/2026 7:23:14;10;1;2;2003;2;1;\n"
        "2007;2;14/10/2026 6:23:00;;10;1;1;;2;1;\n2008;2;14/10/2026 7:23:14;;10;1;2;2007;2;1;\n"
        "2005;3;08/10/2026 7:20:00;;9;1;1;;1;1;\n2006;3;08/10/2026 8:02:50;;9;1;2;2005;1;1;\n",
        "LISTA_AR": "129;14/10/2026 7:00:00;1;2F;1;3;2001/477;14/10/2026 7:23:14;O;PS;34;1;\n"
        "129;14/10/2026 8:00:00;1;3F;1;3;2169/477;14/10/2026 9:00:00;O;PS;35;2;\n",
        "LISTA_RECH": "14/10/2026 7:30:00;0;1;13;13;8;0;;;\n"
        "14/10/2026 8:10:00;35;1;13;90;1;0;3F;;\n14/10/2026 7:30:00;0;3;14;80;0;0;;;\n",
        "LISTA_DEFECTOS": "".join(
            f"14/10/2026 7:30:00;0;1;13;{defect};13;{rej};{rej};0;0;0;0;0;0;0;0;0;;\n"
            for defect, rej in [(15, 1), (16, 5), (17, 1), (18, 2)]
        )
        + "14/10/2026 8:10:00;35;1;13;15;90;1;1;0;0;0;0;0;0;0;0;0;3F;\n",
        "CONT_ZC": "32;14/10/2026 7:30:00;100;100;100;100;0;180;0;100;100;0;0;80.00;80.00;0;1;\n",
    }
    if getattr(request, "param", None) == "same_model":
        sources["MP9_CAMBIO_MODELO"] = (
            sources["MP9_CAMBIO_MODELO"].replace("2007;2;", "2007;3;").replace("2008;2;", "2008;3;")
        )
        sources["LISTA_AR"] = sources["LISTA_AR"].replace("2169/477", "2168/242")
    for family, text in sources.items():
        (p / (family + ".csv")).write_text(text, encoding="utf-8")
    for family in (*list(sources)[:7], "CATALOGO_PRODUCCIONES", *list(sources)[7:]):
        r = run(replace(config, family=family))
        assert r.get("complete", True), (family, r)
    return p


def test_topology_generates_all_parts_and_assigns_five_transition_rows_to_outgoing(
    config, foundation
):
    result = run(config)
    assert result["accepted_associations"] == 6
    rows = ledger(config)
    transition = [r for r in rows if r["status"] == "TRANSITION_TO_OUTGOING"]
    assert len(transition) == 5
    assert all(r["productions"] == [pid(config, 2003)] for r in transition)
    assert len({r["source_id"] for r in rows}) == len(rows)


def test_physical_episodes_commit_even_if_observation_stage_fails(config, foundation, monkeypatch):
    from ingesta_delta_lake import associations
    from ingesta_delta_lake.association_storage import AssociationStore

    original = associations.assign

    def fail_large_stage(bundle, *args):
        if bundle["sources"]["LISTA_RECH"]:
            raise RuntimeError("Observation computation interrupted")
        return original(bundle, *args)

    with monkeypatch.context() as patch:
        patch.setattr(associations, "assign", fail_large_stage)
        with pytest.raises(RuntimeError, match="interrupted"):
            run(config)
    assert sql(config, "SELECT count(*) FROM publications")[0][0] == 1
    pub = json.loads(sql(config, "SELECT payload_json FROM publications")[0][0])
    assert pub["scope"] == "physical"
    assert pub["observation_status"] == "NOT_EVALUATED"
    cid = sql(config, "SELECT value FROM settings WHERE key='control_id'")[0][0]
    version, rows = AssociationStore(config, cid).read()
    assert version == 0 and len(rows) == 6
    result = run(config)
    assert result["accepted_associations"] == 6
    assert result["counts"]["LISTA_RECH"]["source_rows"] == 3
    assert AssociationStore(config, cid).read()[0] == version
    current = inspect(config)
    assert current["counts"]["LISTA_RECH"]["assigned_rows"] == 3
    assert current["counts"]["LISTA_DEFECTOS"]["assigned_rows"] == 5
    assert current["counts"]["CONT_ZC"]["assigned_rows"] == 1
    shared = next(r for r in ledger(config) if r["family"] == "CONT_ZC")
    assert shared["status"] == "SHARED_MACHINE"
    assert shared["productions"] == sorted([pid(config, 2005), pid(config, 2007)])
    assert {r["id_produccion"] for r in current["associations"] if r["ramal"] in (3, 4)} == {
        pid(config, 2005)
    }
    assert {r["id_produccion"] for r in current["associations"] if r["ramal"] in (1, 2)} == {
        pid(config, 2003),
        pid(config, 2007),
    }
    original = {str(p): p.read_bytes() for p in config.table_path.rglob("*") if p.is_file()}
    assert run(config)["inserted_this_attempt"] == 0
    assert {str(p): p.read_bytes() for p in config.table_path.rglob("*") if p.is_file()} == original


def test_catalog_exposes_physical_machine_and_open_state_without_live_query(config, foundation):
    cfg = replace(config, family="CATALOGO_PRODUCCIONES")
    result = search(cfg, "2169/477")
    p = result["productions"][0]
    assert p["ID_MAQUINA"] == 10 and p["nombre_maquina"] == "AV32B"
    assert p["production_state"] == "ABIERTA_EN_ZC" and p["fecha_fin_zf"] is None
    assert search(cfg, "2001/477")["productions"][0]["production_state"] == "CERRADA"
    assert list(catalog.SCHEMA.names)[-2:] == ["ID_MAQUINA", "ID_ST1_REL"]


def test_zf_confirmation_versions_do_not_block_physical_assignments(config, foundation):
    # Multiple observations of a 0 -> 1 transition must retain every physical part.
    from pathlib import Path

    fresh_root = config.silver_root.parent / "uncertain"
    cfg = replace(
        config,
        bronze_root=fresh_root / "bronze",
        silver_root=fresh_root / "silver",
        control_root=fresh_root / "control",
    )
    p = foundation / "MP9_CAMBIO_MODELO.csv"
    p.write_text(p.read_text() + "2007;2;14/10/2026 6:23:00;;10;1;1;;2;0;\n", encoding="utf-8")
    for f in (
        "SIP_MODELO",
        "SIP_DEFECTO",
        "SIP_DEFECTO_STATUS",
        "SIP_DEFECTO_AR",
        "SIP_MAQUINA_MOLDEO",
        "SIP_LINEA_ZF",
        "MP9_CAMBIO_MODELO",
        "CATALOGO_PRODUCCIONES",
        "LISTA_AR",
        "LISTA_DEFECTOS",
        "LISTA_RECH",
        "CONT_ZC",
    ):
        run(replace(cfg, family=f))
    result = run(cfg)
    assert result["accepted_associations"] == 6
    assert any(r["id_produccion"] == pid(cfg, 2007) for r in inspect(cfg)["associations"])
    assert sql(replace(cfg, family="MP9_CAMBIO_MODELO"), "SELECT COUNT(*) FROM issues") == [(0,)]
    assert len([r for r in ledger(cfg) if r["status"] == "TRANSITION_TO_OUTGOING"]) == 5
    assert Path(config.silver_root).exists()


def test_future_tandem_names_and_branch_numbers_are_not_hardcoded(config, foundation):
    from copy import deepcopy

    from ingesta_delta_lake.association_storage import load_bundle
    from ingesta_delta_lake.physical_assignments import decisions

    bundle = deepcopy(load_bundle(config))
    for r in bundle["sources"]["SIP_MAQUINA_MOLDEO"]:
        r["nombre_maquina"] = "GRUPO_" + str(r["id_maquina_fenix"])
    for r in bundle["sources"]["SIP_LINEA_ZF"]:
        r["codigo_ramal"] += 40
    automatic = decisions(bundle, {}, dict(config.line_code_by_origin))
    cold = [r for r in automatic if r["kind"] == "ZF_ASSOCIATION"]
    assert {r["branch"] for r in cold} == {41, 42, 43, 44}
    assert {r["production"] for r in cold if r["branch"] in (43, 44)} == {pid(config, 2005)}


def test_zc_presence_uses_root_closure_and_shared_line_code(config, foundation):
    from ingesta_delta_lake.association_storage import load_bundle
    from ingesta_delta_lake.physical_assignments import decisions

    bundle = load_bundle(config)
    automatic = decisions(bundle, {}, dict(config.line_code_by_origin))
    hot = [r for r in automatic if r["kind"] == "ZC_PRESENCE"]
    assert len(hot) == 3 and {r["machine"] for r in hot} == {32}
    old = next(r for r in hot if r["production"] == pid(config, 2003))
    assert old["end"] == "2026-10-14T06:23:00"
    assert old["end"] != bundle["sources"]["CATALOGO_PRODUCCIONES"][0]["fecha_fin_zf"]
    registry = {r["id"]: r for r in automatic}
    assert decisions(bundle, registry, dict(config.line_code_by_origin)) == automatic
    bundle["catalog_composition"][pid(config, 2007)]["root"]["FECHA_OFF"] = "2026-10-19T06:00:00"
    revised = next(
        r
        for r in decisions(bundle, registry, dict(config.line_code_by_origin))
        if r["kind"] == "ZC_PRESENCE" and r["production"] == pid(config, 2007)
    )
    assert revised["revision"] == 2 and revised["end"] == "2026-10-19T06:00:00"
    registry[revised["id"]] = {**revised, "actor": "human-reviewer"}
    assert revised["id"] not in {
        r["id"] for r in decisions(bundle, registry, dict(config.line_code_by_origin))
    }


@pytest.mark.parametrize("evidence_family", [None, "LISTA_RECH"])
def test_explicit_correction_of_auto_episode_is_not_reverted_by_next_derivation(
    config, foundation, evidence_family
):
    from ingesta_delta_lake.common import digest

    run(config)
    pub = json.loads(
        sql(config, "SELECT payload_json FROM publications ORDER BY no DESC LIMIT 1")[0][0]
    )
    bundle = read_artifact(config, pub["artifacts"]["bundle"])
    old = next(
        d
        for d in bundle["registry"].values()
        if d["production"] == pid(config, 2007) and d["branch"] == 1
    )
    revised = {
        **old,
        "start": "2026-10-14T08:00:00",
        "revision": 2,
        "previous_hash": digest(old),
        "actor": "reviewer",
        "reason": "Verified effective entry",
    }
    if evidence_family:
        from ingesta_delta_lake.associations import source_id

        revised["evidence"] = [
            {
                "family": evidence_family,
                "source_id": source_id(evidence_family, bundle["sources"][evidence_family][0]),
            }
        ]
    path = config.control_root / "manual.json"
    path.write_text(json.dumps({"version": 1, "decisions": [revised]}), encoding="utf-8")
    cfg = replace(config, association_decisions=path)
    assert run(cfg)["updated_this_attempt"] == 1
    assert run(cfg)["updated_this_attempt"] == 0
    current = inspect(cfg)
    assert (
        next(
            r
            for r in current["associations"]
            if r["id_produccion"] == pid(cfg, 2007) and r["ramal"] == 1
        )["vigente_desde"]
        == "2026-10-14T08:00:00"
    )


def test_old_control_contract_is_rejected_without_adopting_or_mutating_table(config, foundation):
    cfg = replace(config, family="MP9_CAMBIO_MODELO")
    with sqlite3.connect(cfg.control_path) as db:
        binding = json.loads(
            db.execute("SELECT value FROM settings WHERE key='binding'").fetchone()[0]
        )
        binding["contract"] = "mp9_cambio_modelo_v1"
        db.execute(
            "UPDATE settings SET value=? WHERE key='binding'",
            (json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
    before = {str(p): p.read_bytes() for p in cfg.table_path.rglob("*") if p.is_file()}
    with pytest.raises(IntegrityError, match="contrato"):
        run(cfg)
    assert {str(p): p.read_bytes() for p in cfg.table_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("foundation", ["same_model"], indirect=True)
def test_concurrent_same_model_keeps_physical_productions_separate(config, foundation):
    result = run(config)
    assert result["accepted_associations"] == 6
    productions = search(replace(config, family="CATALOGO_PRODUCCIONES"), "2168/242")["productions"]
    assert {p["nombre_maquina"] for p in productions} == {"AV32A", "AV32B"}
    assert len({p["id_produccion"] for p in productions}) == 2
    assert inspect(config)["counts"]["LISTA_RECH"]["assigned_rows"] == 3
    rows = ledger(config)
    assert all(len(r["productions"]) <= 1 for r in rows if r["family"] == "LISTA_RECH")
    assert any(r["family"] == "LISTA_AR" and r["status"] == "AMBIGUOUS_PRODUCTION" for r in rows)


def test_late_closure_versions_auto_episodes_without_changing_identity(config, foundation):
    run(config)
    original = {
        r["id_asignacion"]: r
        for r in inspect(config)["associations"]
        if r["id_produccion"] == pid(config, 2007)
    }
    path = foundation / "MP9_CAMBIO_MODELO.csv"
    path.write_text(
        path.read_text()
        .replace("2007;2;14/10/2026 6:23:00;;", "2007;2;14/10/2026 6:23:00;19/10/2026 6:00:00;")
        .replace("2008;2;14/10/2026 7:23:14;;", "2008;2;14/10/2026 7:23:14;19/10/2026 7:00:00;"),
        encoding="utf-8",
    )
    assert run(replace(config, family="MP9_CAMBIO_MODELO"))["updated_this_attempt"] == 2
    assert inspect(config)["status"] == "STALE"
    run(replace(config, family="CATALOGO_PRODUCCIONES"))
    assert run(config)["updated_this_attempt"] == 2
    current = {
        r["id_asignacion"]: r
        for r in inspect(config)["associations"]
        if r["id_produccion"] == pid(config, 2007)
    }
    assert current.keys() == original.keys()
    assert all(r["vigente_hasta"] == "2026-10-19T07:00:00" for r in current.values())
    assert all(r["revision_decision"] == 2 for r in current.values())
    assert run(config)["updated_this_attempt"] == 0


def test_topology_configuration_changes_invalidate_previous_publication(config, foundation):
    run(config)
    assert inspect(replace(config, topology_enabled=False))["status"] == "STALE"
    assert inspect(replace(config, line_code_by_origin=(("AV32", 99),)))["status"] == "STALE"
