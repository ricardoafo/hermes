"""The live home screen draws card by card (#546, change live-home-screen D-6).

Invariants:

- a view the read model cannot serve yet turns only its own card into a plain
  "not available yet" line; every other card still shows its content;
- a ledger read failure in one card names the failure in that card and does
  not stop the app;
- a failing heartbeat leaves the rest of the screen drawn;
- a failing market watch is one line and the positions above it still show (#650).
"""

from __future__ import annotations

import asyncio

from hermes.adapters.console import FixtureReadModel
from hermes.application.console import (
    ActivityDay,
    ConsoleReadModel,
    Heartbeat,
    PortfolioRange,
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    RuleCheck,
    ShadowTest,
    ViewNotAvailableError,
    WatchedPair,
)
from hermes.domain.errors import LedgerError
from hermes.entrypoints.tui import HomeApp

_CARDS = ("heartbeat", "portfolio", "positions", "market", "activity", "shadow")


class _Failing:
    """A demo scenario whose portfolio, shadow test or heartbeat raise a given error."""

    def __init__(self, **errors: Exception) -> None:
        self._base = FixtureReadModel.load("ok-two-positions")
        self._errors = errors

    def _raise(self, view: str) -> None:
        error = self._errors.get(view)
        if error is not None:
            raise error

    @property
    def demo_scenario(self) -> str | None:
        return None

    def heartbeat(self) -> Heartbeat:
        self._raise("heartbeat")
        return self._base.heartbeat()

    def portfolio(self, range_: PortfolioRange = PortfolioRange.TODAY) -> PortfolioSummary:
        self._raise("portfolio")
        return self._base.portfolio(range_)

    def positions(self) -> tuple[PositionRow, ...]:
        return self._base.positions()

    def position(self, coin: str) -> PositionDetail | None:
        return self._base.position(coin)

    def activity(self, *, include_quiet: bool = False) -> tuple[ActivityDay, ...]:
        return self._base.activity(include_quiet=include_quiet)

    def rule_checks(self) -> tuple[RuleCheck, ...]:
        return self._base.rule_checks()

    def shadow_test(self) -> ShadowTest:
        self._raise("shadow")
        return self._base.shadow_test()

    def market_watch(self) -> tuple[WatchedPair, ...]:
        self._raise("market")
        return self._base.market_watch()


def _model(**errors: Exception) -> ConsoleReadModel:
    return _Failing(**errors)


def _texts(model: ConsoleReadModel) -> dict[str, str]:
    async def drive() -> dict[str, str]:
        app = HomeApp(model)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            return {card: app.plain_text(card).strip() for card in _CARDS}

    return asyncio.run(drive())


def _first_line(text: str) -> str:
    return text.splitlines()[0] if text else ""


def test_unavailable_portfolio_is_one_line_and_the_rest_stands() -> None:
    """The portfolio card says "not available yet"; positions and shadow still render (A-4.2)."""
    texts = _texts(_model(portfolio=ViewNotAvailableError("no mark yet")))
    assert (
        texts["portfolio"],
        "ETH" in texts["positions"],
        _first_line(texts["shadow"]).startswith("Shadow test"),
    ) == ("Portfolio · not available yet", True, True)


def test_ledger_failure_is_named_in_its_card() -> None:
    """A failed read shows in the shadow card only; the portfolio still has its total."""
    texts = _texts(_model(shadow=LedgerError(msg="database is locked")))
    assert (texts["shadow"], "€" in texts["portfolio"]) == (
        "Shadow test · could not read the ledger: database is locked",
        True,
    )


def test_failing_heartbeat_keeps_the_screen() -> None:
    """A heartbeat that cannot be read is one line; the positions are still listed."""
    texts = _texts(_model(heartbeat=LedgerError(msg="disk I/O error")))
    assert (texts["heartbeat"], "ETH" in texts["positions"]) == (
        "Heartbeat · could not read the ledger: disk I/O error",
        True,
    )


def test_failing_market_watch_is_one_line_and_positions_stand() -> None:
    """A ledger failure in the market watch names it there; positions still list ETH."""
    texts = _texts(_model(market=LedgerError(msg="disk I/O error")))
    assert (texts["market"], "ETH" in texts["positions"]) == (
        "Market watch · could not read the ledger: disk I/O error",
        True,
    )


def test_a_key_shows_and_folds_the_check_lines_in_the_activity_card() -> None:
    """INV: A toggles the quiet 4h check lines; they are folded until asked for (#653)."""
    from datetime import UTC, date, datetime  # noqa: PLC0415 - local to this test

    from hermes.application.console import ActivityKind  # noqa: PLC0415
    from hermes.application.console.views import ActivityItem  # noqa: PLC0415

    check = ActivityItem(
        at=datetime(2026, 10, 3, 12, tzinfo=UTC),
        kind=ActivityKind.BAR,
        text="checked 3 coins · no signal",
        count=3,
    )

    class _Quiet(_Failing):
        def activity(self, *, include_quiet: bool = False) -> tuple[ActivityDay, ...]:
            return (ActivityDay(day=date(2026, 10, 3), items=(check,) if include_quiet else ()),)

    async def drive() -> tuple[str, str, str]:
        app = HomeApp(_Quiet())
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            folded = app.plain_text("activity")
            await pilot.press("a")
            await pilot.pause()
            shown = app.plain_text("activity")
            await pilot.press("a")
            await pilot.pause()
            return folded, shown, app.plain_text("activity")

    folded, shown, folded_again = asyncio.run(drive())
    assert ("checked 3 coins · no signal" in folded, "checked 3 coins · no signal" in shown) == (
        False,
        True,
    )
    assert "checked 3 coins" not in folded_again
