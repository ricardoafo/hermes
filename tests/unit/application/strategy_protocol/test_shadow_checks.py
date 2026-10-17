"""Tests for serve's daily shadow checks (#796).

Invariants protected here:

- one equity point per UTC day, the day's last portfolio mark;
- the SA-9 repeat kill fires when one overlay rule halted entries 3 or more
  times inside the 30 days before the checked day ends, and only then;
- the Sharpe kill fires once the 30-day rolling Sharpe stayed below 0.3 for
  60 consecutive days, counted over the whole history;
- the drift rule breaches when the median drift size of the last 7 days'
  fills is above 30 bps, whatever the drift's direction, and never without a
  measured fill; v1's exits, priced at the stop by its model, stay out.

The thresholds are the signed ones (pre-registration-v1.md §10), read from
:class:`PreRegistration`, so a change to them fails these tests.
"""

import dataclasses
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.application.strategy_protocol.shadow_checks import (
    SA9_REPEAT_RULE,
    daily_equity,
    day_end,
    run_shadow_checks,
)
from hermes.domain.events.fill_quality_recorded import BookSource, FillQualityRecorded
from hermes.domain.events.risk_halt_recorded import HaltKind, RiskHaltRecorded
from hermes.domain.events.trade_executed import TradeSide
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_SID = StrategyId("swing-donchian-4h")
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_CID = "01J9Z8T3M5BQR2FVKN4XWY6PH1"
_DAY = date(2026, 12, 1)
_END = day_end(_DAY)
_TRIGGERS = PreRegistration().kill_triggers()


def _mark(at: datetime, equity: float) -> PortfolioMark:
    """Return a flat mark whose equity is ``equity`` USDT."""
    snapshot = PortfolioSnapshot.seed(
        _SID, Money(amount=Decimal(str(equity)), currency=Currency.USDT)
    )
    return PortfolioMark(snapshot=snapshot, pair=_BTC, interval=KlineInterval.H4, bar_close_time=at)


def _halt(at: datetime, rule: str = "RO-04", kind: HaltKind = HaltKind.HALT) -> RiskHaltRecorded:
    """Return a recorded overlay halt of ``rule`` at ``at``."""
    return RiskHaltRecorded(
        occurred_at=at,
        correlation_id=_CID,
        strategy_id=_SID,
        pair=_BTC,
        kind=kind,
        rule=rule,
        reason="stale_orderbook",
        pre_registration_ref="§10",
    )


def _fill(at: datetime, drift: str | None, side: TradeSide = TradeSide.BUY) -> FillQualityRecorded:
    """Return a fill-quality row with ``drift`` bps (``None``: no model price)."""
    return FillQualityRecorded(
        occurred_at=at,
        correlation_id=_CID,
        strategy_id=_SID,
        pair=_BTC,
        side=side,
        qty=Decimal("0.001"),
        book_source=BookSource.LIVE,
        book_time=at,
        fill_price=Decimal("84833.12"),
        model_price=None if drift is None else Decimal("84830"),
        drift_bps=None if drift is None else Decimal(drift),
        book_mid=Decimal("84833.115"),
        slippage=Decimal("0.0000001"),
    )


def _check(
    *,
    equity: tuple[tuple[date, float], ...] = (),
    halts: tuple[RiskHaltRecorded, ...] = (),
    fills: tuple[FillQualityRecorded, ...] = (),
    day: date = _DAY,
) -> tuple[bool, str, bool]:
    """Run the checks and return ``(killed, kill rule, drift breached)``."""
    result = run_shadow_checks(day, equity=equity, halts=halts, fills=fills, triggers=_TRIGGERS)
    return result.kill.triggered, result.kill.rule, result.drift_breached


def _declining(days: int) -> tuple[tuple[date, float], ...]:
    """Return ``days`` daily equity points falling 1% and 0.5% in turn (Sharpe far below 0.3)."""
    start = _DAY - timedelta(days=days - 1)
    points: list[tuple[date, float]] = []
    value = 5000.0
    for i in range(days):
        points.append((start + timedelta(days=i), value))
        value *= 0.99 if i % 2 == 0 else 0.995
    return tuple(points)


def test_daily_equity_keeps_the_last_mark_of_each_utc_day() -> None:
    """Three marks on one day and one on the next give two points, each the day's last."""
    marks = (
        _mark(datetime(2026, 12, 1, 3, 59, 59, 999000, tzinfo=UTC), 5000.0),
        _mark(datetime(2026, 12, 1, 19, 59, 59, 999000, tzinfo=UTC), 5010.0),
        _mark(datetime(2026, 12, 1, 23, 59, 59, 999000, tzinfo=UTC), 5020.0),
        _mark(datetime(2026, 12, 2, 3, 59, 59, 999000, tzinfo=UTC), 4990.0),
    )
    assert daily_equity(marks) == ((date(2026, 12, 1), 5020.0), (date(2026, 12, 2), 4990.0))


def test_one_overlay_rule_halting_three_times_in_30_days_kills() -> None:
    """Three RO-04 halts inside the window fire the SA-9 repeat kill (§10)."""
    halts = tuple(_halt(_END - timedelta(days=d)) for d in (1, 10, 29))
    assert _check(halts=halts) == (True, SA9_REPEAT_RULE, False)


@pytest.mark.parametrize(
    "halts",
    [
        pytest.param((_halt(_END - timedelta(days=1)), _halt(_END - timedelta(days=2))), id="two"),
        pytest.param(
            (
                _halt(_END - timedelta(days=1), "RO-04"),
                _halt(_END - timedelta(days=2), "RO-07"),
                _halt(_END - timedelta(days=3), "RO-01"),
            ),
            id="three-rules-once-each",
        ),
        pytest.param(
            (
                _halt(_END - timedelta(days=1)),
                _halt(_END - timedelta(days=2)),
                _halt(_END - timedelta(days=30, seconds=1)),
            ),
            id="third-outside-window",
        ),
        pytest.param(
            (
                _halt(_END - timedelta(days=1)),
                _halt(_END - timedelta(days=2)),
                _halt(_END + timedelta(hours=1)),
            ),
            id="third-after-the-day",
        ),
        pytest.param(
            tuple(
                _halt(_END - timedelta(days=d), "max_drawdown", HaltKind.KILL) for d in (1, 2, 3)
            ),
            id="kills-are-not-overlay-halts",
        ),
    ],
)
def test_fewer_than_three_fires_of_one_rule_in_the_window_do_not_kill(
    halts: tuple[RiskHaltRecorded, ...],
) -> None:
    """Only one SA-9 rule's own HALT rows inside the 30 days count toward the 3."""
    assert _check(halts=halts) == (False, "", False)


def test_sixty_days_below_the_sharpe_floor_kill() -> None:
    """Day 90 is the 60th day with a 30-day Sharpe below 0.3: the shadow Sharpe kill fires."""
    assert _check(equity=_declining(90)) == (True, "shadow_sharpe", False)


def test_fifty_nine_days_below_the_sharpe_floor_do_not_kill() -> None:
    """One day short of the 60-day streak, nothing fires."""
    assert _check(equity=_declining(89)) == (False, "", False)


def test_the_sa9_kill_is_reported_first_when_both_fire() -> None:
    """With both kills due, the result names the SA-9 repeat rule."""
    halts = tuple(_halt(_END - timedelta(days=d)) for d in (1, 2, 3))
    assert _check(equity=_declining(90), halts=halts)[1] == SA9_REPEAT_RULE


def test_a_drift_median_above_30_bps_breaches_without_killing() -> None:
    """Median 31 bps over the window breaches the drift rule; no kill fires."""
    fills = tuple(_fill(_END - timedelta(days=d), v) for d, v in ((1, "20"), (2, "31"), (3, "40")))
    assert _check(fills=fills) == (False, "", True)


@pytest.mark.parametrize(
    "fills",
    [
        pytest.param((), id="no-fills"),
        pytest.param((_fill(_END - timedelta(days=1), None),), id="no-model-price"),
        pytest.param((_fill(_END - timedelta(days=1), "30"),), id="exactly-30"),
        pytest.param((_fill(_END - timedelta(days=7, seconds=1), "90"),), id="older-than-7-days"),
        pytest.param((_fill(_END + timedelta(hours=1), "90"),), id="after-the-day"),
    ],
)
def test_no_measured_fill_above_the_limit_inside_the_window_does_not_breach(
    fills: tuple[FillQualityRecorded, ...],
) -> None:
    """The limit is strict, the window is the 7 days before the day ends, and needs a drift."""
    assert _check(fills=fills)[2] is False


@given(drifts=st.lists(st.integers(min_value=-200, max_value=200), min_size=1, max_size=9))
def test_the_drift_rule_ignores_direction(drifts: list[int]) -> None:
    """Flipping the sign of every drift never changes the verdict."""
    at = _END - timedelta(hours=1)
    straight = tuple(_fill(at, str(d)) for d in drifts)
    flipped = tuple(_fill(at, str(-d)) for d in drifts)
    assert _check(fills=straight)[2] == _check(fills=flipped)[2]


def test_v1_exits_stay_out_of_the_drift_median_and_are_counted() -> None:
    """v1 models a stop exit at the stop: exit drift never breaches and is reported as left out."""
    at = _END - timedelta(hours=1)
    fills = (_fill(at, "4"), _fill(at, "380", TradeSide.SELL), _fill(at, "-250", TradeSide.SELL))
    result = run_shadow_checks(_DAY, equity=(), halts=(), fills=fills, triggers=_TRIGGERS)
    assert (result.drift_breached, result.exits_left_out) == (False, 2)


def test_exits_count_when_the_pre_registration_prices_them_at_market() -> None:
    """With exits counted (a v2-style exit model), the same fills breach and none are left out."""
    at = _END - timedelta(hours=1)
    fills = (_fill(at, "4"), _fill(at, "380", TradeSide.SELL), _fill(at, "-250", TradeSide.SELL))
    triggers = dataclasses.replace(_TRIGGERS, shadow_drift_counts_exits=True)
    result = run_shadow_checks(_DAY, equity=(), halts=(), fills=fills, triggers=triggers)
    assert (result.drift_breached, result.exits_left_out) == (True, 0)
