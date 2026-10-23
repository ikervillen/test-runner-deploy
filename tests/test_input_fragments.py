import csv
import hashlib
from contextlib import closing
from dataclasses import replace

import pytest
from test_mia_batches import csv_row

from ingesta_delta_lake import defects_service as batching
from ingesta_delta_lake.input_fragments import fragments
from ingesta_delta_lake.maintenance import replace_control
from ingesta_delta_lake.rejections_storage import RejectionsControl
from ingesta_delta_lake.service import run
from ingesta_delta_lake.sources import Snapshot


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp1252"])
def test_fragments_keep_complete_csv_records_and_encoding(tmp_path, encoding):
    path = tmp_path / "family" / "20261014" / "source.csv"
    path.parent.mkdir(parents=True)
    data = '1;"linea\ncon salto";ñ;\n2;normal;ñ;\n3;normal;ñ;\n'
    path.write_text(data, encoding=encoding, newline="")
    raw = path.read_bytes()
    snapshot = Snapshot(
        "AV21",
        "original.csv",
        hashlib.sha256(raw).hexdigest(),
        str(path),
        len(raw),
        0,
        "2026-10-14T00:00:00+00:00",
        None,
    )
    parts = list(fragments(snapshot, encoding, target_bytes=65))
    rows = []
    for part in parts:
        with open(part.bronze_path, encoding=encoding, newline="") as stream:
            rows.extend(csv.reader(stream, delimiter=";"))
        assert part.size <= 65
    assert rows == [
        ["1", "linea\ncon salto", "ñ", ""],
        ["2", "normal", "ñ", ""],
        ["3", "normal", "ñ", ""],
    ]
    assert [p.sha256 for p in fragments(snapshot, encoding, target_bytes=65)] == [
        p.sha256 for p in parts
    ]


def test_large_mia_capture_acknowledges_parent_only_after_fragments(config, monkeypatch):
    config = replace(
        config, family="LISTA_RECH", timestamp_policy="local_naive_test_v1", mia_batches_per_turn=1
    )
    path = config.input_root / "AV21" / "LISTA_RECH.csv"
    path.write_text(csv_row(config.family, 0), encoding="utf-8")
    run(config)
    replace_control(config, apply=True, reformulate=True)
    path.write_text("".join(csv_row(config.family, i) for i in range(10)), encoding="utf-8")
    parent_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(batching, "MAX_FILE_BYTES", 100)
    monkeypatch.setattr(batching, "BATCH_FILES", 1)
    result = run(config)
    assert result["remaining_versions"] > 0
    with closing(RejectionsControl(config)) as control:
        assert ("AV21", parent_hash) not in control.consumed_inputs()
    for _ in range(15):
        result = run(config)
        if not result["remaining_versions"]:
            break
    assert result["published_rows"] == result["resolved_groups"] == 10
    with closing(RejectionsControl(config)) as control:
        assert ("AV21", parent_hash) in control.consumed_inputs()
    assert run(config)["new_entries"] == 0
