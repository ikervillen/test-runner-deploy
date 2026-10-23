"""Retire redundant MIA snapshots only after proving their recovery copy exists.

Each completed batch already retains the complete inspection-group document.
The current query index remains untouched. Historical materialized group rows
can therefore be omitted from an offline replacement after exact verification.
"""

import hashlib

import pyarrow as pa

from .common import IntegrityError, canonical
from .retention import payload


def feed(hasher, group_id, document):
    hasher.update(canonical([group_id, document]).encode())
    hasher.update(b"\n")


class InspectionArchive:
    def __init__(self, source, config):
        self.expected, self.actual = {}, {}
        if config is None or config.family not in {"LISTA_RECH", "LISTA_DEFECTOS"}:
            return
        if not source.execute("SELECT 1 FROM inspection_groups LIMIT 1").fetchone():
            return
        settings = dict(source.execute("SELECT key,value FROM settings"))
        if source.execute(
            "SELECT 1 FROM batches WHERE state IN ('PREPARED','WRITING','UNKNOWN') LIMIT 1"
        ).fetchone():
            return  # Never discard a recovery dependency of an unfinished batch.
        if settings.get("inspection_index_version") != settings.get("last_version"):
            raise IntegrityError("Inspection retention requires the confirmed current index")
        from .association_storage import STORES

        table = STORES[config.family](config, settings["control_id"]).table()
        count = sum(pa.table(table.get_add_actions())["num_records"].to_pylist()) if table else 0
        indexed = source.execute(
            "SELECT coalesce(sum(row_count),0) FROM inspection_current"
        ).fetchone()[0]
        if (
            table is None
            or str(table.version()) != settings.get("last_version")
            or count != indexed
        ):
            raise IntegrityError(
                "Inspection current index does not cover the confirmed Delta table"
            )
        # Only load one batch at a time. Retain small fingerprints, never all
        # batch payloads or all historical inspection groups in Python memory.
        for batch_id, raw in source.execute(
            "SELECT batch_id,prepared_json FROM batches WHERE confirmed_at IS NOT NULL "
            "AND state IN ('COMPLETED','PARTIAL') ORDER BY rowid"
        ):
            document = payload(raw)
            groups = document.get("inspection_groups")
            if not isinstance(groups, list):
                continue
            fingerprint = hashlib.sha256()
            for group in groups:
                feed(fingerprint, group["group_id"], canonical(group))
            self.expected[batch_id] = (len(groups), fingerprint.hexdigest())
        self.coverage = {"delta_version": table.version(), "published_rows": count}

    def omit(self, row):
        batch_id, group_id, document = row
        if batch_id not in self.expected:
            return False
        count, hasher = self.actual.setdefault(batch_id, [0, hashlib.sha256()])
        feed(hasher, group_id, document)
        self.actual[batch_id][0] = count + 1
        return True

    def verify(self):
        evidence = {}
        for batch_id, (count, hasher) in self.actual.items():
            observed = count, hasher.hexdigest()
            if observed != self.expected[batch_id]:
                raise IntegrityError(
                    "Inspection audit differs from its retained batch: " + batch_id
                )
            evidence[batch_id] = {"rows": count, "sha256": observed[1]}
        return {
            "policy": "inspection-groups-in-confirmed-payload-v1",
            "archived_rows": sum(v["rows"] for v in evidence.values()),
            "batches": evidence,
            "coverage": getattr(self, "coverage", None),
        }
