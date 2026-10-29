"""Tests for the DailyTradeDigest domain event and its TradeSummary payload.

Verifies:
- Schema version is 1.
- Frozen semantics on both classes.
- Winners / losers tuples are preserved verbatim.
- Empty winners / losers tuples are legal (a slow day).

Reference: code-conventions.md §10 + §3.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hermes.domain.events.daily_trade_digest import DailyTradeDigest, TradeSummary
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair


def _summary(*, pnl_amount: Decimal, base: Currency = Currency.BTC) -> TradeSummary:
    """Return a deterministic TradeSummary for reuse across tests."""
    return TradeSummary(
        pair=Pair(base=base, quote=Currency.USDT),
        pnl=Money(amount=pnl_amount, currency=Currency.EUR),
    )


def _sample_event(
    *,
    winners: tuple[TradeSummary, ...] = (),
    losers: tuple[TradeSummary, ...] = (),
) -> DailyTradeDigest:
    """Return a deterministic DailyTradeDigest instance for reuse across tests."""
    return DailyTradeDigest(
        occurred_at=datetime(2026, 8, 23, 23, 59, 0, tzinfo=UTC),
        correlation_id="01J9Z8T3M5BQR2FVKN4XWY6PH1",
        day=date(2026, 8, 23),
        total_trades=12,
        filled=10,
        rejected=2,
        total_notional=Money(amount=Decimal("12500.50"), currency=Currency.EUR),
        top_winners=winners,
        top_losers=losers,
    )


class TestDailyTradeDigestSchema:
    """Verify the event declares the required schema version."""

    def test_schema_version_is_one(self) -> None:
        """DailyTradeDigest.__event_schema_version__ pins to 1 for the initial release."""
        assert DailyTradeDigest.__event_schema_version__ == 1


class TestDailyTradeDigestInstantiation:
    """Verify construction populates every field and is frozen."""

    def test_all_fields_are_stored(self) -> None:
        """Every constructor argument reaches the instance with the exact value passed."""
        winner = _summary(pnl_amount=Decimal("100.00"))
        loser = _summary(pnl_amount=Decimal("-25.00"), base=Currency.ETH)

        event = _sample_event(winners=(winner,), losers=(loser,))

        assert event.day == date(2026, 8, 23)
        assert event.total_trades == 12
        assert event.filled == 10
        assert event.rejected == 2
        assert event.total_notional.amount == Decimal("12500.50")
        assert event.top_winners == (winner,)
        assert event.top_losers == (loser,)

    def test_event_is_frozen(self) -> None:
        """DailyTradeDigest is a frozen dataclass — mutation raises."""
        event = _sample_event()
        with pytest.raises((AttributeError, TypeError)):
            event.total_trades = 999  # type: ignore[misc]

    def test_empty_winners_and_losers_are_legal(self) -> None:
        """A digest with no winners or losers (slow day) constructs without error."""
        event = _sample_event()
        assert event.top_winners == ()
        assert event.top_losers == ()

    def test_equality_is_by_value(self) -> None:
        """Two DailyTradeDigest instances with identical fields compare equal."""
        winner = _summary(pnl_amount=Decimal("42.00"))
        assert _sample_event(winners=(winner,)) == _sample_event(winners=(winner,))


class TestTradeSummary:
    """Nested TradeSummary is frozen and equal-by-value."""

    def test_summary_is_frozen(self) -> None:
        """TradeSummary is a frozen dataclass — mutation raises."""
        summary = _summary(pnl_amount=Decimal("10"))
        with pytest.raises((AttributeError, TypeError)):
            summary.pnl = Money(amount=Decimal("0"), currency=Currency.EUR)  # type: ignore[misc]

    def test_summary_allows_negative_pnl(self) -> None:
        """A losing trade summary accepts a negative Money PnL — losers are legal."""
        summary = _summary(pnl_amount=Decimal("-15.50"))
        assert summary.pnl.amount == Decimal("-15.50")

    def test_summary_equality_is_by_value(self) -> None:
        """Two TradeSummary instances with identical fields compare equal."""
        assert _summary(pnl_amount=Decimal("1")) == _summary(pnl_amount=Decimal("1"))
