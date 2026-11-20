from openpyxl import Workbook

from app.gold.catalogs import categories


def test_new_fenix_severity_uses_mdl_then_fallback_and_flags_internal_conflict(tmp_path):
    def book(name, initial, severity_col, rows):
        path = tmp_path / name
        workbook = Workbook()
        sheet = workbook.active
        for index, (code, severity) in enumerate(rows, start=initial):
            sheet.cell(index, 3, code)
            sheet.cell(index, severity_col, severity)
        workbook.save(path)
        return path

    mdl = book(
        "mdl.xlsx",
        4,
        2,
        [
            (13, "Grave"),
            (39, "Mayor"),
            (42, "Grave"),
            (42, "Leve"),
            (51, "Sin clasificar"),
        ],
    )
    secondary = book(
        "secondary.xlsx",
        3,
        11,
        [
            (13, "Mayor"),
            (39, "Leve"),
            (42, "Menor"),
            (51, "Crítico"),
            (60, "Leve"),
        ],
    )
    assert categories(mdl, secondary) == {
        "13": "Grave",
        "39": "Mayor",
        "42": None,
        "51": "Critico",
        "60": "Leve",
    }


def test_gold_start_applies_the_versioned_mdl_decisions(tmp_path):
    import json

    from app.gold.catalogs import load_independent_bundle

    path = tmp_path / "catalogs.json"
    path.write_text(
        json.dumps(
            {
                "schema_id": "quality-catalog-bundle-v1",
                "defect_categories": {"13": None, "39": "Leve", "999": "Menor"},
                "model_segments": [{"modelo": "example"}],
            }
        )
    )
    result = load_independent_bundle(path)
    assert result["defect_categories"] == {
        "13": "Grave",
        "39": "Mayor",
        "130": "Grave",
        "311": "Grave",
        "999": "Menor",
    }
    assert result["model_segments"] == [{"modelo": "example"}]
    assert json.loads(path.read_text())["defect_categories"]["39"] == "Leve"


def test_live_receipt_does_not_skip_a_changed_catalog(tmp_path):
    from types import SimpleNamespace

    from app.gold.common import atomic_json
    from app.gold.pipeline import _live_caught_up, _remember_live

    atomic_json(tmp_path / "current.json", {"publication_id": "gold"})
    _remember_live(tmp_path, "silver", "gold", effective_config_id="old")
    source = SimpleNamespace(publication_id="silver", manifest={"coverage": []})
    assert _live_caught_up(tmp_path, tmp_path, source, effective_config_id="new") is None
