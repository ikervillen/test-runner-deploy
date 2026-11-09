"""Immutable local reference corpus, built from real historical application inputs."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

from app.domain.models import MetricId, TrendFilters, TrendRequest, WindowId
from app.infrastructure.repositories.alert_defect_normalizer import ExcelAlertDefectNormalizer
from app.infrastructure.repositories.catalog import CsvCatalogRepository
from app.infrastructure.repositories.defect_catalog import ExcelDefectCatalog
from app.infrastructure.repositories.segment_dar_profiles import JsonSegmentDarProfileRepository
from app.infrastructure.scoring import PolarsCustomerScoreRepository

from .fingerprint import BACKEND_ROOT, source_closure, source_fingerprint


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def evaluate(root: Path, production_ids: list[str], *, requests: dict | None = None) -> dict:
    """Run the unchanged legacy engine and preserve its intermediate MIA quantities."""
    data = root / "AV-2024-2026_parquet"
    catalog = CsvCatalogRepository(data, data / "manifest.csv")
    all_productions = catalog.list_productions()
    selected = catalog.get_productions(tuple(production_ids))
    normalizer = ExcelAlertDefectNormalizer(root / "Programación defectos MDL_enviado V01.xlsx")
    defects = ExcelDefectCatalog(
        root / "COD_DEF.xlsx",
        severity_path=root / "Tabla defectos con criticidades.xlsx",
        alerts_root=root / "avisos_rechazos_parquet",
        alert_defect_normalizer=normalizer,
    )
    repository = PolarsCustomerScoreRepository(
        root / "customer_score.json",
        root / "Tabla defectos con criticidades.xlsx",
        root / "avisos_rechazos_parquet",
        root / "inspeccionadas_totales_parquet",
        root / "Rendimiento_ZC.xlsx",
        defects.mia_labels(),
        alert_defect_labels=defects.alert_rejection_labels(),
        alert_defect_normalizer=normalizer,
        segment_dar_profiles=JsonSegmentDarProfileRepository(root / "segment_dar_profiles.json"),
        data_root=data,
    )
    cases = {}
    for production in selected:
        import pyarrow.parquet as pq

        inspection_path = (
            root / "inspeccionadas_totales_parquet" / production.line / f"{production.id}.parquet"
        )
        available = pq.read_table(inspection_path, columns=["zf_machine_code"])
        machine = min(set(available.column(0).to_pylist()))
        for suffix, filters in [
            ("all", TrendFilters()),
            ("machine", TrendFilters(machine_codes=(machine,))),
        ]:
            if requests is not None:
                saved = requests[f"{production.id}:{suffix}"]
                filters = TrendFilters(**{k: tuple(v) for k, v in saved["filters"].items()})
            request = TrendRequest(
                (production.id,), (), MetricId.REJECTED_COUNT, WindowId.TEN_MINUTES, filters
            )
            result = asdict(repository.analyze(request, (production,), all_productions))
            scan = repository.mia.production_rates(
                request, (production,), respect_branch_selection=True
            )
            cases[f"{production.id}:{suffix}"] = {
                "request": asdict(request),
                "result": result,
                "mia_bases": [asdict(rate) for rate in scan.rates],
                "base_warnings": list(scan.warnings),
            }
    return cases


def freeze(
    data_root: Path, config_root: Path, destination: Path, production_ids: list[str]
) -> dict:
    """Copy stable source bytes once. Never overwrite an existing reference corpus."""
    source = data_root.resolve(strict=True)
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("Reference destination already exists; choose a new directory")
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Reference and original data must have independent locations")
    destination.mkdir(parents=True)
    inventory = []
    families = ("AV-2024-2026_parquet", "avisos_rechazos_parquet", "inspeccionadas_totales_parquet")
    files = [
        (p, p.relative_to(source))
        for family in families
        for p in sorted((source / family).rglob("*"))
        if p.is_file() and p.suffix.lower() in {".parquet", ".csv"}
    ]
    files += [
        (source / name, Path(name))
        for name in (
            "COD_DEF.xlsx",
            "Tabla defectos con criticidades.xlsx",
            "Programación defectos MDL_enviado V01.xlsx",
            "Rendimiento_ZC.xlsx",
        )
    ]
    files += [
        (config_root / name, Path(name))
        for name in ("customer_score.json", "segment_dar_profiles.json")
    ]
    for original, relative in files:
        before = original.stat()
        target = destination / "inputs" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        checksum = file_hash(target)
        after = original.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"Source changed during snapshot: {original}")
        if checksum != file_hash(original):
            raise ValueError(f"Source bytes changed during snapshot: {original}")
        inventory.append({"path": relative.as_posix(), "sha256": checksum, "bytes": before.st_size})
    inputs = destination / "inputs"
    results = evaluate(inputs, production_ids)
    (destination / "expected.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str, allow_nan=False),
        encoding="utf-8",
    )
    project = Path(__file__).resolve().parents[3]
    commit = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    descriptor = {
        "schema_id": "gold-legacy-reference-v2",
        "app_commit": commit,
        "production_ids": production_ids,
        "files": inventory,
        "engine_sha256": source_fingerprint(__name__),
        "engine_modules": [
            path.relative_to(BACKEND_ROOT).as_posix() for path in source_closure(__name__)
        ],
        "expected_sha256": file_hash(destination / "expected.json"),
        "dependencies": {
            name: importlib.metadata.version(name) for name in ("polars", "openpyxl", "pydantic")
        },
        "notes": [
            "Actual historical inputs; no source data modified",
            "Legacy default DAR profile preserved only for this baseline",
            "No industrial/model equivalence to new Fenix productions inferred",
        ],
    }
    (destination / "reference.json").write_text(
        json.dumps(descriptor, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "status": "FROZEN",
        "cases": len(results),
        "files": len(inventory),
        "bytes": sum(f["bytes"] for f in inventory),
        "path": str(destination),
    }


def verify(destination: Path) -> dict:
    descriptor = json.loads((destination / "reference.json").read_text(encoding="utf-8"))
    for record in descriptor["files"]:
        if file_hash(destination / "inputs" / record["path"]) != record["sha256"]:
            raise ValueError(f"Reference input changed: {record['path']}")
    if file_hash(destination / "expected.json") != descriptor["expected_sha256"]:
        raise ValueError("Reference expected output changed")
    expected = json.loads((destination / "expected.json").read_text(encoding="utf-8"))
    actual = json.loads(
        json.dumps(
            evaluate(
                destination / "inputs",
                descriptor["production_ids"],
                requests={k: v["request"] for k, v in expected.items()},
            ),
            default=str,
            allow_nan=False,
        )
    )

    def comparable(value, key=""):
        if isinstance(value, dict):
            return {k: comparable(v, k) for k, v in value.items()}
        if isinstance(value, list):
            items = [comparable(v) for v in value]
            # Polars group order and equal-contribution ties are not deterministic.
            # Compare each defect by identity; never suppress numerical differences.
            if key in {"mia_details", "mia_bases"}:
                items.sort(key=lambda r: (r.get("production_id", ""), r["defect_code"]))
            return items
        return value

    if comparable(actual) != comparable(expected):
        raise ValueError("Legacy reference changed: compare expected output before proceeding")
    return {"status": "MATCH", "cases": len(actual)}
