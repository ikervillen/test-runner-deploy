"""Typed, versioned reference projections. Unknown models never get a default profile."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from openpyxl import load_workbook

from app.domain.text import canonical_severity

from .common import IntegrityError, digest, read_json
from .schemas import make_table


def categories(mdl: Path, severity: Path) -> dict[str, str | None]:
    """New Fenix codes: use the NEW code column, never run the legacy normalizer."""
    choices = defaultdict(set)
    for path, start, code_column, severity_column in [(mdl, 4, 2, 1), (severity, 3, 2, 10)]:
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            for row in book.worksheets[0].iter_rows(min_row=start, values_only=True):
                code, category = row[code_column], canonical_severity(row[severity_column])
                if code is not None and category is not None:
                    try:
                        number = int(code)
                    except (TypeError, ValueError):
                        continue
                    choices[str(number)].add(category)
        finally:
            book.close()
    return {
        code: next(iter(values)) if len(values) == 1 else None for code, values in choices.items()
    }


def build(snapshot, bundle: dict) -> tuple[dict[str, list[dict]], list[dict]]:
    """Bundle is a controlled, hash-versioned configuration, not guessed labels."""
    allowed = {
        "schema_id",
        "defect_categories",
        "mia_labels",
        "profiles",
        "model_segments",
        "zone_rules",
    }
    if (
        set(bundle) - {"segment_catalog"} != allowed
        or bundle["schema_id"] != "quality-catalog-bundle-v1"
    ):
        raise IntegrityError("Unsupported or incomplete reference catalog bundle")
    version = digest(bundle)
    common = {"catalog_version": version, "referencia_evidencia": f"catalog-bundle:{version}"}
    tables, pending = {}, []
    tables["segment_catalog"] = [
        {"catalog_version": version, **r} for r in bundle.get("segment_catalog", [])
    ]
    segment_ids = {r["segmento_calidad"] for r in tables["segment_catalog"]}
    defects = []
    for r in snapshot.rows("SIP_DEFECTO"):
        code = str(r["codigo_aviso_rechazo"])
        category = bundle["defect_categories"].get(code)
        defects.append(
            {
                **common,
                "espacio_codigo": "fenix_ar",
                "codigo": code,
                "id_fenix": r["id_defecto_fenix"],
                "descripcion": r["descripcion"],
                "descripcion_corta": r["descripcion_corta"],
                "categoria": category,
            }
        )
    for code, label in bundle["mia_labels"].items():
        defects.append(
            {
                **common,
                "espacio_codigo": "fenix_mia",
                "codigo": code,
                "id_fenix": None,
                "descripcion": label,
                "descripcion_corta": None,
                "categoria": None,
            }
        )
    tables["defect_catalog"] = defects
    tables["ar_status_catalog"] = [{**common, **r} for r in snapshot.rows("SIP_DEFECTO_STATUS")]
    tables["ar_type_catalog"] = []
    for r in snapshot.rows("SIP_DEFECTO_AR"):
        zone = bundle["zone_rules"].get(str(r["codigo_tipo_ar"]))
        tables["ar_type_catalog"].append(
            {
                **common,
                **r,
                "zona_interpretada": zone,
                "regla_zona": "approved-type-zone-v1" if zone else None,
            }
        )
    members = []
    profiles = set()
    for profile in bundle["profiles"]:
        if profile["id"] in profiles:
            raise IntegrityError("Duplicate profile ID")
        profiles.add(profile["id"])
        for group in profile["groups"]:
            for code in group["normalized_codes"]:
                members.append(
                    {
                        **common,
                        "profile_id": profile["id"],
                        "segmento_calidad": profile["segment_id"],
                        "grupo_id": group["id"],
                        "grupo_label": group["label"],
                        "espacio_codigo": "fenix_ar",
                        "codigo": str(code),
                        "aplicabilidad_cierre": group.get("closure_condition"),
                    }
                )
    tables["dar_profile_members"] = members
    tables["model_segment_catalog"] = []
    for r in bundle["model_segments"]:
        if "segment_catalog" in bundle and r["segmento_calidad"] not in segment_ids:
            raise IntegrityError("Model mapping uses an unknown segment")
        if r.get("estado_dar") == "PENDING" and r["profile_id"] is not None:
            raise IntegrityError("Pending DAR must not reference a configured profile")
        if (
            r["estado_correspondencia"] == "CONFIRMED"
            and r.get("estado_dar") != "PENDING"
            and r["profile_id"] not in profiles
        ):
            raise IntegrityError("Confirmed model mapping uses an unknown DAR profile")
        matching = [m for m in members if m["profile_id"] == r["profile_id"]]
        if any(m["segmento_calidad"] != r["segmento_calidad"] for m in matching):
            raise IntegrityError("Model segment and DAR profile disagree")
        tables["model_segment_catalog"].append({**common, **r})
        if r.get("estado_dar") in {"PENDING", "PROVISIONAL"}:
            pending.append(
                {
                    "source": "dar_profile_definition",
                    "estado": "WAITING_DEPENDENCY",
                    "reason_code": "DAR_SPECIFIC_DEFINITION_PENDING",
                }
            )
    if not bundle["model_segments"]:
        pending.append(
            {
                "source": "model_segment_catalog",
                "estado": "WAITING_DEPENDENCY",
                "reason_code": "P01_QUALITY_MAPPING_PENDING",
            }
        )
    for name, rows in tables.items():
        make_table(name, rows)
    return tables, pending


def prepare_bundle(
    reference_root: Path,
    profiles_path: Path,
    *,
    model_segments: list[dict],
    zone_rules: dict[str, str],
    mia_labels: dict[str, str],
) -> dict:
    return {
        "schema_id": "quality-catalog-bundle-v1",
        "defect_categories": categories(
            reference_root / "Programación defectos MDL_enviado V01.xlsx",
            reference_root / "Tabla defectos con criticidades.xlsx",
        ),
        "mia_labels": mia_labels,
        "profiles": read_json(profiles_path)["profiles"],
        "model_segments": model_segments,
        "zone_rules": zone_rules,
    }
