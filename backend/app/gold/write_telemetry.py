"""Publication counters from the confirmed transaction log and referenced files."""

import json
from urllib.parse import unquote


def allocated_bytes(path):
    info = path.stat()
    return info.st_blocks * 512 if hasattr(info, "st_blocks") else None


def commit_metrics(target, ref, previous, operation):
    version = ref["delta_version"]
    committed = ref["operation_id"] == operation
    result = {
        "previous_delta_version": previous["delta_version"] if previous else None,
        "delta_version": version,
        "commits": int(committed),
        "parquet_added": 0,
        "logical_bytes_added": 0,
        "physical_bytes_added": 0,
        "parquet_replaced": 0,
        "logical_bytes_replaced": 0,
        "physical_bytes_replaced": 0,
        "rows_inserted": 0 if not committed else None,
        "rows_updated": 0 if not committed else None,
        "rows_deleted": 0 if not committed else None,
        "rows_unchanged_copied": 0 if not committed else None,
        "files_scanned_by_merge": 0 if not committed else None,
        "merge_ms": 0 if not committed else None,
    }
    if not committed:
        return result

    def record_file(kind, action):
        path = target / unquote(action["path"])
        if path.suffix != ".parquet":
            return
        result[f"parquet_{kind}"] += 1
        try:
            physical = allocated_bytes(path)
        except OSError:
            physical = None
        for prefix, size in (("logical", action.get("size")), ("physical", physical)):
            key = f"{prefix}_bytes_{kind}"
            before = result[key]
            result[key] = None if before is None or size is None else before + size

    try:
        with (target / "_delta_log" / f"{version:020d}.json").open(encoding="utf-8") as log:
            for line in log:
                item = json.loads(line)
                if "add" in item:
                    record_file("added", item["add"])
                if "remove" in item:
                    record_file("replaced", item["remove"])
                info = item.get("commitInfo")
                if info is None:
                    continue
                result["delta_operation"] = info.get("operation")
                stats = info.get("operationMetrics", {})
                for field, key in {
                    "rows_inserted": "num_target_rows_inserted",
                    "rows_updated": "num_target_rows_updated",
                    "rows_deleted": "num_target_rows_deleted",
                    "rows_unchanged_copied": "num_target_rows_copied",
                    "files_scanned_by_merge": "num_target_files_scanned",
                    "merge_ms": "execution_time_ms",
                    "output_rows": "num_output_rows",
                }.items():
                    if value := stats.get(key):
                        result[field] = int(value)
                    elif key in stats:
                        result[field] = 0
                if previous is None and result["rows_inserted"] is None:
                    rows = stats.get("num_added_rows", stats.get("num_output_rows"))
                    if rows is not None:
                        result["rows_inserted"] = int(rows)
    except (OSError, ValueError, TypeError, KeyError):
        # Preserve publication/recovery if the optional telemetry cannot be reconstructed.
        return {
            key: value if key in ("previous_delta_version", "delta_version", "commits") else None
            for key, value in result.items()
        }
    return result


def legacy_scope_metrics(metrics):
    """Keep the first instrumentation fields available to existing log consumers."""
    return {
        "delta_version": metrics["delta_version"],
        "reused_version": metrics["commits"] == 0,
        "commits": metrics["commits"],
        "added_parquet": metrics["parquet_added"],
        "added_bytes": metrics["logical_bytes_added"],
        "added_allocated_bytes": metrics["physical_bytes_added"],
    }


def publication_metrics(tables, evidence, milliseconds):
    allocations = [m["physical_bytes_added"] for m in tables.values()]
    allocations.append(evidence.get("new_physical_bytes", 0))
    return {
        "tables": tables,
        "evidence": evidence,
        "publication_ms": milliseconds,
        "new_allocated_data_bytes": None if None in allocations else sum(allocations),
    }
