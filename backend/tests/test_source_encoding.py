from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
SOURCES = (("backend/app", "*.py"), ("src", "*.ts"), ("src", "*.tsx"))
# Signatures of UTF-8 text decoded as CP1252 (e.g. "ó" read back as "\u00c3\u00b3").
MOJIBAKE = ("\u00c3", "\u00c2", "\u00e2\u20ac", "\ufffd")


def test_sources_have_no_mojibake():
    damaged = [
        path.relative_to(PROJECT).as_posix()
        for folder, pattern in SOURCES
        for path in sorted((PROJECT / folder).rglob(pattern))
        if any(marker in path.read_text(encoding="utf-8") for marker in MOJIBAKE)
    ]
    assert damaged == []
