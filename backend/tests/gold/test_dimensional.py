from datetime import datetime, timedelta

import pytest

from app.gold.common import IntegrityError
from app.gold.dimensional import reconcile

NOW = datetime(2026, 11, 6)


def candidate(name="Machine A", end=None, start=NOW):
    return dict(
        entity_key="plant:machine:1",
        valid_from=start,
        valid_to=end,
        source_version="accepted-1",
        source_reference="silver:1",
        planta="A",
        id_maquina_fenix=1,
        nombre=name,
        codigo_linea_zc=23,
    )


def test_type_two_change_preserves_old_version_and_recorded_history():
    old = reconcile("dim_machine", [candidate()], [], NOW)
    new = reconcile("dim_machine", [candidate("Machine corrected")], old, NOW + timedelta(hours=1))
    assert len(new) == 2
    historical = next(r for r in new if not r["is_current"])
    current = next(r for r in new if r["is_current"])
    assert historical["dimension_key"] == old[0]["dimension_key"]
    assert historical["recorded_to"] == current["recorded_from"]
    assert old[0]["is_current"]  # previous publication was not mutated
    assert (
        reconcile("dim_machine", [candidate("Machine corrected")], new, NOW + timedelta(hours=2))
        == new
    )


def test_late_closure_preserves_surrogate_key_and_original_snapshot():
    old = reconcile("dim_machine", [candidate()], [], NOW)
    end = NOW + timedelta(days=1)
    new = reconcile("dim_machine", [candidate(end=end)], old, end)
    assert len(new) == 1
    assert new[0]["dimension_key"] == old[0]["dimension_key"]
    assert new[0]["valid_to"] == end
    assert old[0]["valid_to"] is None


def test_missing_business_effective_date_stays_unknown():
    values = reconcile("dim_machine", [candidate(start=None)], [], NOW)
    assert values[0]["valid_from"] is None
    assert values[0]["recorded_from"] == NOW


@pytest.mark.parametrize(
    "values",
    [
        [candidate(), candidate("contradiction")],
        [candidate(end=NOW)],
        [candidate(), candidate(start=NOW + timedelta(minutes=1))],
    ],
)
def test_contradictory_and_overlapping_validity_is_rejected(values):
    with pytest.raises(IntegrityError):
        reconcile("dim_machine", values, [], NOW)


def test_recording_time_does_not_move_backwards():
    old = reconcile("dim_machine", [candidate()], [], NOW)
    with pytest.raises(IntegrityError, match="later recording"):
        reconcile("dim_machine", [candidate("different")], old, NOW)
