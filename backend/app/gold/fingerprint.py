"""Content fingerprint of first-party code, derived from the real import graph.

Hand-maintained file lists go stale when modules move; walking the imports does
not. The walk parses source without executing it and follows only modules under
the first-party package, including parent ``__init__`` files that Python runs on
import. Line endings are normalised so Windows and Linux checkouts agree.
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

from .common import digest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "app"


def source_closure(*modules: str, root: Path = BACKEND_ROOT) -> list[Path]:
    """Every first-party source file loaded by importing ``modules``, sorted."""
    pending = deque(modules)
    seen: dict[str, Path] = {}
    while pending:
        name = pending.popleft()
        if name in seen:
            continue
        path = _module_path(name, root)
        if path is None:
            continue
        seen[name] = path
        pending.extend(_parents(name))
        pending.extend(_imports(name, path, root))
    missing = [name for name in modules if name not in seen]
    if missing:
        raise ModuleNotFoundError(f"First-party modules not found: {', '.join(missing)}")
    return sorted(set(seen.values()))


def source_fingerprint(*modules: str, root: Path = BACKEND_ROOT) -> str:
    """SHA-256 over relative path and text of every file in the closure."""
    return digest(
        [
            (path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
            for path in source_closure(*modules, root=root)
        ]
    )


def _module_path(name: str, root: Path) -> Path | None:
    if name != PACKAGE and not name.startswith(f"{PACKAGE}."):
        return None
    base = root.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[:index]) for index in range(1, len(parts))]


def _imports(name: str, path: Path, root: Path) -> list[str]:
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _absolute(node, package)
            found.append(base)
            found.extend(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return [module for module in found if _module_path(module, root) is not None]


def _absolute(node: ast.ImportFrom, package: str) -> str:
    if node.level == 0:
        return node.module or ""
    anchor = package.split(".")[: len(package.split(".")) - node.level + 1]
    return ".".join([*anchor, *([node.module] if node.module else [])])
