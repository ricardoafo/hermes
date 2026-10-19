"""Tests for the Modelo 721 holdings report (change 36-modelo-100-721 R-2, D-7).

Reference: Orden HFP/887/2023; RGAT art. 42 quater.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from hermes.domain.filing.modelo721 import (
    HoldingLine,
    Modelo721Origin,
    Modelo721Rules,
    Obligation,
    PreviousDeclaration,
    UnitTrade,
    YearEndPosition,
    build_modelo721,
    year_end_positions,
)
from hermes.domain.lots.selection import InsufficientInventory
from hermes.domain.trading.order import OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.quantity import Quantity

_RULES = Modelo721Rules(threshold_eur=Decimal("50000"), increase_eur=Decimal("20000"))
_MARCH = datetime(2026, 3, 1, 12, tzinfo=UTC)


def _pos(asset: Currency, units: str) -> YearEndPosition:
    return YearEndPosition(asset=asset, units=Decimal(units), oldest_lot_opened_at=_MARCH)


def _trade(
    ref: int, side: OrderSide, qty: str, at: datetime, asset: Currency = Currency.BTC
) -> UnitTrade:
    return UnitTrade(ref=str(ref), asset=asset, side=side, qty=Decimal(qty), executed_at=at)


class TestYearEndPositions:
    """Units held at the end of the Madrid tax year."""

    def test_units_net_buys_and_sells_and_date_the_oldest_open_lot(self) -> None:
        """Invariant: FIFO sells the oldest lot first, so the oldest open lot is the second BUY."""
        second_buy = _MARCH + timedelta(days=10)
        positions = year_end_positions(
            2026,
            [
                _trade(1, OrderSide.BUY, "1", _MARCH),
                _trade(2, OrderSide.BUY, "2", second_buy),
                _trade(3, OrderSide.SELL, "1.5", _MARCH + timedelta(days=20)),
            ],
        ).unwrap()
        assert positions == (
            YearEndPosition(
                asset=Currency.BTC, units=Decimal("1.5"), oldest_lot_opened_at=second_buy
            ),
        )

    def test_trades_after_the_madrid_year_end_are_ignored(self) -> None:
        """Invariant: a fill at 23:30 UTC on 31 December is next year's and does not count."""
        late = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
        positions = year_end_positions(
            2026, [_trade(1, OrderSide.BUY, "1", _MARCH), _trade(2, OrderSide.SELL, "1", late)]
        ).unwrap()
        assert [(p.asset, p.units) for p in positions] == [(Currency.BTC, Decimal("1"))]

    def test_a_fully_sold_coin_is_not_held(self) -> None:
        """Invariant: zero units means no position."""
        positions = year_end_positions(
            2026,
            [
                _trade(1, OrderSide.BUY, "1", _MARCH),
                _trade(2, OrderSide.SELL, "1", _MARCH + timedelta(days=1)),
            ],
        ).unwrap()
        assert positions == ()

    def test_selling_more_than_held_is_an_error(self) -> None:
        """Invariant: inconsistent trades fail instead of reporting negative units."""
        result = year_end_positions(2026, [_trade(1, OrderSide.SELL, "1", _MARCH)])
        assert result.failure() == InsufficientInventory(
            requested=Quantity(amount=Decimal("1"), asset=Currency.BTC),
            available=Quantity(amount=Decimal("0"), asset=Currency.BTC),
        )


class TestFirstDeclaration:
    """No earlier 721 on file: the €50,000 threshold."""

    def test_total_over_the_threshold_makes_it_due(self) -> None:
        """Invariant: 1 BTC at 50,000.01 EUR crosses the threshold; the line is origin A."""
        report = build_modelo721(
            2026, [_pos(Currency.BTC, "1")], {Currency.BTC: Decimal("50000.01")}, _RULES
        )
        assert (report.obligation, report.total_eur, report.lines[0].origin) == (
            Obligation.REQUIRED,
            Decimal("50000.01"),
            Modelo721Origin.FIRST,
        )

    def test_total_at_the_threshold_is_not_due(self) -> None:
        """Invariant: the rule is 'exceeds', so exactly 50,000 EUR is not due."""
        report = build_modelo721(
            2026, [_pos(Currency.BTC, "1")], {Currency.BTC: Decimal("50000")}, _RULES
        )
        assert report.obligation is Obligation.NOT_REQUIRED

    def test_the_threshold_applies_to_the_total_of_all_coins(self) -> None:
        """Invariant: two coins below the threshold each, above it together, make it due."""
        report = build_modelo721(
            2026,
            [_pos(Currency.BTC, "1"), _pos(Currency.ETH, "10")],
            {Currency.BTC: Decimal("30000"), Currency.ETH: Decimal("2500")},
            _RULES,
        )
        assert (report.total_eur, report.obligation) == (Decimal("55000"), Obligation.REQUIRED)

    def test_missing_price_below_the_threshold_is_undetermined(self) -> None:
        """Invariant: an unknown value could cross the threshold, so the answer is open."""
        report = build_modelo721(
            2026,
            [_pos(Currency.BTC, "1"), _pos(Currency.ETH, "1")],
            {Currency.BTC: Decimal("100")},
            _RULES,
        )
        assert (report.obligation, report.missing_prices, report.complete) == (
            Obligation.UNDETERMINED,
            (Currency.ETH,),
            False,
        )

    def test_known_total_over_the_threshold_is_due_despite_a_missing_price(self) -> None:
        """Invariant: a missing price cannot lower a total already over the threshold."""
        report = build_modelo721(
            2026,
            [_pos(Currency.BTC, "1"), _pos(Currency.ETH, "1")],
            {Currency.BTC: Decimal("60000")},
            _RULES,
        )
        assert report.obligation is Obligation.REQUIRED


class TestLaterDeclaration:
    """An earlier 721 on file: the €20,000 increase rule and extinctions."""

    def _previous(self, **values: str) -> PreviousDeclaration:
        return PreviousDeclaration(
            year=2025, values_eur={Currency(k): Decimal(v) for k, v in values.items()}
        )

    def test_increase_over_20000_makes_it_due_and_marks_lines_m(self) -> None:
        """Invariant: 60,000 to 80,000.01 is more than 20,000 up; a coin declared before is M."""
        report = build_modelo721(
            2026,
            [_pos(Currency.BTC, "1")],
            {Currency.BTC: Decimal("80000.01")},
            _RULES,
            self._previous(BTC="60000"),
        )
        assert (report.obligation, report.lines[0].origin) == (
            Obligation.REQUIRED,
            Modelo721Origin.MODIFIED,
        )

    def test_increase_of_exactly_20000_is_not_due(self) -> None:
        """Invariant: the rule is 'more than', so +20,000 exactly is not due."""
        report = build_modelo721(
            2026,
            [_pos(Currency.BTC, "1")],
            {Currency.BTC: Decimal("80000")},
            _RULES,
            self._previous(BTC="60000"),
        )
        assert report.obligation is Obligation.NOT_REQUIRED

    def test_a_declared_coin_no_longer_held_is_due_as_extinguished(self) -> None:
        """Invariant: a declared coin with no units now gets a C line and makes it due."""
        report = build_modelo721(
            2026,
            [_pos(Currency.BTC, "1")],
            {Currency.BTC: Decimal("60000")},
            _RULES,
            self._previous(BTC="60000", ETH="5000"),
        )
        assert report.obligation is Obligation.REQUIRED
        assert report.lines[1] == HoldingLine(
            asset=Currency.ETH,
            origin=Modelo721Origin.EXTINGUISHED,
            units=Decimal("0"),
            price_eur=None,
            value_eur=Decimal("0"),
            oldest_lot_opened_at=None,
        )

    def test_a_new_coin_after_a_declaration_is_origin_a(self) -> None:
        """Invariant: a coin never declared before is A even when others are M."""
        report = build_modelo721(
            2026,
            [_pos(Currency.BTC, "1"), _pos(Currency.SOL, "1")],
            {Currency.BTC: Decimal("60000"), Currency.SOL: Decimal("100")},
            _RULES,
            self._previous(BTC="60000"),
        )
        assert [(line.asset, line.origin) for line in report.lines] == [
            (Currency.BTC, Modelo721Origin.MODIFIED),
            (Currency.SOL, Modelo721Origin.FIRST),
        ]

    def test_previous_declaration_must_be_for_an_earlier_year(self) -> None:
        """Invariant: a same-year 'previous' declaration is a caller error."""
        same_year = PreviousDeclaration(year=2026, values_eur={Currency.BTC: Decimal("1")})
        with pytest.raises(ValueError, match="not before 2026"):
            build_modelo721(2026, [], {}, _RULES, same_year)


def test_value_is_units_times_price_without_rounding() -> None:
    """Invariant: the line value keeps full precision; rounding is the renderer's job."""
    report = build_modelo721(
        2026, [_pos(Currency.BTC, "0.12345678")], {Currency.BTC: Decimal("81234.567")}, _RULES
    )
    assert report.lines[0].value_eur == Decimal("10028.95806651426")


def test_negative_rule_amounts_are_rejected() -> None:
    """Invariant: thresholds are non-negative."""
    with pytest.raises(ValueError, match=">= 0"):
        Modelo721Rules(threshold_eur=Decimal("-1"), increase_eur=Decimal("0"))
