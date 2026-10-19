from pathlib import Path

import pytest

from ingesta_delta_lake.config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    entry = tmp_path / "incoming"
    (entry / "AV21").mkdir(parents=True)
    return Config(
        plant="AIALA",
        input_root=entry.resolve(),
        bronze_root=(tmp_path / "bronze").resolve(),
        silver_root=(tmp_path / "silver").resolve(),
        control_root=(tmp_path / "control").resolve(),
        sources=(("AV21", (entry / "AV21").resolve()),),
        stable_wait_seconds=0.01,
    )


@pytest.fixture
def source(config: Config) -> Path:
    path = config.input_root / "AV21" / "SIP_MODELO.csv"
    path.write_text("754;3081/360;1;\n", encoding="utf-8")
    return path
