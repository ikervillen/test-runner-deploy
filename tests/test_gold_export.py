from dataclasses import dataclass
from pathlib import Path

from ingesta_delta_lake.gold_export import publish


@dataclass
class Config:
    silver_root: Path
    bronze_root: Path
    control_root: Path


def test_export_is_disabled_without_explicit_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("INGESTA_GOLD_EXPORT", raising=False)
    assert publish(Config(tmp_path / "s", tmp_path / "b", tmp_path / "c"), {}) == {
        "status": "DISABLED"
    }


def test_export_passes_actual_discoveries_and_contains_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("INGESTA_GOLD_EXPORT", "1")
    cfg = Config(tmp_path / "silver", tmp_path / "bronze", tmp_path / "control")
    discovery = {"LISTA_RECH": "2026-11-06T12:00:00+00:00"}

    def exporter(config, root, **kwargs):
        assert config is cfg
        assert root == tmp_path
        assert kwargs == dict(max_ledger_rows=None, source_mode="continuous", discoveries=discovery)
        return {"status": "EXPORTED", "publication_id": "accepted"}

    assert publish(cfg, discovery, exporter=exporter)["publication_id"] == "accepted"

    def failed(*args, **kwargs):
        raise ValueError("retry after accepted source advances")

    assert publish(cfg, discovery, exporter=failed)["status"] == "FAILED"
    assert (tmp_path / "gold-export-status.json").exists()


def test_export_recovers_from_corrupt_status_without_inventing_discoveries(tmp_path, monkeypatch):
    monkeypatch.setenv("INGESTA_GOLD_EXPORT", "1")
    cfg = Config(tmp_path / "silver", tmp_path / "bronze", tmp_path / "control")
    (tmp_path / "gold-export-status.json").write_text("{corrupt", encoding="utf-8")

    def exporter(*args, **kwargs):
        assert kwargs["discoveries"] == {}
        return {"status": "EXPORTED"}

    assert publish(cfg, {}, exporter=exporter)["status"] == "EXPORTED"


def test_export_state_write_failure_does_not_abort_worker(tmp_path, monkeypatch):
    monkeypatch.setenv("INGESTA_GOLD_EXPORT", "1")
    cfg = Config(tmp_path / "silver", tmp_path / "bronze", tmp_path / "control")

    def fail_write(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("ingesta_delta_lake.gold_export.atomic_json", fail_write)
    assert publish(cfg, {}, exporter=lambda *a, **kw: {"status": "EXPORTED"}) == {
        "status": "FAILED",
        "error_type": "OSError",
        "error": "disk unavailable",
    }
