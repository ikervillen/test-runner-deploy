"""Read-only workbook classification import; segment DAR definitions stay explicit."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import unicodedata
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook

from .common import IntegrityError, atomic_json, digest


def normalize(value):
    return " ".join(
        "".join(
            c
            for c in unicodedata.normalize("NFKD", str(value or ""))
            if not unicodedata.combining(c)
        )
        .casefold()
        .split()
    )


def import_workbook(path: Path, *, base_bundle: dict, common_profile_id: str | None = None):
    """Import exact Código→Familia links for Vigente/En preparación only.

    A shared profile requires an explicit ID. It is copied per segment and marked
    PROVISIONAL, never accepted as the final segment-specific DAR definition.
    """
    content = path.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    workbook = load_workbook(path, read_only=True, data_only=True)
    models: dict[str, dict] = {}
    families: dict[str, str] = {}
    statuses: Counter[str] = Counter()
    try:
        for sheet in workbook:
            records = sheet.iter_rows(values_only=True)
            headers = {
                normalize(value): i
                for i, value in enumerate(next(records, ()))
                if value is not None
            }
            family_column = next(
                (headers[k] for k in ("familia", "familias") if k in headers), None
            )
            if family_column is None or "estado" not in headers or "codigo" not in headers:
                continue
            for number, values in enumerate(records, 2):
                code = str(values[headers["codigo"]] or "").strip()
                if not code:
                    continue
                state = normalize(values[headers["estado"]])
                statuses[state] += 1
                if state not in {"vigente", "en preparacion"}:
                    continue
                label = str(values[family_column] or "").strip()
                if not label:
                    raise IntegrityError(f"Missing family in {sheet.title}!{number}")
                segment = re.sub(r"[^a-z0-9]+", "_", normalize(label)).strip("_")
                if not segment:
                    raise IntegrityError("Empty normalized segment identity")
                if segment in families and families[segment] != label:
                    raise IntegrityError("Different family labels normalize to the same identity")
                families[segment] = label
                row = dict(
                    codigo_modelo_botella=code,
                    segmento_marble=label,
                    segmento_calidad=segment,
                    tipo_cierre=None,
                    estado_correspondencia="CONFIRMED",
                    estado_modelo=str(values[headers["estado"]]).strip(),
                    estado_dar="PENDING",
                    profile_id=None,
                    referencia_clasificacion=f"xlsx-sha256:{sha}:{sheet.title}:row:{number}",
                )
                if code in models and any(
                    models[code][k] != row[k] for k in ("segmento_calidad", "estado_modelo")
                ):
                    raise IntegrityError(f"Conflicting model classification: {code}")
                models[code] = row
    finally:
        workbook.close()
    if not models:
        raise IntegrityError("Workbook contains no accepted Código/Familia/Estado classifications")
    if path.read_bytes() != content:
        raise IntegrityError("Classification workbook changed during import")
    bundle = copy.deepcopy(base_bundle)
    bundle["model_segments"] = [models[k] for k in sorted(models)]
    bundle["segment_catalog"] = [
        dict(segmento_calidad=k, etiqueta=v, source_sha256=sha, dar_status="PENDING")
        for k, v in sorted(families.items())
    ]
    profiles = {p["id"]: p for p in bundle["profiles"]}
    if common_profile_id is not None:
        if common_profile_id not in profiles:
            raise IntegrityError("Explicit common DAR source profile does not exist")
        original = profiles[common_profile_id]
        bundle["profiles"] = []
        for segment, label in sorted(families.items()):
            profile = copy.deepcopy(original)
            profile.update(id=f"provisional_{segment}_v1", segment_id=segment, segment_label=label)
            bundle["profiles"].append(profile)
        for row in bundle["model_segments"]:
            row.update(
                profile_id=f"provisional_{row['segmento_calidad']}_v1", estado_dar="PROVISIONAL"
            )
        for row in bundle["segment_catalog"]:
            row["dar_status"] = "PROVISIONAL"
    report = dict(
        schema_id="quality-segments-import-v1",
        source_file=path.name,
        source_sha256=sha,
        models=len(models),
        families=families,
        source_states=dict(statuses),
        dar_definition_status="PENDING_PER_SEGMENT",
        common_profile_id=common_profile_id,
        provisional_common_dar=common_profile_id is not None,
        bundle_hash=digest(bundle),
    )
    return bundle, report


def save_import(path, base_bundle, output, *, common_profile_id=None):
    bundle, report = import_workbook(
        path, base_bundle=base_bundle, common_profile_id=common_profile_id
    )
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != report["source_sha256"]:
        raise IntegrityError("Classification workbook changed before archival")
    archive = output.parent / "classification-sources" / (report["source_sha256"] + ".xlsx")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists() and archive.read_bytes() != source:
        raise IntegrityError("Classification archive hash collision")
    if not archive.exists():
        with archive.open("xb") as stream:
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
    atomic_json(output, bundle)
    atomic_json(output.with_suffix(".report.json"), report)
    return report
