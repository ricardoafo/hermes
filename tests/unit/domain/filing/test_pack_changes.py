"""Tests for the changes between two tax pack refreshes (change 568-tax-pack R-4)."""

from __future__ import annotations

from hermes.domain.filing.pack_changes import FIRST_REFRESH, figure_changes


def test_first_refresh_says_so() -> None:
    """Invariant: with no previous figures the only line is the first-refresh note."""
    assert figure_changes(None, {"net": "1.00"}) == (FIRST_REFRESH,)


def test_identical_figures_give_no_change() -> None:
    """Invariant: a refresh that moves nothing reports nothing (no changes.md entry)."""
    assert figure_changes({"net": "1.00", "sales": "2"}, {"net": "1.00", "sales": "2"}) == ()


def test_a_changed_figure_shows_old_and_new() -> None:
    """Invariant: a moved figure is reported with both values."""
    assert figure_changes({"net": "1.00"}, {"net": "2.50"}) == ("net: 1.00 → 2.50",)


def test_added_and_removed_figures_are_named() -> None:
    """Invariant: a figure that appears or disappears is reported as such, sorted by name."""
    assert figure_changes({"a": "1"}, {"b": "2"}) == ("a: removed (was 1)", "b: 2 (new)")
