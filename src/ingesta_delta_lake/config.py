"""Configuración explícita, sin destinos productivos implícitos."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .common import ConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    plant: str
    input_root: Path
    bronze_root: Path
    silver_root: Path
    control_root: Path
    sources: tuple[tuple[str, Path], ...]
    encoding: str = "utf-8-sig"
    stable_wait_seconds: float = 0.2
    capture_attempts: int = 3
    max_file_bytes: int = 64 * 1024 * 1024
    environment: str = "test"
    family: str = "SIP_MODELO"
    timestamp_policy: str | None = None
    pending_review_after_hours: float | None = None
    line_by_origin: tuple[tuple[str, str], ...] = ()
    association_decisions: Path | None = None
    topology_enabled: bool = False
    line_code_by_origin: tuple[tuple[str, int], ...] = ()
    mia_batches_per_turn: int | None = None
    input_scan_interval_seconds: int = 0

    @property
    def contract(self):
        from .contracts import get_contract

        return get_contract(self.family)

    @property
    def table_path(self) -> Path:
        return self.silver_root / self.plant / self.family.lower()

    @property
    def control_path(self) -> Path:
        # Conservar la ubicación de la entrega 01, sin migrar controles ya creados.
        filename = (
            "control.sqlite"
            if self.family == "SIP_MODELO"
            else f"control-{self.family.lower()}.sqlite"
        )
        return self.control_root / filename

    def validate(self) -> None:
        _ = self.contract  # Rechazar familias no implementadas antes de crear destinos.
        if (
            type(self.input_scan_interval_seconds) is not int
            or not 0 <= self.input_scan_interval_seconds <= 86400
        ):
            raise ConfigurationError("input_scan_interval_seconds debe estar entre 0 y 86400")
        if self.mia_batches_per_turn is not None and (
            type(self.mia_batches_per_turn) is not int or not 1 <= self.mia_batches_per_turn <= 100
        ):
            raise ConfigurationError("mia_batches_per_turn debe ser un entero entre 1 y 100")
        if (
            self.family
            in {
                "MP9_CAMBIO_MODELO",
                "CATALOGO_PRODUCCIONES",
                "LISTA_DEFECTOS",
                "LISTA_RECH",
                "LISTA_AR",
                "CONT_ZC",
                "ASIGNACION_PRODUCCION_RAMAL",
                "SIP_MAQUINA_MOLDEO",
                "SIP_LINEA_ZF",
            }
            and self.timestamp_policy != "local_naive_test_v1"
        ):
            raise ConfigurationError(
                "Las fuentes temporales requieren timestamp_policy=local_naive_test_v1 en ensayo"
            )
        if len(dict(self.line_by_origin)) != len(self.line_by_origin) or any(
            not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", value)
            for pair in self.line_by_origin
            for value in pair
        ):
            raise ConfigurationError("Correspondencia origen/línea inválida o duplicada")
        if (
            self.family in {"CATALOGO_PRODUCCIONES", "ASIGNACION_PRODUCCION_RAMAL"}
            and not self.line_by_origin
        ):
            raise ConfigurationError("El catálogo requiere line_by_origin explícito")
        if type(self.topology_enabled) is not bool:
            raise ConfigurationError("topology_enabled debe ser booleano")
        if len(dict(self.line_code_by_origin)) != len(self.line_code_by_origin) or any(
            not isinstance(v, int) or isinstance(v, bool) for _, v in self.line_code_by_origin
        ):
            raise ConfigurationError("Códigos de línea inválidos o duplicados")
        if self.topology_enabled and set(dict(self.line_code_by_origin)) != {
            o for o, _ in self.sources
        }:
            raise ConfigurationError("La topología requiere line_code_by_origin para cada origen")
        if self.association_decisions is not None and (
            not self.association_decisions.is_absolute()
            or self.association_decisions != self.association_decisions.resolve()
            or not self.association_decisions.is_relative_to(self.control_root)
        ):
            raise ConfigurationError(
                "Las decisiones deben estar en una ruta absoluta dentro de control_root"
            )
        if self.pending_review_after_hours is not None and (
            not isinstance(self.pending_review_after_hours, (float, int))
            or not 0 < self.pending_review_after_hours <= 87600
        ):
            raise ConfigurationError("pending_review_after_hours debe ser positivo y finito")
        # La primera entrega se valida en destinos de ensayo; habilitar producción
        # será una capacidad explícita tras validar runtime y almacenamiento.
        if self.environment not in {"test", "development"}:
            raise ConfigurationError("Esta entrega solo admite environment=test/development")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", self.plant):
            raise ConfigurationError("plant debe ser un identificador explícito A-Z/0-9/_/-")
        roots = (self.input_root, self.bronze_root, self.silver_root, self.control_root)
        if any(not p.is_absolute() or p != p.resolve() for p in roots):
            raise ConfigurationError("Las raíces deben ser absolutas y estar resueltas")
        if any(str(p).startswith("\\\\") or p == Path(p.anchor) for p in roots):
            raise ConfigurationError("No se admiten raíces de disco ni UNC/SMB en esta entrega")
        for i, p in enumerate(roots):
            for q in roots[i + 1 :]:
                if p.is_relative_to(q) or q.is_relative_to(p):
                    raise ConfigurationError(
                        "Entrada, Bronze, Silver y control deben estar separados"
                    )
        for p in roots[1:]:
            if p.is_relative_to(PROJECT_ROOT) or PROJECT_ROOT.is_relative_to(p):
                raise ConfigurationError("Los datos operativos no pueden estar dentro del código")
            if p.exists() and not p.is_dir():
                raise ConfigurationError(f"No es un directorio: {p}")
        if not self.input_root.is_dir() or not self.sources:
            raise ConfigurationError("Se necesita una entrada existente y al menos un origen")
        seen = set()
        for origin, path in self.sources:
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", origin):
                raise ConfigurationError(f"Origen inválido: {origin}")
            if not path.is_relative_to(self.input_root) or not path.is_dir():
                raise ConfigurationError(f"Origen fuera de la entrada o inexistente: {origin}")
            if path in seen:
                raise ConfigurationError("Dos orígenes no pueden reutilizar la misma carpeta")
            seen.add(path)
        if not 0.01 <= self.stable_wait_seconds <= 10:
            raise ConfigurationError("stable_wait_seconds debe estar entre 0.01 y 10")
        if not 1 <= self.capture_attempts <= 10 or self.max_file_bytes < 1:
            raise ConfigurationError("Límites de captura inválidos")
        try:
            "".encode(self.encoding)
        except LookupError as exc:
            raise ConfigurationError("Codificación desconocida") from exc


def load_config(path: Path) -> Config:
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
        allowed = set(Config.__dataclass_fields__)
        if set(data) - allowed:
            raise ConfigurationError(f"Opciones desconocidas: {sorted(set(data) - allowed)}")
        roots = {}
        for key in ("input_root", "bronze_root", "silver_root", "control_root"):
            p = Path(data[key])
            if not p.is_absolute():
                raise ConfigurationError(f"{key} debe ser absoluto")
            roots[key] = p.resolve()
        sources = tuple(
            (origin, (roots["input_root"] / folder).resolve())
            for origin, folder in sorted(data["sources"].items())
        )
        if "line_by_origin" in data:
            data["line_by_origin"] = tuple(sorted(data["line_by_origin"].items()))
        if "line_code_by_origin" in data:
            data["line_code_by_origin"] = tuple(sorted(data["line_code_by_origin"].items()))
        if data.get("association_decisions") is not None:
            decision_path = Path(data["association_decisions"])
            if not decision_path.is_absolute():
                raise ConfigurationError("association_decisions debe ser absoluto")
            data["association_decisions"] = decision_path.resolve()
        config = Config(**{**data, **roots, "sources": sources})
        config.validate()
        return config
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ConfigurationError(f"Configuración inválida: {exc}") from exc
