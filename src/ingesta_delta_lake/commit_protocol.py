"""Persistent application transactions complement checked operation reconciliation.

Sequence follows the expected Delta version under the existing single-writer
lock. Never use attempt IDs or container IDs: retries must retain the identity.
"""

from deltalake import Transaction


def application_transaction(control_id, before_version):
    return [Transaction("fenix:" + control_id, 0 if before_version is None else before_version + 1)]
