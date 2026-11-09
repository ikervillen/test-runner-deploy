from pathlib import Path

import pytest

from app.gold.common import IntegrityError, read_json
from app.gold.contracts import CONTRACTS, Contract, contract_id, describe, ordered, validate
from app.gold.schemas import SCHEMAS


def test_every_published_table_has_one_contract_and_acyclic_dependencies():
    assert set(CONTRACTS) == set(SCHEMAS)
    sequence = ordered()
    for name, contract in CONTRACTS.items():
        assert contract.grain
        assert all(sequence.index(parent) < sequence.index(name) for parent in contract.parents)
    assert len(contract_id()) == 64
    assert len(describe()["tables"]) == len(SCHEMAS)
    assert read_json(Path(__file__).resolve().parents[3] / "gold/contracts-v3.json") == describe()


def test_cycles_and_unknown_tables_are_rejected(monkeypatch):
    with pytest.raises(IntegrityError, match="Unknown"):
        ordered(["invented"])
    monkeypatch.setitem(CONTRACTS, "dim_plant", Contract("dimension", "one", ("dim_date",)))
    monkeypatch.setitem(CONTRACTS, "dim_date", Contract("dimension", "one", ("dim_plant",)))
    with pytest.raises(IntegrityError, match="Cyclic"):
        ordered()


def test_empty_full_publication_is_explicit_and_partial_cannot_claim_completeness():
    validate({name: [] for name in CONTRACTS}, complete=True)
    with pytest.raises(IntegrityError, match="Incomplete"):
        validate({"productions": []}, complete=True)


def test_unknown_dimension_reference_fails_before_publication():
    from app.gold.dimensional import validate_links

    with pytest.raises(IntegrityError, match="missing dimension"):
        validate_links({"quality_scores": [{"plant_key": "missing"}]})
