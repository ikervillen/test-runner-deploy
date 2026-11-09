"""Exercise producer-side input contracts without touching a running ingestion."""

from datetime import datetime

import pytest

from app.gold.common import IntegrityError
from app.gold.producer import _iso, _time, auxiliary


def write_csv(directory, name, content):
    path = directory / name
    path.write_bytes(content.encode("cp1252"))
    return path


def test_auxiliary_capture_keeps_nulls_and_late_closures_without_reopening(tmp_path):
    sources = tmp_path / "source"
    sources.mkdir()
    write_csv(sources, "01_MP9_PARAM_MODELO.csv", "1;9;01/11/2026 06:00;NULL;8;0;0;0;0;\n")
    write_csv(
        sources, "02_MP9_PARAM_MODELO.csv", "1;9;01/11/2026 06:00;01/11/2026 14:00;8;0;0;0;0;\n"
    )
    write_csv(sources, "03_MP9_PARAM_MODELO.csv", "1;9;01/11/2026 06:00;NULL;8;0;0;0;0;\n")
    write_csv(sources, "01_SIP_NUMERO_GOTAS_SIMULTANEAS.csv", "7;Doble;2;\n")
    write_csv(sources, "01_SIP_MAQUINA_MOLDEO.csv", "1;A;0;0;0;7;0;01/01/2026;NULL;0;\n")
    write_csv(sources, "01_MP9_LISTA_RECH_ZC.csv", "1;01/11/2026 06:10:00;100;0;0;4;0;A;0;1;1;\n")
    result = auxiliary(tmp_path / "snapshot", (("AV23", sources),))
    parameters = result["production_parameters"].to_pylist()
    assert len(parameters) == 1
    assert parameters[0]["fecha_off"] == datetime(2026, 11, 1, 14)
    assert result["gobs_dictionary"].to_pylist() == [{"id_gotas": 7, "valor": 2}]
    assert result["machine_gobs"].to_pylist()[0]["vigente_hasta"] is None
    hot = result["hot_rejection_evidence"].to_pylist()[0]
    assert (hot["molde"], hot["seccion"], hot["cavidad"], hot["rechazos"]) == (100, 1, "A", 4)
    # Re-exporting identical bytes shares one content-addressed capture.
    assert len(list((tmp_path / "snapshot/bronze-extensions").rglob("*.csv"))) == 5
    assert auxiliary(tmp_path / "snapshot", (("AV23", sources),)) == result


def test_auxiliary_rejects_contradictory_parameters(tmp_path):
    sources = tmp_path / "source"
    sources.mkdir()
    write_csv(sources, "01_MP9_PARAM_MODELO.csv", "1;9;01/11/2026;NULL;8;0;0;0;0;\n")
    write_csv(sources, "02_MP9_PARAM_MODELO.csv", "1;9;01/11/2026;NULL;12;0;0;0;0;\n")
    with pytest.raises(IntegrityError, match="Contradictory"):
        auxiliary(tmp_path / "snapshot", (("AV23", sources),))


@pytest.mark.parametrize(
    "family",
    ["MP9_PARAM_MODELO", "SIP_NUMERO_GOTAS_SIMULTANEAS", "SIP_MAQUINA_MOLDEO", "MP9_LISTA_RECH_ZC"],
)
def test_auxiliary_rejects_changed_source_schema(tmp_path, family):
    write_csv(tmp_path, f"01_{family}.csv", "1;2;\n")
    with pytest.raises(IntegrityError, match="schema"):
        auxiliary(tmp_path / "snapshot", (("AV23", tmp_path),))


def test_auxiliary_timestamp_contract():
    assert _time("NULL") is None
    assert _time("") is None
    assert _time("01/11/2026 06:10") == _time("01/11/2026 06:10:00")
    assert _iso(None) is None
    assert _iso("2026-11-01T06:10:00") == datetime(2026, 11, 1, 6, 10)
    with pytest.raises(IntegrityError, match="timestamp"):
        _time("2026-11-01")
