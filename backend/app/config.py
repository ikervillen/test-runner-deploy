from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    host: str = "127.0.0.1"
    port: int = 8001
    data_root: Path
    data_backend: Literal["legacy", "gold"] = "legacy"
    gold_root: Path | None = None
    gold_history_root: Path | None = None
    gold_query_max_rows: int = Field(default=250_000, ge=1, le=1_000_000)
    gold_query_total_rows: int = Field(default=500_000, ge=1, le=2_000_000)
    log_level: str = "INFO"
    max_productions_per_query: int = Field(default=50, ge=1, le=500)
    score_cache_size: int = Field(default=3, ge=0, le=32)
    score_query_max_concurrency: int = Field(default=2, ge=1, le=16)
    score_query_queue_timeout_seconds: float = Field(default=1.0, gt=0, le=60)
    score_query_timeout_seconds: float = Field(default=180.0, gt=0, le=900)
    auth_mode: Literal["disabled", "trusted_proxy"] = "disabled"
    auth_trusted_user_header: str = "X-Forwarded-User"
    auth_trusted_roles_header: str = "X-Forwarded-Roles"
    auth_viewer_role: str = "quality_viewer"
    auth_manager_role: str = "quality_manager"
    auth_metrics_role: str = "quality_admin"
    four_hour_offset_hours: int = Field(default=0, ge=0, le=23)
    alerts_rejections_root: Path | None = None
    hot_end_yield_path: Path | None = None
    defect_severity_path: Path | None = None
    alert_defect_normalization_path: Path | None = None
    defect_catalog_path: Path | None = None
    customer_score_config_path: Path | None = None
    segment_dar_profiles_path: Path | None = None
    inspected_totals_root: Path | None = None
    quality_aggregate_root: Path | None = None

    @model_validator(mode="after")
    def independent_gold_stores(self) -> Settings:
        if self.gold_history_root is not None:
            live = self.resolved_gold_root
            history = self.gold_history_root.expanduser().resolve()
            if (
                live.is_relative_to(history)
                or history.is_relative_to(live)
                or (live.exists() and history.exists() and live.samefile(history))
            ):
                raise ValueError("Live and historical Gold stores must be physically independent")
            self.gold_history_root = history
        return self

    @field_validator("data_root")
    @classmethod
    def resolve_data_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @property
    def manifest_path(self) -> Path:
        return self.data_root / "manifest.csv"

    @property
    def resolved_alerts_rejections_root(self) -> Path:
        configured = self.alerts_rejections_root
        return (
            configured.expanduser().resolve()
            if configured is not None
            else (self.data_root.parent / "avisos_rechazos_parquet").resolve()
        )

    @property
    def resolved_inspected_totals_root(self) -> Path:
        configured = self.inspected_totals_root
        return (
            configured.expanduser().resolve()
            if configured is not None
            else (self.data_root.parent / "inspeccionadas_totales_parquet").resolve()
        )

    @property
    def resolved_hot_end_yield_path(self) -> Path:
        configured = self.hot_end_yield_path
        return (
            configured.expanduser().resolve()
            if configured is not None
            else (self.data_root.parent / "Rendimiento_ZC.xlsx").resolve()
        )

    @property
    def resolved_defect_severity_path(self) -> Path:
        configured = self.defect_severity_path
        return (
            configured.expanduser().resolve()
            if configured is not None
            else (self.data_root.parent / "Tabla defectos con criticidades.xlsx").resolve()
        )

    @property
    def resolved_alert_defect_normalization_path(self) -> Path:
        configured = self.alert_defect_normalization_path
        return (
            configured.expanduser().resolve()
            if configured is not None
            else (self.data_root.parent / "Programación defectos MDL_enviado V01.xlsx").resolve()
        )

    @property
    def resolved_defect_catalog_path(self) -> Path:
        configured = self.defect_catalog_path
        return (
            configured.expanduser().resolve()
            if configured is not None
            else (self.data_root.parent / "COD_DEF.xlsx").resolve()
        )

    @property
    def resolved_customer_score_config_path(self) -> Path:
        configured = self.customer_score_config_path
        return (
            configured.expanduser().resolve()
            if configured is not None
            else (Path(__file__).resolve().parents[2] / "config" / "customer_score.json")
        )

    @property
    def resolved_segment_dar_profiles_path(self) -> Path:
        configured = self.segment_dar_profiles_path
        return (
            configured.expanduser().resolve()
            if configured is not None
            else (Path(__file__).resolve().parents[2] / "config" / "segment_dar_profiles.json")
        )

    @property
    def resolved_quality_aggregate_root(self) -> Path | None:
        return (
            self.quality_aggregate_root.expanduser().resolve()
            if self.quality_aggregate_root is not None
            else None
        )

    def required_paths(self) -> dict[str, Path]:
        if self.data_backend == "gold":
            return {"publicacion Gold": self.resolved_gold_root / "current.json"}
        return {
            "directorio Parquet MIA": self.data_root,
            "manifest de producciones": self.manifest_path,
            "directorio Parquet avisos y rechazos": self.resolved_alerts_rejections_root,
            "directorio Parquet de inspeccionadas": self.resolved_inspected_totals_root,
            "archivo rendimiento ZC": self.resolved_hot_end_yield_path,
            "tabla de criticidades": self.resolved_defect_severity_path,
            "tabla de normalizacion MDL": self.resolved_alert_defect_normalization_path,
            "catalogo MIA": self.resolved_defect_catalog_path,
            "configuracion del score": self.resolved_customer_score_config_path,
            "catalogo de perfiles DAR": self.resolved_segment_dar_profiles_path,
        }

    @property
    def resolved_gold_root(self) -> Path:
        return (self.gold_root or self.data_root).expanduser().resolve()

    def data_version_token(self) -> tuple[tuple[str, int], ...]:
        paths = list(self.required_paths().values())
        aggregate_root = self.resolved_quality_aggregate_root
        if aggregate_root is not None:
            paths.append(aggregate_root / "current.json")
        return tuple(
            (str(path), path.stat().st_mtime_ns if path.exists() else -1) for path in paths
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
