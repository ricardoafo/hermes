"""Every console screen, keyed by name, for parametrised renderer tests."""

from collections.abc import Callable
from typing import Final

from rich.console import RenderableType

from hermes.adapters.console import FixtureReadModel, available_scenarios
from hermes.application.console import ConsoleReadModel, PortfolioRange, Surface
from hermes.application.console.render import (
    RenderContext,
    render_activity,
    render_heartbeat,
    render_market_watch,
    render_overview,
    render_portfolio,
    render_position_detail,
    render_positions,
    render_shadow_test,
    to_plain_text,
)

type Screen = Callable[[ConsoleReadModel, RenderContext], RenderableType]


def _detail(model: ConsoleReadModel, ctx: RenderContext) -> RenderableType:
    rows = model.positions()
    return render_position_detail(model.position(rows[0].coin) if rows else None, ctx)


SCREENS: Final[dict[str, Screen]] = {
    "heartbeat": lambda m, c: render_heartbeat(m.heartbeat(), c),
    "portfolio": lambda m, c: render_portfolio(m.portfolio(PortfolioRange.TODAY), c),
    "portfolio-year": lambda m, c: render_portfolio(m.portfolio(PortfolioRange.YEAR), c),
    "positions": lambda m, c: render_positions(m.positions(), c),
    "position-detail": _detail,
    "activity": lambda m, c: render_activity(m.activity(), c),
    "shadow-test": lambda m, c: render_shadow_test(m.shadow_test(), c),
    "market-watch": lambda m, c: render_market_watch(m.market_watch(), c),
    "overview": render_overview,
}

SCENARIOS: Final = available_scenarios()
WIDTHS: Final = (80, 120)


def render(scenario: str, screen: str, *, surface: Surface, width: int = 80) -> str:
    """Render ``screen`` for ``scenario`` on ``surface`` as plain text."""
    model = FixtureReadModel.load(scenario)
    ctx = RenderContext.for_model(model, surface=surface, width=width)
    return to_plain_text(SCREENS[screen](model, ctx), width=width)
