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
