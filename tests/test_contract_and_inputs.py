import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ingesta_delta_lake import sources
from ingesta_delta_lake.common import CaptureError, ConfigurationError, IntegrityError
from ingesta_delta_lake.config import PROJECT_ROOT, load_config
from ingesta_delta_lake.modelos import decide, parse
from ingesta_delta_lake.sources import Snapshot, capture, discover, parse_name


def snapshot(path: Path, data: bytes) -> Snapshot:
    path.write_bytes(data)
    return Snapshot(
        "AV21",
        str(path),
        hashlib.sha256(data).hexdigest(),
        str(path),
        len(data),
        0,
        "2026-10-16T09:20:00Z",
        None,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("SIP_MODELO.csv", None),
        ("20261014_SIP_MODELO.csv", "2026-10-14"),
        ("16102026_SIP_MODELO.csv", "2026-10-16"),
        ("16102026_92028_SIP_MODELO.csv", "2026-10-16T09:20:28"),
        ("16102026_092028_SIP_MODELO.csv", "2026-10-16T09:20:28"),
        ("16102026_142028_sip_modelo.CSV", "2026-10-16T14:20:28"),
    ],
)
def test_names(name, expected):
    assert parse_name(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "32102026_92028_SIP_MODELO.csv",
        "16102026_250000_SIP_MODELO.csv",
        "16102026_96028_SIP_MODELO.csv",
        "otro_SIP_MODELO.csv",
        "SIP_MODELO.txt",
    ],
)
def test_invalid_names(name):
    with pytest.raises(CaptureError):
        parse_name(name)


def test_discovery_is_scoped(config, source):
    (source.parent / "16102026_92028_CONT_GEN.csv").write_text("not input", encoding="utf-8")
    (source.parent / "20261014_SIP_MODELO.csv").write_bytes(source.read_bytes())
    found = discover(config)
    assert {p.name for _, p, _ in found} == {"SIP_MODELO.csv", "20261014_SIP_MODELO.csv"}


def test_unknown_selected_name_is_not_silently_ignored(config, source):
    (source.parent / "bad_SIP_MODELO.csv").write_bytes(source.read_bytes())
    with pytest.raises(CaptureError):
        discover(config)


def test_discovery_resolves_root_once_instead_of_every_ordinary_file(config, source, monkeypatch):
    from pathlib import Path

    for hour in range(10):
        (source.parent / f"16102026_{hour:02d}0028_SIP_MODELO.csv").write_bytes(source.read_bytes())
    resolved = []
    original = Path.resolve

    def track(path, *args, **kwargs):
        resolved.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", track)
    assert len(discover(config)) == 11
    assert resolved == [config.sources[0][1]]


def test_discovery_rejects_symlink_escape(config, source, tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_bytes(source.read_bytes())
    alias = source.parent / "20261015_SIP_MODELO.csv"
    try:
        alias.symlink_to(outside)
    except OSError:
        pytest.skip("Symbolic links require host privileges")
    with pytest.raises(CaptureError, match="fuera del origen"):
        discover(config)


def test_aliases_verify_same_immutable_capture_once_per_scan(config, source, monkeypatch):
    from ingesta_delta_lake import sources

    first = capture(config, "AV21", source, None)
    known = {("AV21", first.sha256): first}
    original = sources.hashlib.file_digest
    checks = []

    def track(stream, algorithm):
        checks.append(stream.name)
        return original(stream, algorithm)

    monkeypatch.setattr(sources.hashlib, "file_digest", track)
    verified = set()
    for _ in range(3):
        assert (
            capture(config, "AV21", source, None, known=known, verified=verified).sha256
            == first.sha256
        )
    assert len(checks) == 1
    capture(config, "AV21", source, None, known=known, verified=set())
    assert len(checks) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"environment": "production"},
        {"plant": "../OTHER"},
        {"stable_wait_seconds": 0},
        {"capture_attempts": 0},
        {"encoding": "unknown-encoding"},
    ],
)
def test_config_rejects_unsafe_options(config, changes):
    with pytest.raises(ConfigurationError):
        replace(config, **changes).validate()


def test_config_rejects_overlapping_roots(config):
    with pytest.raises(ConfigurationError):
        replace(config, bronze_root=config.input_root / "bronze").validate()
    with pytest.raises(ConfigurationError):
        replace(config, silver_root=PROJECT_ROOT / "data").validate()


def test_config_loads_explicit_paths(config, tmp_path):
    fields = {"plant": config.plant, "environment": "test"}
    fields.update(
        {
            k: str(getattr(config, k))
            for k in ("input_root", "bronze_root", "silver_root", "control_root")
        }
    )
    content = "\n".join(f"{k} = {json.dumps(v)}" for k, v in fields.items())
    path = tmp_path / "config.toml"
    path.write_text(content + '\n[sources]\nAV21 = "AV21"\n', encoding="utf-8")
    assert load_config(path).sources == config.sources
    path.write_text(content + '\ntypo = 1\n[sources]\nAV21 = "AV21"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_capture_does_not_modify_source(config, source):
    original = source.read_bytes()
    result = capture(config, "AV21", source, None)
    assert source.read_bytes() == original
    assert Path(result.bronze_path).read_bytes() == original
    assert capture(config, "AV21", source, None).bronze_path == result.bronze_path


def test_changed_capture_is_retried_and_not_published(config, source, monkeypatch):
    reads = iter([b"A", b"B"] * config.capture_attempts)
    monkeypatch.setattr(sources, "_read_limited", lambda *_: next(reads))
    with pytest.raises(CaptureError):
        capture(config, "AV21", source, None)
    assert not config.bronze_root.exists()


def test_capture_limit(config, source):
    with pytest.raises(CaptureError):
        capture(replace(config, max_file_bytes=1, capture_attempts=1), "AV21", source, None)


def test_altered_bronze_is_not_overwritten(config, source):
    captured = capture(config, "AV21", source, None)
    target = Path(captured.bronze_path)
    target.write_bytes(b"tampered")
    with pytest.raises(IntegrityError):
        capture(config, "AV21", source, None)
    assert target.read_bytes() == b"tampered"


def test_contract_preserves_codes_and_ignores_state(tmp_path):
    s = snapshot(tmp_path / "in.csv", b"1;0012/03A;1;\n1;0012/03A;2;\n2;CODE/ABC;X;\n")
    parsed = parse([s], "utf-8-sig")
    inserts, issues = decide(parsed, {})
    assert not issues
    assert parsed.rows_read == 3 and len(parsed.pairs) == 2
    assert [(r["id_modelo_fenix"], r["codigo_modelo_botella"]) for r in inserts] == [
        (1, "0012/03A"),
        (2, "CODE/ABC"),
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b"1.0;MODEL;1;\n",
        b"9223372036854775808;MODEL;1;\n",
        b";MODEL;1;\n",
        b"1;;1;\n",
        b"1;   ;1;\n",
        b"1;MODEL;1;EXTRA;\n",
        b"1;MODEL\n",
    ],
)
def test_invalid_rows_keep_full_evidence(tmp_path, raw):
    parsed = parse([snapshot(tmp_path / "in.csv", raw)], "utf-8-sig")
    assert parsed.issues and parsed.issues[0]["evidence"]["raw"]
    assert not parsed.pairs


def test_decoding_is_strict(tmp_path):
    parsed = parse([snapshot(tmp_path / "in.csv", b"1;\xff;1;\n")], "utf-8-sig")
    assert parsed.issues[0]["reason"] == "ENCODING"
    assert parsed.issues[0]["raw_hex"] == b"1;\xff;1;\n".hex()


@pytest.mark.parametrize(
    ("raw", "existing"),
    [
        (b"1;A;1;\n1;B;1;\n", {}),
        (b"1;A;1;\n2;A;1;\n", {}),
        (b"1;B;1;\n", {1: "A"}),
        (b"2;A;1;\n", {1: "A"}),
    ],
)
def test_conflicts_in_both_directions(tmp_path, raw, existing):
    inserts, issues = decide(parse([snapshot(tmp_path / "in.csv", raw)], "utf-8-sig"), existing)
    assert not inserts and issues
    assert all(i["reason"] == "ASSOCIATION_CONFLICT" for i in issues)


def test_prepared_source_hash_is_checked(tmp_path):
    snap = snapshot(tmp_path / "in.csv", b"1;A;1;\n")
    Path(snap.bronze_path).write_bytes(b"2;B;1;\n")
    with pytest.raises(IntegrityError):
        parse([snap], "utf-8-sig")


def test_empty_discarded_state_is_not_a_business_validation(tmp_path):
    parsed = parse([snapshot(tmp_path / "in.csv", b"1;MODEL;\n")], "utf-8-sig")
    assert not parsed.issues and (1, "MODEL") in parsed.pairs


def test_csv_error_keeps_full_unparseable_content(tmp_path):
    content = b'1;"UNTERMINATED;1;\n'
    parsed = parse([snapshot(tmp_path / "in.csv", content)], "utf-8-sig")
    assert parsed.issues[0]["reason"] == "CSV"
    assert parsed.issues[0]["raw_text"] == content.decode()


def test_repeated_rows_keep_count_without_expanding_duplicate_payloads(tmp_path):
    parsed = parse([snapshot(tmp_path / "in.csv", b"1;MODEL;1;\n" * 100)], "utf-8-sig")
    assert parsed.rows_read == 100
    assert len(parsed.pairs[(1, "MODEL")]) == 1
    assert parsed.pairs[(1, "MODEL")][0]["duplicate_count"] == 99
