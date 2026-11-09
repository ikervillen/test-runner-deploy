from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_data_root_is_required_instead_of_using_a_personal_path() -> None:
    with pytest.raises(ValidationError, match="data_root"):
        Settings()


def test_data_locations_are_resolved_from_explicit_root(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path / "mia")

    assert settings.data_root == (tmp_path / "mia").resolve()
    assert (
        settings.resolved_alerts_rejections_root == (tmp_path / "avisos_rechazos_parquet").resolve()
    )
    assert (
        settings.resolved_inspected_totals_root
        == (tmp_path / "inspeccionadas_totales_parquet").resolve()
    )
