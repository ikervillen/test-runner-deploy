import json
from copy import deepcopy

import pytest
from openpyxl import Workbook

from app.gold.common import IntegrityError, read_json
from app.gold.segments import import_workbook, save_import


@pytest.fixture
def classification_book(tmp_path):
    path = tmp_path / "Segmentos.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["Código", "Descripción", "Familia", "Estado"])
    for row in [
        ["001/435", "A", "Vino Ligero", "Vigente"],
        ["002/487", "B", "Tarro", "En preparación"],
        ["003/487", "C", "Tarros", "Vigente"],
        ["004/487", "D", "Licores", "Obsoleto"],
    ]:
        sheet.append(row)
    book.save(path)
    book.close()
    return path


@pytest.fixture
def bundle():
    return {
        "schema_id": "quality-catalog-bundle-v1",
        "model_segments": [],
        "profiles": [
            {
                "id": "existing",
                "segment_id": "old",
                "groups": [{"id": "oval", "label": "Oval", "normalized_codes": [53, 221]}],
            }
        ],
        "mia_labels": {},
        "defect_categories": {},
        "zone_rules": {},
    }


def test_only_current_and_preparation_models_preserve_exact_families(classification_book, bundle):
    result, report = import_workbook(classification_book, base_bundle=bundle)
    assert report["models"] == 3
    assert {r["codigo_modelo_botella"] for r in result["model_segments"]} == {
        "001/435",
        "002/487",
        "003/487",
    }
    assert {r["segmento_calidad"] for r in result["segment_catalog"]} == {
        "vino_ligero",
        "tarro",
        "tarros",
    }
    assert all(
        r["estado_dar"] == "PENDING" and r["profile_id"] is None for r in result["model_segments"]
    )
    assert report["dar_definition_status"] == "PENDING_PER_SEGMENT"
    assert bundle["model_segments"] == []


def test_common_dar_is_explicit_identical_and_always_provisional(classification_book, bundle):
    expected = deepcopy(bundle["profiles"][0]["groups"])
    result, report = import_workbook(
        classification_book, base_bundle=bundle, common_profile_id="existing"
    )
    assert len(result["profiles"]) == 3
    assert all(p["groups"] == expected for p in result["profiles"])
    assert all(r["estado_dar"] == "PROVISIONAL" for r in result["model_segments"])
    assert {r["profile_id"] for r in result["model_segments"]} == {
        p["id"] for p in result["profiles"]
    }
    assert report["provisional_common_dar"]
    with pytest.raises(IntegrityError, match="does not exist"):
        import_workbook(classification_book, base_bundle=bundle, common_profile_id="invented")


def test_import_preserves_source_and_archive_is_content_addressed(
    classification_book, bundle, tmp_path
):
    original = classification_book.read_bytes()
    output = tmp_path / "catalogs.json"
    report = save_import(classification_book, bundle, output)
    assert classification_book.read_bytes() == original
    assert (
        tmp_path / "classification-sources" / (report["source_sha256"] + ".xlsx")
    ).read_bytes() == original
    assert read_json(output)["model_segments"]
    assert save_import(classification_book, bundle, output) == report


@pytest.mark.parametrize("family,state", [(None, "Vigente"), ("Tarro", "Obsoleto")])
def test_invalid_or_empty_accepted_classification_is_rejected(tmp_path, bundle, family, state):
    path = tmp_path / "bad.xlsx"
    book = Workbook()
    book.active.append(["Código", "Familia", "Estado"])
    book.active.append(["A", family, state])
    book.save(path)
    book.close()
    with pytest.raises(IntegrityError):
        import_workbook(path, base_bundle=bundle)


def test_contradictory_model_is_not_selected_by_row_order(classification_book, bundle):
    from openpyxl import load_workbook

    book = load_workbook(classification_book)
    book.active.append(["001/435", "A", "Agua", "Vigente"])
    book.save(classification_book)
    book.close()
    with pytest.raises(IntegrityError, match="Conflicting model"):
        import_workbook(classification_book, base_bundle=bundle)


def test_cli_import_retains_pending_dar_and_output_is_reproducible(
    classification_book, bundle, tmp_path, capsys
):
    from app.gold.cli import main
    from app.gold.common import atomic_json

    source = tmp_path / "source-bundle.json"
    output = tmp_path / "output.json"
    atomic_json(source, bundle)
    assert (
        main(
            [
                "import-segments",
                "--workbook",
                str(classification_book),
                "--bundle",
                str(source),
                "--output",
                str(output),
                "--common-profile",
                "existing",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["models"] == 3
    assert report["dar_definition_status"] == "PENDING_PER_SEGMENT"
    assert {r["estado_dar"] for r in read_json(output)["model_segments"]} == {"PROVISIONAL"}
