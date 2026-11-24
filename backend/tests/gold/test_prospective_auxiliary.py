from datetime import datetime

from app.gold import common, producer


def test_fresh_gold_only_accepts_hot_rejections_at_or_after_t0(tmp_path, monkeypatch):
    t0 = datetime(2026, 11, 24, 9, 10)
    monkeypatch.setattr(common, "live_epoch", lambda: t0)
    payload = "\n".join(
        ";".join([str(identity), stamp, "3", "", "", "2", "", "", "", "1", "4"])
        for identity, stamp in (
            (1, "24/11/2026 09:09:59"),
            (2, "24/11/2026 09:10:00"),
            (3, "24/11/2026 09:20:00"),
        )
    )
    tables = producer.auxiliary(
        tmp_path, (), captures=[("AV21", "MP9_LISTA_RECH_ZC", payload.encode("ascii"))]
    )
    assert sorted(tables["hot_rejection_evidence"]["id"].to_pylist()) == [2, 3]
