from datetime import datetime
from types import SimpleNamespace

import pyarrow as pa

from ingesta_delta_lake.associations import interval_decision
from ingesta_delta_lake.physical_assignments import outgoing
from scripts.audit_live_fenix import relation_index, relation_rows

HOUR = "2026-11-19T08:"
START, CUT, END = (HOUR + x for x in ("00:00", "05:00", "10:00"))


def make_snapshot(*episodes):
    composition = {}
    for identity, machine, model, start in episodes:
        composition[identity] = {
            "coverage": "VALID",
            "root": {"ID_MAQUINA": machine},
            "zf": [
                {
                    "origen_fenix": "AV32",
                    "ID_MAQUINA": machine,
                    "ID_MODELO": model,
                    "FECHA_ON": start,
                    "FECHA_OFF": None,
                }
            ],
        }
    rows = [
        {
            "origen_fenix": "AV32",
            "id_linea_zf_fenix": branch_id,
            "id_maquina_fenix": machine,
            "codigo_ramal": branch,
            "vigente_desde": START,
            "vigente_hasta": None,
        }
        for branch_id, machine, branch in ((41, 10, 1), (43, 9, 3))
    ]
    return SimpleNamespace(
        manifest={"controls": {"CATALOGO_PRODUCCIONES": {}}, "tables": {"SIP_LINEA_ZF": {}}},
        control=lambda _: {"extra": {"composition": composition}},
        batches=lambda *a, **kw: [pa.RecordBatch.from_pylist(rows)],
    )


def evidence(old_closure):
    start, cut = (
        datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M:%S") for value in (START, CUT)
    )
    text = (
        f"10;477;{start};;41;10;1;1;;\n"
        f"11;477;{start};;41;10;2;1;10;\n"
        f"20;455;{cut};;41;10;1;1;;\n"
        f"21;455;{cut};;41;10;2;1;20;\n"
        f"30;242;{start};;43;9;1;1;;\n"
        f"31;242;{start};;43;9;2;1;30;\n"
    )
    if old_closure:
        text += f"11;477;{start};{cut};41;10;2;1;10;\n"
    return [("AV32", *r) for r in relation_rows(text.encode(), "utf-8")]


def test_explicit_handover_preserves_the_other_tandem_machine():
    snap = make_snapshot(("old", 10, 477, START), ("new", 10, 455, CUT), ("other", 9, 242, START))
    registry, prods, blocked, reasons = relation_index(snap, evidence(old_closure=True))
    assert not blocked and not reasons
    branch = registry["AV32", 1]
    assert interval_decision(branch, CUT, END)[:2] == ("ASSIGNED", ["new"])
    assert outgoing(branch, START, END, prods)[:2] == ("TRANSITION_TO_OUTGOING", ["old"])
    assert interval_decision(registry["AV32", 3], START, END)[:2] == ("ASSIGNED", ["other"])


def test_open_predecessor_is_never_closed_by_onset_of_successor():
    snap = make_snapshot(("old", 10, 477, START), ("new", 10, 455, CUT), ("other", 9, 242, START))
    registry, prods, _, _ = relation_index(snap, evidence(old_closure=False))
    branch = registry["AV32", 1]
    assert interval_decision(branch, START, END)[0] == "PENDING_COVERAGE"
    assert outgoing(branch, START, END, prods) is None


def test_latency_uses_first_visible_gold_commit_and_keeps_missing_files_in_denominator():
    from scripts.audit_live_fenix import assemble

    registered = {
        "block": START,
        "line": "AV32",
        "family": "CONT_ZC",
        "mtime": datetime.fromisoformat(START + "+00:00").timestamp(),
        "bronze": "2026-11-19T08:00:20+00:00",
        "silver": "2026-11-19T08:00:50+00:00",
        "state": "HANDLED",
        "rows": [(START, "key", "content")],
        "issues": 0,
        "error": None,
    }
    missing = {**registered, "bronze": None, "silver": None, "rows": [], "state": "SIN_CAPTURA"}
    snapshot = SimpleNamespace(manifest={"created_at": "2026-11-19T08:09:00+00:00"})
    result = assemble(
        {"start": START, "lines": ["AV32"], "files": [registered, missing], "relation_checks": []},
        {("CONT_ZC", "key"): ("content", "id", {})},
        {"CONT_ZC": {"id"}},
        {("CONT_ZC", "id"): datetime.fromisoformat("2026-11-19T08:01:00+00:00")},
        True,
        {},
        ({}, {}, {}, {}),
        {},
        snapshot,
        SimpleNamespace(document={}),
    )
    row = result["resumen"][0]
    assert (row["Bronze"], row["CSV_Silver"], row["Filas_Silver"], row["Filas_Gold"]) == (
        "1/2",
        "1/2",
        "1/1",
        "1/1",
    )
    assert (row["Fenix_Bronze_s"], row["Bronze_Silver_s"], row["Silver_Gold_s"]) == (20, 30, 10)
