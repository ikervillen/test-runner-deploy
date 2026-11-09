"""Bounded, version-pinned Gold reads. No access to Silver/control databases."""

from __future__ import annotations

from pathlib import Path

import pyarrow.dataset as ds
from deltalake.exceptions import DeltaError

from app.domain.errors import ApplicationError

from .common import IntegrityError, digest, read_json
from .publications import Snapshot


class Query:
    def __init__(
        self,
        root: Path,
        *,
        max_rows=250_000,
        total_rows=500_000,
        publication_id=None,
        scan_parent=None,
    ):
        try:
            self.snapshot = Snapshot.open(
                root, require_gold_contract=True, publication_id=publication_id
            )
        except (OSError, ValueError, DeltaError) as exc:
            raise ApplicationError(
                "GOLD_UNAVAILABLE",
                "La publicación Gold no está disponible o es incompatible.",
                status_code=503,
            ) from exc
        self.max_rows, self.total_rows = max_rows, total_rows
        self.scanned = 0
        self.files: set[str] = set()
        self.cache: dict[tuple, list[dict]] = {}
        self.scan_parent = scan_parent
        self.forks: dict[str, Query] = {}

    def rows(self, name, ids=None, field="id_produccion"):
        key = (name, None if ids is None else tuple(sorted(ids)), field)
        if key not in self.cache:
            if ids is not None and not ids:
                self.cache[key] = []
                return []
            expression = None if ids is None else ds.field(field).isin(list(ids))
            pruning = None if ids is None else [(field, "in", list(ids))]
            try:
                rows = self.snapshot.rows(
                    name,
                    expression=expression,
                    max_rows=self.max_rows,
                    file_pruning_predicate=pruning,
                    on_scan=self.record_scan,
                )
            except IntegrityError as exc:
                raise ApplicationError(
                    "GOLD_QUERY_BUDGET" if "budget" in str(exc) else "GOLD_UNAVAILABLE",
                    "La selección excede el presupuesto o sus datos no son legibles.",
                    status_code=413 if "budget" in str(exc) else 503,
                ) from exc
            self.files.update(
                self.snapshot.table(name).to_pyarrow_dataset(file_pruning_predicate=pruning).files
            )
            self.cache[key] = rows
        return self.cache[key]

    def record_scan(self, rows):
        self.scanned += rows
        if self.scan_parent is not None:
            self.scan_parent.record_scan(rows)
        if self.scanned > self.total_rows:
            raise ApplicationError(
                "GOLD_QUERY_BUDGET",
                "La selección supera el presupuesto de lectura.",
                status_code=413,
            )

    def fork(self, publication_id):
        if not publication_id or publication_id == self.snapshot.publication_id:
            return self
        if publication_id not in self.forks:
            self.forks[publication_id] = Query(
                self.snapshot.root,
                publication_id=publication_id,
                max_rows=self.max_rows,
                total_rows=self.total_rows,
                scan_parent=self,
            )
        return self.forks[publication_id]

    def config(self, identity):
        if (
            not isinstance(identity, str)
            or len(identity) != 64
            or any(c not in "0123456789abcdef" for c in identity)
        ):
            raise IntegrityError("Invalid effective configuration identifier")
        value = read_json(self.snapshot.root / "configs" / f"{identity}.json")
        if (
            value.get("schema_id") != "quality-effective-config-v1"
            or digest([value["score"], value["catalogs"]]) != identity
        ):
            raise IntegrityError("Effective configuration hash mismatch")
        return value

    def select(self, selection, limit):
        ids = tuple(dict.fromkeys(selection.production_ids))
        if len(ids) > limit:
            raise ApplicationError(
                "TOO_MANY_PRODUCTIONS", f"Selecciona como máximo {limit} producciones."
            )
        productions = self.rows("productions", ids)
        if set(ids) != {p["id_produccion"] for p in productions}:
            raise ApplicationError(
                "PRODUCTION_NOT_FOUND",
                "La selección contiene una producción desconocida.",
                status_code=404,
            )
        assignments = self.rows("production_assignments", ids)
        branches = {
            f"{p['linea']}-{a['ramal']}"
            for p in productions
            for a in assignments
            if a["id_produccion"] == p["id_produccion"]
            and a["zona"] == "ZF"
            and a["ramal"] is not None
        }
        if set(selection.branches) - branches:
            raise ApplicationError(
                "INVALID_BRANCH_SELECTION", "Los ramales no pertenecen a la selección."
            )
        return productions
