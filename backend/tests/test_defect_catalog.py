from __future__ import annotations

from pathlib import Path

import polars as pl
from openpyxl import Workbook

from app.infrastructure.repositories.defect_catalog import ExcelDefectCatalog


def test_severity_new_code_label_overrides_no_usar_from_alerts(tmp_path: Path) -> None:
    cod_def_path = tmp_path / "COD_DEF.xlsx"
    severity_path = tmp_path / "Tabla defectos con criticidades.xlsx"
    alerts_root = tmp_path / "avisos_rechazos_parquet"
    alerts_machine = alerts_root / "AV32"
    alerts_machine.mkdir(parents=True)

    _write_workbook(
        cod_def_path,
        (
            ("code", "description"),
            (402, "NO USAR"),
        ),
    )
    _write_workbook(
        severity_path,
        (
            ("old", "old_desc", "new", "new_desc", "", "", "", "", "", "", "severity"),
            ("old", "old_desc", "new", "new_desc", "", "", "", "", "", "", "severity"),
            (
                20,
                "Cuerpo extrano antiguo",
                402,
                "Cuerpo extrano o grasa en el interior",
                "",
                "",
                "",
                "",
                "",
                "",
                "Critico",
            ),
        ),
    )
    pl.DataFrame(
        {
            "defect_code": [402],
            "defect_description": ["NO USAR"],
        }
    ).write_parquet(alerts_machine / "2026-01-30.parquet")

    catalog = ExcelDefectCatalog(
        cod_def_path,
        severity_path=severity_path,
        alerts_root=alerts_root,
    )

    assert catalog.mia_labels()[402] == "NO USAR"
    assert catalog.alert_rejection_labels()[402] == "Cuerpo extrano o grasa en el interior"
    assert catalog.labels()[402] == "Cuerpo extrano o grasa en el interior"


def test_mia_labels_do_not_get_overridden_by_alert_or_severity_labels(tmp_path: Path) -> None:
    cod_def_path = tmp_path / "COD_DEF.xlsx"
    severity_path = tmp_path / "Tabla defectos con criticidades.xlsx"
    alerts_root = tmp_path / "avisos_rechazos_parquet"
    alerts_machine = alerts_root / "AV32"
    alerts_machine.mkdir(parents=True)

    _write_workbook(
        cod_def_path,
        (
            ("code", "description"),
            (1, "Planarita Meccanica"),
        ),
    )
    _write_workbook(
        severity_path,
        (
            ("old", "old_desc", "new", "new_desc", "", "", "", "", "", "", "severity"),
            ("old", "old_desc", "new", "new_desc", "", "", "", "", "", "", "severity"),
            (30, "Rebaba antigua", 1, "Rebaba en boca PS", "", "", "", "", "", "", "Grave"),
        ),
    )
    pl.DataFrame(
        {
            "defect_code": [1],
            "defect_description": ["Rebaba desde aviso"],
        }
    ).write_parquet(alerts_machine / "2026-01-30.parquet")

    catalog = ExcelDefectCatalog(
        cod_def_path,
        severity_path=severity_path,
        alerts_root=alerts_root,
    )

    assert catalog.mia_labels()[1] == "Planarita Meccanica"
    assert catalog.alert_rejection_labels()[1] == "Rebaba en boca PS"


def _write_workbook(path: Path, rows: tuple[tuple[object, ...], ...]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
