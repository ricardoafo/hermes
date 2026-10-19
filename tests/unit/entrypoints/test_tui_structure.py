"""``HomeApp`` stays the router: its painters only ever move out (issue #1184).

Invariant: the ``_paint*`` methods on ``HomeApp`` are a fixed list that only
shrinks. A new screen's painter goes in a controller next to its
``tui_<screen>.py``, not on the app class.
"""

import ast
from pathlib import Path
from typing import Final

_TUI: Final = Path(__file__).parents[3] / "src" / "hermes" / "entrypoints" / "tui.py"
_ALLOWED: Final = frozenset(
    {
        "_paint",
        "_paint_activity",
        "_paint_chip",
        "_paint_detail",
        "_paint_feed_and_clock",
        "_paint_heartbeat",
        "_paint_heartbeat_read",
        "_paint_live",
        "_paint_mainnet",
        "_paint_market",
        "_paint_market_read",
        "_paint_portfolio",
        "_paint_portfolio_read",
        "_paint_positions",
        "_paint_positions_read",
        "_paint_positions_tab",
        "_paint_release",
        "_paint_run_line",
        "_paint_shadow",
        "_paint_status_badges",
        "_paint_tab_screen",
        "_paint_tax",
        "_paint_third_column",
    }
)


def _home_app_painters() -> set[str]:
    tree = ast.parse(_TUI.read_text(encoding="utf-8"))
    home = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HomeApp")
    return {
        node.name
        for node in home.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("_paint")
    }


def test_home_app_gains_no_new_painter() -> None:
    """A painter on ``HomeApp`` that is not on the shrinking list is a new one."""
    assert _home_app_painters() - _ALLOWED == set()


def test_the_system_painter_left_home_app() -> None:
    """The System tab draws through its controller, so ``HomeApp`` has no System painter."""
    assert "_paint_system" not in _home_app_painters()
