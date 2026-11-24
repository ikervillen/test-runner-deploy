"""Incremental reads of a confirmed live Silver Delta version."""

from collections import defaultdict

import pyarrow as pa
from deltalake.exceptions import DeltaError

from .common import IntegrityError


class FullReadRequired(Exception):
    """A complete scan is needed to preserve the producer's accepted view."""


def references(snapshot):
    return {
        name: {
            "table_id": ref["table_id"],
            "version": ref["version"],
            "rejected_hashes": sorted(ref.get("rejected_hashes", [])),
        }
        for name, ref in snapshot.manifest["tables"].items()
    }


def read_changes(snapshot, family, before, after, columns, budget, *, locate=None):
    if before is None or after is None or before["table_id"] != after["table_id"]:
        raise FullReadRequired
    if (
        before["rejected_hashes"] != after["rejected_hashes"]
        or after["version"] < before["version"]
    ):
        raise FullReadRequired
    if before["version"] == after["version"]:
        return {}, 0
    from ingesta_delta_lake.pipeline_rows import business_key, conflicting_keys, review_hash

    table = snapshot.table(family)
    versions = defaultdict(list)
    count = 0
    try:
        for batch in pa.RecordBatchReader.from_stream(
            table.load_cdf(before["version"] + 1, after["version"])
        ):
            count += batch.num_rows
            if count > budget:
                raise IntegrityError("Live Silver changes exceed their explicit row budget")
            for event in batch.to_pylist():
                kind = event["_change_type"]
                if kind not in {"insert", "update_preimage", "update_postimage"}:
                    # A delete may reflect an archive handover or a withdrawal. Use the
                    # existing retention and reconciliation rules for that version.
                    raise FullReadRequired
                values = {name: event[name] for name in columns}
                if locate is not None:
                    locate(business_key(family, values), values)
                versions[event["_commit_version"]].append((kind, values))
    except (DeltaError, OSError, pa.ArrowException) as exc:
        raise FullReadRequired from exc
    changed = {}
    rejected = set(after["rejected_hashes"])
    for version, events in sorted(versions.items()):
        for kind, row in events:
            if kind == "update_preimage":
                changed[business_key(family, row)] = (None, [])
        accepted = {}
        for kind, row in events:
            if kind == "update_preimage" or review_hash(family, row) in rejected:
                continue
            key = business_key(family, row)
            if key in accepted and accepted[key] != row:
                raise FullReadRequired
            accepted[key] = row
            changed[key] = (
                row,
                [
                    {
                        "source_table": after["table_id"],
                        "source_version": version,
                        "row_hash": review_hash(family, row),
                    }
                ],
            )
        if conflicting_keys(family, accepted):
            raise FullReadRequired
    return changed, count


def materialize(snapshot, previous, prior_proofs, family, after, columns, *, budget):
    from ingesta_delta_lake.pipeline_rows import business_key, conflicting_keys

    before = previous.document["silver_refs"].get(family)
    if before is None and after is None:
        return [], {}, 0
    changed, count = read_changes(snapshot, family, before, after, columns, budget)
    rows = {business_key(family, r): r for r in previous.rows(family, max_rows=budget)}
    proofs = dict(prior_proofs.get(family, {}))
    for key, (row, lineage) in changed.items():
        if row is None:
            rows.pop(key, None)
            proofs.pop(key, None)
        else:
            rows[key] = row
            proofs[key] = lineage
    if len(rows) > budget:
        raise IntegrityError("Incremental Gold Silver view exceeds its explicit row budget")
    if changed and conflicting_keys(family, rows):
        raise FullReadRequired
    return list(rows.values()), proofs, count
