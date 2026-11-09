from app.gold.freshness import source_freshness

LIVE = {"LISTA_RECH", "CONT_ZC"}


def manifest(*coverage):
    return {
        "publication_id": "silver-1",
        "created_at": "2026-11-06T14:00:00+00:00",
        "source_mode": "continuous",
        "coverage": list(coverage),
    }


def row(source, seen, origin="AV22"):
    return {"source": source, "origin": origin, "last_successful_discovery": seen}


def test_lag_is_measured_against_the_freshest_family():
    result = source_freshness(
        manifest(
            row("LISTA_RECH", "2026-11-06T15:00:00"),
            row("LISTA_RECH", "2026-11-06T14:50:00", origin="AV23"),
            row("CONT_ZC", "2026-11-06T14:30:00"),
            row("CATALOGO_PRODUCCIONES", "2026-11-06T10:00:00"),
        ),
        LIVE,
    )
    assert result["discovery_lag_seconds"] == {"CONT_ZC": 1800.0, "LISTA_RECH": 0.0}
    assert result["published_at"] == "2026-11-06T14:00:00+00:00"
    assert result["source_mode"] == "continuous"


def test_offset_timestamps_compare_as_local_wall_clock():
    result = source_freshness(
        manifest(
            row("LISTA_RECH", "2026-11-06T15:00:00+01:00"), row("CONT_ZC", "2026-11-06T15:00:00")
        ),
        LIVE,
    )
    assert result["discovery_lag_seconds"] == {"CONT_ZC": 0.0, "LISTA_RECH": 0.0}


def test_snapshot_without_discoveries_reports_no_lag():
    result = source_freshness(manifest(row("LISTA_RECH", None)), LIVE)
    assert result["discovery_lag_seconds"] == {}
    assert result["publication_id"] == "silver-1"
