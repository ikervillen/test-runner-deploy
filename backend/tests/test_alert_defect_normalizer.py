from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.infrastructure.repositories.alert_defect_normalizer import ExcelAlertDefectNormalizer


def test_normalizes_a_legacy_vizrala_code_to_the_common_mdl_defect(tmp_path: Path) -> None:
    path = tmp_path / "Programacion defectos MDL.xlsx"
    _write_mdl_catalog(
        path,
        (
            ("General", "Leve", 220, "Claro", 413, "Claro", "Claro"),
            ("General", "Menor", 413, "Botellas baja", 999, "Botella baja", "Botella baja"),
        ),
    )
    normalizer = ExcelAlertDefectNormalizer(path)

    normalized = normalizer.normalize(413, "Claro")

    assert normalized.source_code == 413
    assert normalized.code == 220
    assert normalized.description == "Claro"
    assert normalized.severity == "Leve"
    assert normalized.zone == "General"
    assert normalized.resolution == "normalized_legacy"


def test_keeps_an_ambiguous_legacy_split_unresolved_but_preserves_common_severity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "Programacion defectos MDL.xlsx"
    _write_mdl_catalog(
        path,
        (
            ("Boca", "Critico", 1, "Rebaba en boca PS", 30, "Rebaba antigua", "Rebaba PS"),
            ("Boca", "Critico", 4, "Rebaba en boca SS", 30, "Rebaba antigua", "Rebaba SS"),
        ),
    )
    normalizer = ExcelAlertDefectNormalizer(path)

    normalized = normalizer.normalize(30, "Rebaba antigua")

    assert normalized.code is None
    assert normalized.description == "Rebaba antigua"
    assert normalized.severity == "Critico"
    assert normalized.zone == "Boca"
    assert normalized.resolution == "ambiguous_legacy_mapping"


def test_does_not_reinterpret_an_unmapped_legacy_code_as_a_new_code(tmp_path: Path) -> None:
    path = tmp_path / "Programacion defectos MDL.xlsx"
    _write_mdl_catalog(
        path,
        (("General", "Critico", 402, "Cuerpo extrano interior", 20, "Cuerpo extrano", "C.extra"),),
    )
    normalizer = ExcelAlertDefectNormalizer(path)

    normalized = normalizer.normalize(402, "NO USAR")

    assert normalized.code is None
    assert normalized.description == "NO USAR"
    assert normalized.severity is None
    assert normalized.resolution == "unmapped_legacy_code"


def _write_mdl_catalog(path: Path, rows: tuple[tuple[object, ...], ...]) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append((None,) * 20)
    worksheet.append((None,) * 20)
    worksheet.append(
        (
            "ZONA",
            "CRITICIDAD",
            "NUEVO",
            "NUEVA DESCRIPCION",
            "VIEJO",
            "DESCRIPCION ANTERIOR",
            "DESCRIPCION FENIX (MAX. 20 CARACTERES)",
        )
    )
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
