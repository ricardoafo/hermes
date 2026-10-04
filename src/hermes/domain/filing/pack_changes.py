"""What changed in a filing pack's headline figures between two refreshes.

Figures are compared as the strings the pack prints, so a change is exactly
what the operator would see change.

Reference: openspec/changes/568-tax-pack/change.md R-4.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

__all__ = ["FIRST_REFRESH", "figure_changes"]

FIRST_REFRESH: Final[str] = "First refresh of this pack."


def figure_changes(
    previous: Mapping[str, str] | None, current: Mapping[str, str]
) -> tuple[str, ...]:
    """Return one line per figure that differs, sorted by figure name.

    Args:
        previous: Figures of the last refresh, or ``None`` when there was none.
        current: Figures of this refresh.

    Returns:
        The change lines; empty when nothing changed.
    """
    if previous is None:
        return (FIRST_REFRESH,)
    lines: list[str] = []
    for name in sorted(previous.keys() | current.keys()):
        old = previous.get(name)
        new = current.get(name)
        if old == new:
            continue
        if old is None:
            lines.append(f"{name}: {new} (new)")
        elif new is None:
            lines.append(f"{name}: removed (was {old})")
        else:
            lines.append(f"{name}: {old} → {new}")
    return tuple(lines)
