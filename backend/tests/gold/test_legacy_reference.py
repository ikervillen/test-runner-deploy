import json
from pathlib import Path

from app.gold import legacy
from app.gold.fingerprint import source_fingerprint

WORKBOOKS = (
    "COD_DEF.xlsx",
    "Tabla defectos con criticidades.xlsx",
    "Programación defectos MDL_enviado V01.xlsx",
    "Rendimiento_ZC.xlsx",
)
CONFIGS = ("customer_score.json", "segment_dar_profiles.json")


def test_freeze_records_the_fingerprint_of_the_current_engine(tmp_path: Path, monkeypatch):
    data, config = tmp_path / "data", tmp_path / "config"
    for root, names in ((data, WORKBOOKS), (config, CONFIGS)):
        root.mkdir()
        for name in names:
            (root / name).write_bytes(b"placeholder")
    monkeypatch.setattr(legacy, "evaluate", lambda inputs, production_ids: {})

    result = legacy.freeze(data, config, tmp_path / "reference", ["P1"])

    descriptor = json.loads((tmp_path / "reference/reference.json").read_text(encoding="utf-8"))
    assert result["status"] == "FROZEN"
    assert descriptor["engine_sha256"] == source_fingerprint("app.gold.legacy")
    assert "app/infrastructure/scoring/repository.py" in descriptor["engine_modules"]
