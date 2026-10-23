import pytest

from ingesta_delta_lake.common import IntegrityError
from ingesta_delta_lake.control_archive import Archive, read


def test_audit_identity_and_recovery_do_not_depend_on_compression(tmp_path, monkeypatch):
    document = {"values": list(range(1000)) * 20}
    receipts = []
    for level in (1, 9):
        monkeypatch.setenv("INGESTA_CONTROL_ARCHIVE_COMPRESSION_LEVEL", str(level))
        root = tmp_path / str(level)
        archive = Archive(root, "history")
        archive.append(document)
        receipt = archive.finish()
        assert list(read(root, receipt)) == [document]
        receipts.append(receipt)
    assert receipts[0] == receipts[1]


@pytest.mark.parametrize("level", ["0", "10"])
def test_invalid_audit_policy_creates_no_temporary_artifact(tmp_path, monkeypatch, level):
    monkeypatch.setenv("INGESTA_CONTROL_ARCHIVE_COMPRESSION_LEVEL", level)
    root = tmp_path / "audit"
    with pytest.raises(IntegrityError):
        Archive(root, "history")
    assert not root.exists()
