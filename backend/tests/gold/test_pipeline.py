"""Industrial-shaped fixtures: accepted inputs, calculation and atomic publication."""

import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pytest
from deltalake import CommitProperties, write_deltalake

from app.gold.attribution import source_id
from app.gold.cli import main
from app.gold.common import IntegrityError, atomic_json, read_json
from app.gold.contracts import CONTRACT_VERSION, CONTRACTS, contract_id, validate
from app.gold.failures import EXIT_DATA
from app.gold.pipeline import build, run
from app.gold.publications import Publisher, Snapshot, promote, reference

START = datetime(2026, 11, 1, 6)
CUTOFF = START + timedelta(hours=8)
PROJECT = Path(__file__).resolve().parents[3]


def inputs():
    data = {
        name: []
        for name in (
            "CATALOGO_PRODUCCIONES",
            "MP9_CAMBIO_MODELO",
            "production_parameters",
            "ASIGNACION_PRODUCCION_RAMAL",
            "LISTA_DEFECTOS",
            "LISTA_RECH",
            "LISTA_AR",
            "CONT_ZC",
            "observation_assignments",
        )
    }
    data.update(
        {
            "SIP_MODELO": [{"id_modelo_fenix": 10, "codigo_modelo_botella": "001/100"}],
            "SIP_MAQUINA_MOLDEO": [
                {
                    "id_maquina_fenix": 1,
                    "nombre_maquina": "Machine A",
                    "codigo_linea_zc": 23,
                    "vigente_desde": START - timedelta(days=1),
                    "vigente_hasta": None,
                }
            ],
            "machine_gobs": [
                {
                    "id_maquina_fenix": 1,
                    "id_gotas": 7,
                    "vigente_desde": START - timedelta(days=1),
                    "vigente_hasta": None,
                }
            ],
            "gobs_dictionary": [{"id_gotas": 7, "valor": 2}],
            "SIP_DEFECTO": [
                {
                    "codigo_aviso_rechazo": 53,
                    "id_defecto_fenix": 9,
                    "descripcion": "Oval mouth",
                    "descripcion_corta": "Oval",
                }
            ],
            "SIP_DEFECTO_STATUS": [
                {
                    "id_status_fenix": 1,
                    "codigo_status": 1,
                    "descripcion": "Alert",
                    "descripcion_corta": "A",
                    "codigo_origen": 1,
                    "codigo_zona": 2,
                }
            ],
            "SIP_DEFECTO_AR": [
                {
                    "id_tipo_ar_fenix": 1,
                    "codigo_tipo_ar": 1,
                    "descripcion": "Cold inspection",
                    "codigo_agrupacion": 1,
                }
            ],
        }
    )
    for n in range(4):
        identity, assignment = f"production-{n}", f"assignment-{n}"
        start, end = START + timedelta(hours=n * 2), START + timedelta(hours=n * 2 + 1)
        data["CATALOGO_PRODUCCIONES"].append(
            {
                "id_produccion": identity,
                "planta": "AV",
                "origen_fenix": "AV23",
                "linea": "AV23",
                "ID_MAQUINA": 1,
                "id_zc_raiz": n * 2 + 1,
                "fecha_inicio_zc": start,
                "fecha_fin_zf": end,
            }
        )
        for offset, status in ((1, 1), (2, 2)):
            data["MP9_CAMBIO_MODELO"].append(
                {
                    "origen_fenix": "AV23",
                    "ID": n * 2 + offset,
                    "STATUS": status,
                    "ID_ST1_REL": n * 2 + 1 if status == 2 else None,
                    "ID_MAQUINA": 1,
                    "ID_MODELO": 10,
                    "FECHA_ON": start,
                    "FECHA_OFF": end,
                }
            )
        data["production_parameters"].append(
            {
                "id": n,
                "origen_fenix": "AV23",
                "id_cambio_modelo": n * 2 + 1,
                "fecha_on": start,
                "fecha_off": end,
                "num_secciones": 8,
            }
        )
        data["ASIGNACION_PRODUCCION_RAMAL"].append(
            {
                "id_asignacion": assignment,
                "id_produccion": identity,
                "zona": "ZF",
                "ramal": 1,
                "vigente_desde": start,
                "vigente_hasta": end,
                "version_regla": "accepted-v1",
                "revision_decision": 1,
                "evidencia_origen": f"accepted:{identity}",
            }
        )
        common = {
            "origen_fenix": "AV23",
            "FECHA": end,
            "MOLDE": 100,
            "LINEA": 1,
            "COD_MAQ_ZF": 12,
            "INSPECC": 1000,
            "CAVIDAD": "1A",
        }
        observations = {
            "LISTA_RECH": {**common, "RECH": 10, "RECH_AUTO": 0, "FECHA_R": end},
            "LISTA_DEFECTOS": {
                **common,
                "COD_DEF": 1,
                "RECH": (n + 1) * 10,
                **{f"RECH{i}": 0 for i in range(1, 11)},
            },
            "LISTA_AR": {
                "origen_fenix": "AV23",
                "COD_AR": 53,
                "FECHA_ON": start,
                "FECHA_OFF": start + timedelta(minutes=15),
                "MAQ_IS": 23,
                "CAVIDAD": "1A",
                "STATUS": 1,
                "TIPO": 1,
                "MODELO": "001/100",
                "COLOR": "green",
                "PROCESO": "production",
                "MOLDE": 100,
                "ID_Agrupación": 1,
            },
            "CONT_ZC": {
                "origen_fenix": "AV23",
                "MAQ_IS": 23,
                "FECHA": end,
                "RUEDA": 950,
                "TEORICO_A": 500,
                "TEORICO_B": 500,
                "SALIDA_MAQ_IS": 950,
            },
        }
        for family, row in observations.items():
            data[family].append(row)
            data["observation_assignments"].append(
                {
                    "source_family": family,
                    "source_id": source_id(family, row),
                    "status": "ASSIGNED",
                    "productions": [identity],
                    "rule": "accepted-v1",
                    "assignment_id": assignment,
                    "zone": "ZF",
                }
            )
    return data


def bundle():
    return {
        "schema_id": "quality-catalog-bundle-v1",
        "defect_categories": {"53": "Grave"},
        "mia_labels": {"1": "Inspection defect"},
        "zone_rules": {"1": "ZF"},
        "profiles": [
            {
                "id": "profile",
                "segment_id": "wine",
                "groups": [
                    {"id": "oval", "label": "Oval mouth", "normalized_codes": [53]},
                ],
            }
        ],
        "model_segments": [
            {
                "codigo_modelo_botella": "001/100",
                "segmento_marble": "Wine",
                "segmento_calidad": "wine",
                "profile_id": "profile",
                "tipo_cierre": None,
                "estado_correspondencia": "CONFIRMED",
                "estado_dar": "CONFIRMED",
            }
        ],
    }


def accepted(root, data=None, coverage=None):
    data = inputs() if data is None else data
    refs = []
    for name, rows in data.items():
        table = pa.Table.from_pylist(rows)
        # Delta has no null-only storage type; unknown nullable values stay None.
        for i, field in enumerate(table.schema):
            if pa.types.is_null(field.type):
                table = table.set_column(i, field.name, pa.array([None] * len(rows), pa.string()))
        relative = f"silver/{name}"
        write_deltalake(
            root / relative,
            table,
            commit_properties=CommitProperties(custom_metadata={"gold_operation_id": "accepted"}),
        )
        refs.append(reference(root, name, relative, 0, "accepted"))
    promote(
        root,
        refs,
        environment="test",
        plant="AV",
        input_id=None,
        operation_id="accepted",
        generation=1,
        previous=None,
        coverage=coverage
        if coverage is not None
        else [
            {"source": name, "origin": "AV23", "estado": "READY"}
            for name in ("LISTA_DEFECTOS", "LISTA_RECH", "LISTA_AR", "CONT_ZC")
        ],
    )
    return Snapshot.open(root)


@pytest.fixture
def calculation(tmp_path):
    snap = accepted(tmp_path / "input")
    config = read_json(PROJECT / "config/customer_score.json")
    return snap, config


def test_two_workers_publish_the_same_tables_as_one(calculation, monkeypatch):
    snap, config = calculation
    monkeypatch.setenv("GOLD_SCORE_WORKERS", "1")
    single, single_status = build(
        snap, bundle=bundle(), config=config, cutoff=CUTOFF, calculation_time=CUTOFF
    )
    monkeypatch.setenv("GOLD_SCORE_WORKERS", "2")
    statistics: dict = {}
    parallel, parallel_status = build(
        snap,
        bundle=bundle(),
        config=config,
        cutoff=CUTOFF,
        calculation_time=CUTOFF,
        statistics=statistics,
    )
    assert statistics["score_workers"] == 2
    assert parallel_status == single_status
    assert set(parallel) == set(single)
    for name in single:
        assert parallel[name] == single[name]


def test_complete_calculation_publishes_joinable_star_and_full_score(calculation, tmp_path):
    snap, config = calculation
    tables, status = build(
        snap, bundle=bundle(), config=config, cutoff=CUTOFF, calculation_time=CUTOFF
    )
    assert set(tables) == set(CONTRACTS)
    assert len(tables["productions"]) == 4
    assert len(tables["dim_model"]) == 1
    assert len(tables["dim_machine"]) == 1
    scores = [r for r in tables["quality_scores"] if r["tipo_ventana"] == "PRODUCTION"]
    assert len(scores) == 4
    assert all(r["model_key"] == tables["dim_model"][0]["dimension_key"] for r in scores)
    assert all(r["configuration_status"] == "CONFIRMED" for r in scores)
    assert all(r["muestras"] == 3 for r in tables["mia_reference_values"])
    assert all(r["inspeccionadas"] == 1000 for r in tables["mia_window_exposures"])
    assert all(r["estado"] == "READY" for r in status if r["source"] == "quality_scores")
    root = tmp_path / "gold"
    Publisher(root, environment="test", plant="AV").publish(
        snap, tables, config_id="test", cutoff=CUTOFF.isoformat(), coverage=[]
    )
    published = Snapshot.open(root)
    assert published.manifest["contract"] == {"version": CONTRACT_VERSION, "id": contract_id()}
    validate({name: published.rows(name) for name in CONTRACTS}, complete=True)


@pytest.mark.parametrize("dar_state", ["PENDING", "PROVISIONAL"])
def test_missing_and_provisional_dar_are_not_confused_with_approved_profiles(
    calculation, dar_state
):
    snap, config = calculation
    catalogs = bundle()
    catalogs["model_segments"][0]["estado_dar"] = dar_state
    if dar_state == "PENDING":
        catalogs["model_segments"][0]["profile_id"] = None
    tables, status = build(
        snap, bundle=catalogs, config=config, cutoff=CUTOFF, calculation_time=CUTOFF
    )
    assert any(r["reason_code"] == "DAR_SPECIFIC_DEFINITION_PENDING" for r in status)
    if dar_state == "PENDING":
        assert tables["quality_scores"] == []
        assert any("MODEL_SEGMENT_DAR_PROFILE_PENDING" in (r["reason_code"] or "") for r in status)
    else:
        assert tables["quality_scores"]
        assert {r["configuration_status"] for r in tables["quality_scores"]} == {"PROVISIONAL_DAR"}


def test_pipeline_retry_and_new_config_preserve_closed_scores_and_scd(calculation, tmp_path):
    snap, config = calculation
    root = tmp_path / "gold"
    kwargs = dict(bundle=bundle(), config=config, cutoff=CUTOFF, environment="test", plant="AV")
    first = run(snap.root, root, **kwargs)
    assert first["status"] == "PUBLISHED"
    pinned = Snapshot.open(root)
    assert run(snap.root, root, **kwargs)["status"] == "UNCHANGED"
    changed = deepcopy(config)
    changed["weights"]["dar"] += 1
    second = run(snap.root, root, **{**kwargs, "config": changed})
    assert second["status"] == "PUBLISHED"
    current = Snapshot.open(root)
    old_closed = {r["id_resultado"]: r for r in pinned.rows("quality_scores")}
    assert {r["id_resultado"]: r for r in current.rows("quality_scores")} == old_closed
    assert len(current.rows("dim_model")) == 1
    revised = bundle()
    revised["model_segments"][0]["segmento_marble"] = "Corrected classification"
    assert run(snap.root, root, **{**kwargs, "bundle": revised})["status"] == "PUBLISHED"
    new = Snapshot.open(root, require_gold_contract=True)
    models = new.rows("dim_model")
    assert len(models) == 2
    old_key = current.rows("dim_model")[0]["dimension_key"]
    assert not next(r for r in models if r["dimension_key"] == old_key)["is_current"]
    assert all(r["model_key"] == old_key for r in new.rows("quality_scores"))
    assert all(r["valid_from"] is None for r in models)  # No invented business-effective date.
    forced = run(
        snap.root,
        root,
        **{
            **kwargs,
            "config": changed,
            "bundle": revised,
            "recalculate_closed_ids": {"production-3"},
            "recalculation_reason": "Approved calibration",
        },
    )
    assert forced["incremental"]["mia_windows_reused"] > 0
    forced_snap = Snapshot.open(root)
    components = forced_snap.rows("quality_score_components")
    own = {
        r["id_resultado"]
        for r in forced_snap.rows("quality_scores")
        if r["id_produccion"] == "production-3"
    }
    assert {
        r["peso"] for r in components if r["id_resultado"] in own and r["componente"] == "DAR"
    } == {changed["weights"]["dar"]}
    assert all(
        r["basis_publication_id"] == forced_snap.publication_id
        for r in forced_snap.rows("quality_scores")
        if r["id_produccion"] == "production-3"
    )
    with pytest.raises(IntegrityError, match="reason"):
        run(snap.root, root, **{**kwargs, "recalculate_closed_ids": {"production-3"}})


def test_uncovered_inputs_and_resource_budget_never_produce_complete_scores(calculation):
    snap, config = calculation
    uncovered = Snapshot(snap.root, {**snap.manifest, "coverage": []})
    tables, status = build(
        uncovered, bundle=bundle(), config=config, cutoff=CUTOFF, calculation_time=CUTOFF
    )
    assert tables["quality_scores"] == []
    blocked = [r for r in status if r["source"] == "coverage"]
    assert len(blocked) == 4
    assert all(r["estado"] == "WAITING_DEPENDENCY" and r["period_end"] is None for r in blocked)
    assert blocked[0]["reason_code"] == "COVERAGE:CONT_ZC,LISTA_AR,LISTA_DEFECTOS,LISTA_RECH"
    with pytest.raises(IntegrityError, match="budget"):
        build(
            snap,
            bundle=bundle(),
            config=config,
            cutoff=CUTOFF,
            calculation_time=CUTOFF,
            max_total_rows=1,
        )
    # A single Silver table may exceed the old per-source cap; the total is the guard.
    build(
        snap,
        bundle=bundle(),
        config=config,
        cutoff=CUTOFF,
        calculation_time=CUTOFF,
        max_rows_per_source=1,
        max_total_rows=500_000,
    )


def covered(snap, until):
    rows = [
        {
            "source": name,
            "origin": "AV23",
            "estado": "READY",
            "covered_from": START - timedelta(hours=1),
            "covered_until": until,
        }
        for name in ("LISTA_DEFECTOS", "LISTA_RECH", "LISTA_AR", "CONT_ZC")
    ]
    if until is None:
        rows = [r for r in rows if r["source"] != "CONT_ZC"]
    return Snapshot(snap.root, {**snap.manifest, "coverage": rows})


def test_each_production_is_calculated_as_far_as_its_sources_accredit(calculation, tmp_path):
    snap, config = calculation
    root = tmp_path / "gold"
    publisher = Publisher(root, environment="test", plant="AV")

    def publish(until, previous):
        tables, status = build(
            covered(snap, until),
            bundle=bundle(),
            config=config,
            cutoff=CUTOFF,
            calculation_time=CUTOFF,
            previous=previous,
        )
        # A real producer gives each coverage change its own Silver publication id.
        publisher.publish(
            covered(snap, until),
            tables,
            config_id=f"silver-{until}",
            cutoff=CUTOFF.isoformat(),
            coverage=[],
        )
        by_production = {r["id_produccion"]: r for r in status if r["source"] == "coverage"}
        windows = [
            r["period_end"]
            for r in status
            if r["source"] == "quality_scores" and r["id_produccion"] == "production-3"
        ]
        return by_production, max(windows)

    half_past_twelve = START + timedelta(hours=6, minutes=34)
    status, corte = publish(half_past_twelve, None)
    assert status["production-2"]["estado"] == "READY"
    assert status["production-3"]["estado"] == "WAITING_DEPENDENCY"
    assert status["production-3"]["reason_code"] == "COVERAGE:INTERVAL"
    assert status["production-3"]["period_end"] == START + timedelta(hours=6, minutes=30)
    assert corte == START + timedelta(hours=6, minutes=30)

    status, corte = publish(None, Snapshot.open(root))
    assert status["production-3"]["reason_code"] == "COVERAGE:CONT_ZC"
    assert status["production-3"]["period_end"] == START + timedelta(hours=6, minutes=30)
    assert corte == START + timedelta(hours=6, minutes=30)

    status, corte = publish(CUTOFF, Snapshot.open(root))
    assert status["production-3"]["estado"] == "READY"
    assert status["production-3"]["period_end"] == START + timedelta(hours=7)
    assert corte == START + timedelta(hours=7)


@pytest.mark.parametrize(
    "fault", ["assignment", "window", "grain", "component", "total", "normalized", "orphan"]
)
def test_invalid_cross_table_contracts_cannot_be_published(calculation, tmp_path, fault):
    snap, config = calculation
    tables, _ = build(snap, bundle=bundle(), config=config, cutoff=CUTOFF, calculation_time=CUTOFF)
    if fault == "assignment":
        tables["mia_inspections"][0]["id_asignacion"] = "assignment-3"
    elif fault == "window":
        tables["quality_scores"][0]["corte"] = CUTOFF + timedelta(days=1)
    elif fault == "grain":
        tables["quality_scores"].append(
            {**tables["quality_scores"][0], "id_resultado": "duplicate"}
        )
    elif fault == "component":
        tables["quality_score_components"].pop()
    elif fault == "total":
        tables["quality_scores"][0]["total_penalty"] += 1
    elif fault == "normalized":
        tables["quality_scores"][0]["normalized_score"] += 1
    else:
        tables["aql_score_details"][0]["id_resultado"] = "missing"
    root = tmp_path / "invalid-output"
    with pytest.raises(IntegrityError):
        Publisher(root, environment="test", plant="AV").publish(
            snap, tables, config_id="test", cutoff=CUTOFF.isoformat(), coverage=[]
        )
    assert not (root / "tables").exists()
    assert not (root / "current.json").exists()


def test_cli_build_inspect_validate_and_offline_watch_guard(calculation, tmp_path, capsys):
    snap, config = calculation
    atomic_json(tmp_path / "catalogs.json", bundle())
    atomic_json(tmp_path / "score.json", config)
    settings = {
        "schema_id": "gold-settings-v1",
        "input_root": str(snap.root),
        "output_root": "output",
        "environment": "test",
        "plant": "AV",
        "score_config": "score.json",
        "catalog_bundle": "catalogs.json",
        "poll_seconds": 30,
        "max_rows_per_source": 1000,
        "max_total_rows": 10000,
    }
    settings_path = tmp_path / "settings.json"
    atomic_json(settings_path, settings)
    args = ["build", "--settings", str(settings_path), "--cutoff", CUTOFF.isoformat()]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PUBLISHED"
    root = tmp_path / "output"
    assert main(["inspect", "--root", str(root)]) == 0
    assert len(json.loads(capsys.readouterr().out)["tables"]) == len(CONTRACTS)
    assert main(["validate-gold", "--root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "VALID"
    assert main(["contracts"]) == 0
    assert len(json.loads(capsys.readouterr().out)["tables"]) == len(CONTRACTS)
    assert main(["watch", "--settings", str(settings_path)]) == EXIT_DATA
    assert "continuous" in capsys.readouterr().err
    assert (
        main(["publish", "--settings", str(settings_path), "--at", CUTOFF.isoformat()]) == EXIT_DATA
    )
    assert "continuous" in capsys.readouterr().err
    assert main(["validate-gold", "--root", str(root), "--max-rows", "0"]) == EXIT_DATA
    assert "budget" in capsys.readouterr().err
    assert main([*args[:-1], CUTOFF.isoformat() + "+01:00"]) == EXIT_DATA
    assert "industrial cutoff" in capsys.readouterr().err
    for changes in (
        {"poll_seconds": 0},
        {"max_total_rows": 0},
        {"max_total_rows": 50_000_001},
        {"schema_id": "unknown"},
    ):
        atomic_json(settings_path, {**settings, **changes})
        assert main(args) == EXIT_DATA
        failure = json.loads(capsys.readouterr().err)
        assert failure["status"] == "FAILED" and failure["retryable"] is False
