"""Persistent application transactions complement checked operation reconciliation.

Sequence follows the expected Delta version under the existing single-writer
lock. Never use attempt IDs or container IDs: retries must retain the identity.
"""

from deltalake import Transaction


def delta_configuration(config):
    """Independent stores retain change evidence until an explicit safe cleanup exists."""
    if not config.change_data_feed:
        return None
    return {
        "delta.enableChangeDataFeed": "true",
        "delta.enableExpiredLogCleanup": "false",
    }


def application_transaction(control_id, before_version):
    return [Transaction("fenix:" + control_id, 0 if before_version is None else before_version + 1)]


def merge_counts_match(metrics, inserts, updates, config):
    if metrics["num_target_rows_deleted"]:
        return False
    if config.archive_context:
        # Intention verification checks the complete accepted logical result.
        # A retired row is physically inserted when its accepted closure changes.
        return metrics["num_target_rows_inserted"] + metrics["num_target_rows_updated"] == len(
            inserts
        ) + len(updates)
    return metrics["num_target_rows_inserted"] == len(inserts) and metrics[
        "num_target_rows_updated"
    ] == len(updates)
