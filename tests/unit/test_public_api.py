"""__all__ completeness guard for public sub-layers.

Every module under hermes.domain.* and hermes.ports.* — including package
__init__.py — MUST declare __all__ as a list[str]. Empty list is valid
for package __init__.py that intentionally re-exports nothing (importers
use the deeper submodule directly). Ratchets the convention documented in
code-conventions.md §14 ("public/internal API").

Design note: package __init__.py IS NOT exempt from the rule (per proposal
D-5); an explicit `__all__: list[str] = []` documents intent. Modules with
public symbols must list them.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

import pytest

import hermes.domain
import hermes.ports


def _walk(pkg: ModuleType) -> list[str]:
    """Return every importable submodule name (including the package itself)."""
    return [
        pkg.__name__,
        *(info.name for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}.")),
    ]


ALL_MODULES = sorted(set(_walk(hermes.domain) + _walk(hermes.ports)))


@pytest.mark.parametrize("module_name", ALL_MODULES)
def test_module_declares_all(module_name: str) -> None:
    """Every hermes.domain.* and hermes.ports.* module declares __all__: list[str].

    Empty list is allowed for package __init__.py (see module docstring).
    """
    mod = importlib.import_module(module_name)
    assert hasattr(mod, "__all__"), f"{module_name} is missing __all__"
    all_attr = mod.__all__
    assert isinstance(all_attr, list), (
        f"{module_name}.__all__ must be a list, got {type(all_attr).__name__}"
    )
    for name in all_attr:
        assert isinstance(name, str), f"{module_name}.__all__ contains non-str: {name!r}"
