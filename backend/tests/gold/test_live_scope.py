from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pytest

from app.gold.attribution import KEY_FIELDS, source_id
from app.gold.common import IntegrityError
from app.gold.live_scope import LiveScope
from app.gold.publications import Snapshot


class InputTable:
    def __init__(self, rows):
        self.rows = rows

    def to_pyarrow_dataset(self):
        return ds.dataset(pa.Table.from_pylist(self.rows))


def inputs():
    start = datetime(2026, 11, 9, 10)
    end = start + timedelta(hours=2)
    tables = {}
    assignments = []
    expected = set()
    for family, keys in KEY_FIELDS.items():
        time_key = "FECHA_ON" if family == "LISTA_AR" else "FECHA"
        rows = []
        for i, time in enumerate(
            (
                start - timedelta(minutes=10),
                start + timedelta(minutes=10),
                end + timedelta(minutes=10),
            )
        ):
            row = dict.fromkeys(keys)
            row.update(origen_fenix="AV21", **{time_key: time})
            if family == "LISTA_AR":
                row["FECHA_OFF"] = time + timedelta(minutes=15) if i != 2 else None
            rows.append(row)
            identity = source_id(family, row)
            assignments.append({"source_family": family, "source_id": identity})
            if i == 1 or (i == 0 and family == "LISTA_AR"):
                expected.add(identity)
        tables[family] = InputTable(rows)
    tables["observation_assignments"] = InputTable(assignments)
    return LiveScope(start, end), Snapshot(Path("."), {"coverage": []}, tables), expected


def test_scope_joins_only_attributions_for_selected_facts():
    scope, snapshot, expected = inputs()
    actual = scope.assignments(snapshot, max_rows=10, max_total_rows=10)
    assert {row["source_id"] for row in actual} == expected


def test_scope_keeps_row_guard_for_matching_rows():
    scope, snapshot, _ = inputs()
    with pytest.raises(IntegrityError, match="total fact budget"):
        scope.assignments(snapshot, max_rows=10, max_total_rows=4)


def test_scope_never_extends_the_accredited_period():
    scope, _, _ = inputs()
    old = {"source": "CONT_ZC", "estado": "READY", "covered_until": scope.start.isoformat()}
    current = {"source": "LISTA_RECH", "estado": "READY", "covered_until": scope.end.isoformat()}
    snapshot = Snapshot(Path("."), {"coverage": [old, current]})
    view = scope.coverage_view(snapshot)
    assert old["estado"] == "READY"
    assert view.manifest["coverage"][0]["estado"] == "WAITING_INPUT"
    assert view.manifest["coverage"][1]["covered_until"] == current["covered_until"]
    assert view.manifest["coverage"][1]["covered_from"] == scope.start
