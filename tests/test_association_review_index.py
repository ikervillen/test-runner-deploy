import json

from ingesta_delta_lake import associations as module


def row(number, origin="AV21"):
    return {
        "origen_fenix": origin,
        "FECHA": "2026-10-14T08:00:00",
        "MOLDE": number,
        "LINEA": 1,
        "COD_MAQ_ZF": 13,
        "INSPECC": 96,
        "CAVIDAD": None,
    }


def test_index_preserves_key_and_origin_blocks_without_mutating_the_bundle():
    family = "LISTA_RECH"
    cases = [
        {"payload": {"key": list(module.SOURCE_KEYS[family](row(1)))}},
        {"payload": {"evidence": [{"origin": "AV32"}]}},
    ]
    bundle = {"cases": {family: cases}, "sources": {"LISTA_AR": []}}
    before = json.dumps(bundle)
    context = module.prepare_context(bundle)
    for value, expected in [(row(1), True), (row(2), False), (row(2, "AV32"), True)]:
        assert module.blocked(bundle, family, value, context.reviews) == expected
        assert module.blocked(bundle, family, value) == expected
    assert json.dumps(bundle) == before


def test_review_hash_work_is_linear_in_cases_plus_rows(monkeypatch):
    family = "LISTA_RECH"
    bundle = {
        "cases": {
            family: [{"payload": {"key": module.SOURCE_KEYS[family](row(i))}} for i in range(1000)]
        }
    }
    calls, original = [], module.digest

    def counted(value):
        calls.append(1)
        return original(value)

    monkeypatch.setattr(module, "digest", counted)
    index = module.review_index(bundle)
    assert not any(module.blocked(bundle, family, row(i), index) for i in range(1000, 2000))
    assert len(calls) == 2000
