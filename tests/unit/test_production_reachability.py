"""Every module under ``src/hermes`` is reachable from something that runs (issue #1181).

The repo audit of 18 Oct found about 2,800 lines of tested code that nothing
in production imported: features that looked live and were not. This scan
walks the import graph from the code that actually runs (``python -m hermes``,
the entrypoints, the ``[project.scripts]`` targets and every script under
``scripts/``) and fails when a module nobody reaches appears, or when a
parked module listed below becomes reachable or disappears, so the list
only ever shrinks.

A ``TYPE_CHECKING`` import counts as an edge, as does a ``"hermes.x.y"``
string (lazy imports and entry points).
"""

import ast
import tomllib
from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT

_SRC = REPO_ROOT / "src"

_PARKED: dict[str, str] = {
    "hermes.adapters.ledger.migrations": "SQL files the migration runner loads by path",
    "hermes.adapters.exchanges.kraken_spot": "order-placing adapter, not wired until MVP2",
    "hermes.adapters.exchanges.binance_spot": "issue #1220: order-placing adapter, MVP2",
    "hermes.infrastructure.mainnet_permit": "issue #1220: real-money gate of the order adapters",
    "hermes.infrastructure.env_gate": "issue #1220: read only by mainnet_permit",
    "hermes.ports.exchange": "the order port the MVP2 adapters implement",
    "hermes.adapters.market_data.binance_live_ws": "issue #1181: live L2 stream, Code Owner call",
    "hermes.ports.market_data": "issue #1181: the port binance_live_ws implements",
    "hermes.ports.dataset_manifest": "issue #1181: port with no consumer, to judge",
    "hermes.ports.fees": "issue #1181: port with no consumer, to judge",
    "hermes.ports.klines": "issue #1181: port with no consumer, to judge",
    "hermes.ports.portfolio_history": "issue #1181: port with no consumer, to judge",
    "hermes.ports.stop_history": "issue #1181: port with no consumer, to judge",
}


def _module_name(path: Path) -> str:
    parts = path.relative_to(_SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _modules() -> dict[str, Path]:
    return {_module_name(path): path for path in _SRC.rglob("*.py")}


def _named(tree: ast.Module, package: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                base = ".".join([*parts, *([node.module] if node.module else [])])
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("hermes.")
        ):
            names.add(node.value.split(":")[0])
    return names


def _resolve(names: set[str], modules: dict[str, Path]) -> set[str]:
    found: set[str] = set()
    for name in names:
        prefix = name
        while prefix:
            if prefix in modules:
                found.add(prefix)
            prefix = prefix.rpartition(".")[0]
    return found


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _package(path: Path, name: str) -> str:
    return name if path.name == "__init__.py" else name.rpartition(".")[0]


def unreachable(modules: dict[str, Path], roots: set[str]) -> set[str]:
    """Return the modules no import path from ``roots`` reaches."""
    edges = {
        name: _resolve(
            _named(ast.parse(path.read_text(encoding="utf-8")), _package(path, name)), modules
        )
        for name, path in modules.items()
    }
    seen: set[str] = set()
    stack = [root for root in roots if root in modules]
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.add(name)
            stack.extend(edges[name])
    return set(modules) - seen


def _roots(modules: dict[str, Path]) -> set[str]:
    roots = {"hermes.__main__"} | {n for n in modules if n.startswith("hermes.entrypoints")}
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    roots.update(target.split(":")[0] for target in project["project"]["scripts"].values())
    for script in sorted((REPO_ROOT / "scripts").rglob("*.py")):
        roots |= _resolve(_named(_parse(script), ""), modules)
    return roots


def test_every_module_is_reachable_or_parked_with_a_reason() -> None:
    """A module nothing runs fails here until it is wired, deleted or parked below."""
    modules = _modules()
    found = unreachable(modules, _roots(modules))
    assert {
        "never run, not parked": sorted(found - set(_PARKED)),
        "parked but now reachable or gone": sorted(set(_PARKED) - found),
    } == {"never run, not parked": [], "parked but now reachable or gone": []}


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"run": "import hermes.used\n", "used": "", "dead": ""}, {"hermes.dead"}),
        ({"run": "from hermes import used\n", "used": "", "dead": ""}, {"hermes.dead"}),
        ({"run": "from . import used\n", "used": "", "dead": ""}, {"hermes.dead"}),
        ({"run": "X = 'hermes.used:main'\n", "used": "", "dead": ""}, {"hermes.dead"}),
        (
            {
                "run": "import typing\nif typing.TYPE_CHECKING:\n    import hermes.used\n",
                "used": "",
                "dead": "",
            },
            {"hermes.dead"},
        ),
        (
            {"run": "", "used": "import hermes.dead\n", "dead": ""},
            {"hermes", "hermes.used", "hermes.dead"},
        ),
    ],
)
def test_the_scan_follows_each_kind_of_import(
    tmp_path: Path, files: dict[str, str], expected: set[str]
) -> None:
    """Absolute, package, relative, string and TYPE_CHECKING imports are edges; nothing else is."""
    package = tmp_path / "hermes"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for stem, text in files.items():
        (package / f"{stem}.py").write_text(text, encoding="utf-8")
    modules = {
        ".".join(path.relative_to(tmp_path).with_suffix("").parts).removesuffix(".__init__"): path
        for path in package.rglob("*.py")
    }
    assert unreachable(modules, {"hermes.run"}) == expected
