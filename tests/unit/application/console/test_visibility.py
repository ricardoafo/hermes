"""``pnl_visible`` shows shadow P&L only on the operator's own pulled surfaces.

Code Owner ruling 2026-10-03: True for the operator CLI, the TUI and pulled
Telegram replies; False for pushed Telegram, Slack, reports, MCP tools, the
supervisor desk, the challenger lab and the ADR-012 loops. No override.
"""

import inspect

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.console.visibility import Surface, pnl_visible

_OPERATOR = (Surface.OPERATOR_CLI, Surface.OPERATOR_TUI, Surface.TELEGRAM_PULLED)
_BLIND = (
    Surface.TELEGRAM_PUSHED,
    Surface.SLACK,
    Surface.REPORT,
    Surface.MCP_TOOL,
    Surface.SUPERVISOR_DESK,
    Surface.CHALLENGER_LAB,
    Surface.EVALUATION_LOOP,
)


@pytest.mark.parametrize("surface", _BLIND, ids=lambda s: s.value)
def test_blind_surface_never_shows_pnl(surface: Surface) -> None:
    """Each blind surface gets no P&L."""
    assert pnl_visible(surface) is False


@pytest.mark.parametrize("surface", _OPERATOR, ids=lambda s: s.value)
def test_operator_surface_shows_pnl(surface: Surface) -> None:
    """Each operator surface gets P&L."""
    assert pnl_visible(surface) is True


def test_every_surface_is_classified() -> None:
    """Every surface is either operator or blind, so none is left undecided."""
    assert set(Surface) == set(_OPERATOR) | set(_BLIND)


@given(surface=st.sampled_from(Surface))
def test_only_the_three_operator_surfaces_see_pnl(surface: Surface) -> None:
    """Visibility holds exactly for the operator allow list."""
    assert pnl_visible(surface) is (surface in _OPERATOR)


def test_visibility_takes_no_override_argument() -> None:
    """The rule has one input, the surface: no date, env or flag can unblind."""
    assert list(inspect.signature(pnl_visible).parameters) == ["surface"]


def test_environment_cannot_unblind_a_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting plausible override variables leaves pushed Telegram blind."""
    monkeypatch.setenv("HERMES_PNL_VISIBLE", "1")
    monkeypatch.setenv("HERMES_REPORTING_PHASE", "certified")
    assert pnl_visible(Surface.TELEGRAM_PUSHED) is False
