"""Which surfaces may show shadow P&L.

The operator sees shadow money on the console he pulls himself: the CLI, the
TUI and Telegram replies to his own commands. Every other surface is blind:
pushed Telegram messages, Slack, reports, MCP tools, the supervisor desk, the
challenger lab and the ADR-012 evaluation loops. The rule is a closed allow
list with no date, environment or configuration override, so a new surface is
blind until a reviewed change adds it here.

Reference: openspec/changes/console-read-model/change.md (D-5); Code Owner
ruling 2026-10-03.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = ["OPERATOR_SURFACES", "Surface", "pnl_visible"]


class Surface(StrEnum):
    """Every place console data can be shown."""

    OPERATOR_CLI = "operator_cli"
    OPERATOR_TUI = "operator_tui"
    TELEGRAM_PULLED = "telegram_pulled"
    TELEGRAM_PUSHED = "telegram_pushed"
    SLACK = "slack"
    REPORT = "report"
    MCP_TOOL = "mcp_tool"
    SUPERVISOR_DESK = "supervisor_desk"
    CHALLENGER_LAB = "challenger_lab"
    EVALUATION_LOOP = "evaluation_loop"


OPERATOR_SURFACES: Final[frozenset[Surface]] = frozenset(
    {Surface.OPERATOR_CLI, Surface.OPERATOR_TUI, Surface.TELEGRAM_PULLED}
)


def pnl_visible(surface: Surface) -> bool:
    """Return whether ``surface`` may show P&L, prices of open positions or values.

    Args:
        surface: Where the rendered output goes.

    Returns:
        True only for the operator's own pulled surfaces.
    """
    return surface in OPERATOR_SURFACES
