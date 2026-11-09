from pathlib import Path

import pytest

from app.gold.fingerprint import BACKEND_ROOT, source_closure, source_fingerprint


def _write(root: Path, relative: str, text: str = "") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def package(tmp_path: Path) -> Path:
    _write(tmp_path, "app/__init__.py")
    _write(tmp_path, "app/engine/__init__.py", "from .entry import run\n")
    _write(tmp_path, "app/engine/entry.py", "from . import helpers\nfrom ..rules import limit\n")
    _write(tmp_path, "app/engine/helpers.py", "import json\n")
    _write(tmp_path, "app/rules.py", "def limit():\n    import app.lazy\n")
    _write(tmp_path, "app/lazy.py")
    _write(tmp_path, "app/unrelated.py")
    return tmp_path


def _relative(paths: list[Path], root: Path) -> list[str]:
    return [path.relative_to(root).as_posix() for path in paths]


def test_closure_follows_relative_absolute_and_deferred_imports(package):
    assert _relative(source_closure("app.engine.entry", root=package), package) == [
        "app/__init__.py",
        "app/engine/__init__.py",
        "app/engine/entry.py",
        "app/engine/helpers.py",
        "app/lazy.py",
        "app/rules.py",
    ]


def test_fingerprint_changes_with_any_transitive_dependency(package):
    before = source_fingerprint("app.engine.entry", root=package)
    _write(package, "app/unrelated.py", "VALUE = 1\n")
    assert source_fingerprint("app.engine.entry", root=package) == before
    _write(package, "app/lazy.py", "VALUE = 1\n")
    assert source_fingerprint("app.engine.entry", root=package) != before


def test_fingerprint_ignores_line_endings(package):
    before = source_fingerprint("app.engine.entry", root=package)
    (package / "app/rules.py").write_bytes(b"def limit():\r\n    import app.lazy\r\n")
    assert source_fingerprint("app.engine.entry", root=package) == before


def test_unknown_entry_module_fails_loudly(package):
    with pytest.raises(ModuleNotFoundError, match=r"app\.missing"):
        source_closure("app.missing", root=package)


@pytest.mark.parametrize(
    ("entry", "required"),
    [
        ("app.gold.pipeline", {"app/domain/score_config.py", "app/domain/score_math.py"}),
        (
            "app.gold.legacy",
            {
                "app/infrastructure/scoring/repository.py",
                "app/infrastructure/scoring/mia.py",
                "app/infrastructure/scoring/aql.py",
                "app/infrastructure/scoring/dar.py",
                "app/infrastructure/scoring/zc.py",
                "app/infrastructure/aggregates/mia.py",
                "app/domain/score_config.py",
                "app/domain/shifts.py",
            },
        ),
    ],
)
def test_real_engines_cover_their_score_modules(entry, required):
    assert required <= set(_relative(source_closure(entry), BACKEND_ROOT))
