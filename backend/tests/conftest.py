from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.config import Settings
from app.factory import create_app


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    production_dir = tmp_path / "AV23_PROD_001_20240213__20240215"
    production_dir.mkdir()
    alerts_dir = tmp_path / "avisos_rechazos_parquet" / "AV23"
    alerts_dir.mkdir(parents=True)
    inspected_dir = tmp_path / "inspeccionadas_totales_parquet" / "AV23"
    inspected_dir.mkdir(parents=True)
    severity_path = tmp_path / "Tabla defectos con criticidades.xlsx"
    normalization_path = tmp_path / "Programacion defectos MDL_enviado V01.xlsx"
    dar_profiles_path = tmp_path / "segment_dar_profiles.json"
    rows = [
        _row("2024-02-13 07:01:00", "AV23-1", 3, 10, 2, 100),
        _row("2024-02-13 07:01:00", "AV23-1", 4, 10, 3, 100),
        _row("2024-02-13 07:11:00", "AV23-1", 3, 10, 4, 120),
        _row("2024-02-13 08:02:00", "AV23-1", 3, 11, 5, 80),
        _row("2024-02-13 07:02:00", "AV23-2", 3, 10, 7, 200),
    ]
    alert_rows = [
        _alert_row("2024-02-13 07:01:00", "2024-02-13 07:06:00", "alert-1", 3, "Ovalizacion"),
        _alert_row("2024-02-13 07:02:00", "2024-02-13 07:07:00", "alert-2", 5, "Defecto menor"),
        _alert_row("2024-02-13 07:11:00", "2024-02-13 07:21:00", "alert-3", 4, "Rechupe interior"),
        _alert_row("2024-02-13 08:02:00", "2024-02-13 08:04:00", "alert-4", 6, "Defecto menor"),
    ]
    frame = pl.DataFrame(rows).with_columns(pl.col("LISTA_DEFECTOS_FECHA_REAL").str.to_datetime())
    pl.DataFrame(alert_rows).with_columns(
        pl.col("event_start").str.to_datetime(),
        pl.col("event_end").str.to_datetime(),
    ).write_parquet(alerts_dir / "2024-02-13.parquet")
    _write_severity_workbook(severity_path)
    _write_mdl_workbook(normalization_path)
    _write_dar_profiles(dar_profiles_path)
    branch_1 = frame.filter(pl.col("LISTA_DEFECTOS_MAQ_IS_RAMAL") == "AV23-1")
    branch_2 = frame.filter(pl.col("LISTA_DEFECTOS_MAQ_IS_RAMAL") == "AV23-2")
    branch_1_path = production_dir / "AV23-1.parquet"
    branch_2_path = production_dir / "AV23-2.parquet"
    branch_1.write_parquet(branch_1_path)
    branch_2.write_parquet(branch_2_path)
    pl.DataFrame(
        [
            {
                "production_id": "AV23_PROD_001",
                "machine": "AV23",
                "machine_branch": "AV23-1",
                "timestamp": "2024-02-13 07:01:00",
                "inspected_total": 900,
                "rejected_total": 11,
                "model_color": "M",
                "cavity": "-",
                "cavity_total": "-",
                "section": "-",
                "zf_machine_code": 10,
            },
            {
                "production_id": "AV23_PROD_001",
                "machine": "AV23",
                "machine_branch": "AV23-1",
                "timestamp": "2024-02-13 07:01:00",
                "inspected_total": 700,
                "rejected_total": 3,
                "model_color": "M",
                "cavity": "-",
                "cavity_total": "-",
                "section": "-",
                "zf_machine_code": 10,
            },
            {
                "production_id": "AV23_PROD_001",
                "machine": "AV23",
                "machine_branch": "AV23-2",
                "timestamp": "2024-02-13 07:02:00",
                "inspected_total": 100,
                "rejected_total": 7,
                "model_color": "M",
                "cavity": "-",
                "cavity_total": "-",
                "section": "-",
                "zf_machine_code": 10,
            },
        ]
    ).with_columns(pl.col("timestamp").str.to_datetime()).write_parquet(
        inspected_dir / "AV23_PROD_001.parquet"
    )

    manifest_path = tmp_path / "manifest.csv"
    fieldnames = [
        "production_id",
        "machine",
        "production_start_day",
        "production_end_day",
        "production_calendar_days",
        "machine_branch",
        "branch_days_with_data",
        "rows",
        "data_start",
        "data_end",
        "file_size_bytes",
        "relative_path",
    ]
    with manifest_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for branch, path, count, start, end in [
            ("AV23-1", branch_1_path, 4, "2024-02-13 07:01:00", "2024-02-13 08:02:00"),
            ("AV23-2", branch_2_path, 1, "2024-02-13 07:02:00", "2024-02-13 07:02:00"),
        ]:
            writer.writerow(
                {
                    "production_id": "AV23_PROD_001",
                    "machine": "AV23",
                    "production_start_day": "2024-02-13",
                    "production_end_day": "2024-02-15",
                    "production_calendar_days": "3",
                    "machine_branch": branch,
                    "branch_days_with_data": "1",
                    "rows": str(count),
                    "data_start": start,
                    "data_end": end,
                    "file_size_bytes": str(path.stat().st_size),
                    "relative_path": path.relative_to(tmp_path).as_posix(),
                }
            )
    return tmp_path


@pytest.fixture
def client(data_root: Path) -> Iterator[TestClient]:
    settings = Settings(
        data_root=data_root,
        alerts_rejections_root=data_root / "avisos_rechazos_parquet",
        inspected_totals_root=data_root / "inspeccionadas_totales_parquet",
        defect_severity_path=data_root / "Tabla defectos con criticidades.xlsx",
        alert_defect_normalization_path=data_root / "Programacion defectos MDL_enviado V01.xlsx",
        segment_dar_profiles_path=data_root / "segment_dar_profiles.json",
        cache_size=4,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def client_with_zc(data_root: Path) -> Iterator[TestClient]:
    _write_zc_workbook(data_root / "Rendimiento_ZC.xlsx")
    settings = Settings(
        data_root=data_root,
        alerts_rejections_root=data_root / "avisos_rechazos_parquet",
        inspected_totals_root=data_root / "inspeccionadas_totales_parquet",
        hot_end_yield_path=data_root / "Rendimiento_ZC.xlsx",
        defect_severity_path=data_root / "Tabla defectos con criticidades.xlsx",
        alert_defect_normalization_path=data_root / "Programacion defectos MDL_enviado V01.xlsx",
        segment_dar_profiles_path=data_root / "segment_dar_profiles.json",
        cache_size=4,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _row(
    timestamp: str,
    branch: str,
    defect: int,
    machine_code: int,
    rejected: int,
    inspected: int,
) -> dict[str, object]:
    return {
        "LISTA_DEFECTOS_MAQ_IS": "AV23",
        "LISTA_DEFECTOS_PLANTA": "AV",
        "LISTA_DEFECTOS_FECHA": timestamp,
        "LISTA_DEFECTOS_FECHA_REAL": timestamp,
        "LISTA_DEFECTOS_MOLDE": 0,
        "LISTA_DEFECTOS_RAMAL": "LV",
        "LISTA_DEFECTOS_MAQ_IS_RAMAL": branch,
        "LISTA_DEFECTOS_COD_MAQ_ZF": machine_code,
        "LISTA_DEFECTOS_COD_DEF": defect,
        "LISTA_DEFECTOS_INSPECC": inspected,
        "LISTA_DEFECTOS_RECH": rejected,
        "LISTA_DEFECTOS_CAVIDAD": "-",
        "LISTA_DEFECTOS_CAVIDAD_TOTAL": "-",
        "LISTA_DEFECTOS_MODELO": "4835/242",
        "LISTA_DEFECTOS_MODELO_COLOR": "M",
        "LISTA_DEFECTOS_MODELO_SIN_COLOR": "4835/242",
        "LISTA_DEFECTOS_SECCION": "-",
        "LISTA_DEFECTOS_DESCRIPCION_CORTA": "AV23",
        "PRODUCCION_ID": "AV23_PROD_001",
        "PRODUCCION_INSTANTE_ID": f"{timestamp}_{branch}",
    }


def _alert_row(
    timestamp: str,
    event_end: str,
    event_id: str,
    defect: int,
    description: str,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "event_type": "Rechazo",
        "line": "AV23",
        "machine": "AV23",
        "event_start": timestamp,
        "event_end": event_end,
        "defect_code": defect,
        "defect_description": description,
        "cavity": "-",
        "color": "M",
        "section": "-",
        "machine_code": 10,
        "source_file": "fixture.xlsx",
    }


def _write_severity_workbook(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["legacy", "", "new", "", "", "", "", "", "", "", "severity"])
    worksheet.append(["legacy", "", "new", "", "", "", "", "", "", "", "severity"])
    worksheet.append([3, "", 3, "", "", "", "", "", "", "", "Critico"])
    worksheet.append([4, "", 4, "", "", "", "", "", "", "", "Critico"])
    worksheet.append([5, "", 5, "", "", "", "", "", "", "", "Menor"])
    worksheet.append([6, "", 6, "", "", "", "", "", "", "", "Menor"])
    worksheet.append([212, "Verticalidad", 414, "Verticalidad", "", "", "", "", "", "", "Mayor"])
    worksheet.append(
        [230, "Cuello torcido", 113, "Cuello torcido", "", "", "", "", "", "", "Mayor"]
    )
    worksheet.append(
        [302, "Perfil interno boca / Rechupe", 115, "Rechupe", "", "", "", "", "", "", "Menor"]
    )
    worksheet.append([307, "Boca ovalada", 53, "Boca ovalada", "", "", "", "", "", "", "Menor"])
    worksheet.append(
        [315, "Botella ovalada", 221, "Botella ovalada", "", "", "", "", "", "", "Menor"]
    )
    worksheet.append(
        [
            110,
            "Line-over en superficie cierre",
            27,
            "Line-over en superficie cierre",
            "",
            "",
            "",
            "",
            "",
            "",
            "Grave",
        ]
    )
    workbook.save(path)


def _write_mdl_workbook(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append([None] * 20)
    worksheet.append([None] * 20)
    worksheet.append(
        [
            "ZONA",
            "CRITICIDAD",
            "NUEVO",
            "NUEVA DESCRIPCION",
            "VIEJO",
            "DESCRIPCION ANTERIOR",
            "DESCRIPCION FENIX (MAX. 20 CARACTERES)",
        ]
    )
    for row in (
        ("General", "CRITICO", 3, "Ovalizacion", 3, "Ovalizacion", "Ovalizacion"),
        ("General", "CRITICO", 4, "Rechupe interior", 4, "Rechupe interior", "Rechupe"),
        ("General", "Menor", 5, "Defecto menor", 5, "Defecto menor", "Defecto menor"),
        ("General", "Menor", 6, "Defecto menor 2", 6, "Defecto menor", "Defecto menor 2"),
        ("General", "Mayor", 414, "Verticalidad", 212, "Verticalidad", "Verticalidad"),
        ("General", "Mayor", 113, "Cuello torcido", 230, "Cuello torcido", "Cuello torcido"),
        ("General", "Menor", 115, "Rechupe", 302, "Perfil interno boca / Rechupe", "Rechupe"),
        ("General", "Menor", 53, "Boca ovalada", 307, "Boca ovalada", "Boca ovalada"),
        ("General", "Menor", 221, "Botella ovalada", 315, "Botella ovalada", "Botella ovalada"),
        (
            "General",
            "Grave",
            27,
            "Line-over en superficie cierre",
            110,
            "Line-over en superficie cierre",
            "Line-over",
        ),
    ):
        worksheet.append(row)
    workbook.save(path)


def _write_zc_workbook(path: Path) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["FECHA", "ID_LINEA_ZC", "RUEDA", "TEORICO_A", "TEORICO_B"])
    worksheet.append([datetime(2024, 2, 13, 7, 30), 14, 90, 50, 50])
    workbook.save(path)


def _write_dar_profiles(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_profile_id": "fixture_vino_ligero_v1",
                "profiles": [
                    {
                        "id": "fixture_vino_ligero_v1",
                        "segment_id": "vino_ligero",
                        "segment_label": "Vino Ligero",
                        "version": 1,
                        "groups": [
                            {
                                "id": "ovalizacion",
                                "label": "Ovalizacion",
                                "normalized_codes": [3],
                            },
                            {
                                "id": "rechupe_45mm",
                                "label": "Corcho: Rechupe int. 45 mm",
                                "normalized_codes": [4],
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
