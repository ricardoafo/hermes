"""Tests for the Modelo 100 transmissions report (change 36-modelo-100-721 R-1).

Reference: ADR-016 items 2, 3 and 8; IRPF art. 35 and 33.5.f.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.filing.modelo100 import (
    CoinSummary,
    ConsiderationKind,
    DisposalPiece,
    PendingSale,
    PurchaseRecord,
    build_modelo100,
)
from hermes.domain.value_objects.currency import Currency

_JUNE = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
_JAN = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def _piece(
    sell: str,
    *,
    proceeds: str | None = "1000",
    cost: str | None = "800",
    fee: str | None = "10",
    asset: Currency = Currency.BTC,
    received: Currency = Currency.USDT,
    at: datetime = _JUNE,
    buy: str = "b1",
    acquired: datetime = _JAN,
    qty: str = "1",
) -> DisposalPiece:
    return DisposalPiece(
        sell_ref=sell,
        asset=asset,
        received=received,
        disposed_at=at,
        buy_ref=buy,
        acquired_at=acquired,
        qty=Decimal(qty),
        proceeds_eur=None if proceeds is None else Decimal(proceeds),
        cost_eur=None if cost is None else Decimal(cost),
        fee_eur=None if fee is None else Decimal(fee),
    )


class TestTransmissionValues:
    """ADR-016 item 3: fees inside both values."""

    def test_sale_values_net_the_sell_fee_and_keep_the_lot_cost(self) -> None:
        """Invariant: transmission = proceeds - fee; acquisition = cost; gain = difference."""
        report = build_modelo100(2026, [_piece("s1")])
        (t,) = report.transmissions
        assert (t.transmission_eur, t.acquisition_eur, t.gain_eur) == (
            Decimal("990"),
            Decimal("800"),
            Decimal("190"),
        )

    def test_a_sale_across_two_lots_is_one_transmission(self) -> None:
        """Invariant: pieces of one SELL add up to one line with the oldest lot's date."""
        report = build_modelo100(
            2026,
            [
                _piece("s1", proceeds="600", cost="500", fee="6", buy="b1", qty="0.6"),
                _piece(
                    "s1",
                    proceeds="400",
                    cost="450",
                    fee="4",
                    buy="b2",
                    qty="0.4",
                    acquired=_JAN + timedelta(days=30),
                ),
            ],
        )
        (t,) = report.transmissions
        assert (t.qty, t.transmission_eur, t.acquisition_eur, t.first_acquired_at, t.buy_refs) == (
            Decimal("1.0"),
            Decimal("990"),
            Decimal("950"),
            _JAN,
            ("b1", "b2"),
        )


class TestYearAndPending:
    """ADR-016 items 2 and 5."""

    def test_sales_outside_the_madrid_year_are_ignored(self) -> None:
        """Invariant: a fill at 23:30 UTC on 31 December belongs to the next year."""
        late = datetime(2026, 12, 31, 23, 30, tzinfo=UTC)
        report = build_modelo100(2026, [_piece("s1"), _piece("s2", at=late)])
        assert [t.sell_ref for t in report.transmissions] == ["s1"]

    def test_a_sale_with_a_pending_value_is_listed_and_left_out_of_totals(self) -> None:
        """Invariant: one unpriced piece makes the whole sale pending, and the year incomplete."""
        report = build_modelo100(
            2026,
            [_piece("s1"), _piece("s2", buy="b1", fee=None, qty="0.5"), _piece("s2", buy="b2")],
        )
        assert (report.pending, report.gains_eur, report.complete) == (
            (
                PendingSale(
                    sell_ref="s2", asset=Currency.BTC, disposed_at=_JUNE, qty=Decimal("1.5")
                ),
            ),
            Decimal("190"),
            False,
        )


class TestSummaries:
    """Change D-3 and D-4."""

    def test_lines_group_by_coin_and_kind_of_consideration(self) -> None:
        """Invariant: one line per (coin, kind); sales for EUR and for USDT stay apart."""
        report = build_modelo100(
            2026,
            [
                _piece("s1"),
                _piece("s2", proceeds="500", cost="700", fee="5"),
                _piece("s3", received=Currency.EUR),
                _piece("s4", asset=Currency.ETH),
            ],
        )
        assert report.summaries == (
            CoinSummary(
                asset=Currency.BTC,
                kind=ConsiderationKind.FIAT,
                sales=1,
                qty=Decimal("1"),
                transmission_eur=Decimal("990"),
                acquisition_eur=Decimal("800"),
                gain_eur=Decimal("190"),
            ),
            CoinSummary(
                asset=Currency.BTC,
                kind=ConsiderationKind.VIRTUAL_CURRENCY,
                sales=2,
                qty=Decimal("2"),
                transmission_eur=Decimal("1485"),
                acquisition_eur=Decimal("1500"),
                gain_eur=Decimal("-15"),
            ),
            CoinSummary(
                asset=Currency.ETH,
                kind=ConsiderationKind.VIRTUAL_CURRENCY,
                sales=1,
                qty=Decimal("1"),
                transmission_eur=Decimal("990"),
                acquisition_eur=Decimal("800"),
                gain_eur=Decimal("190"),
            ),
        )

    def test_gains_and_losses_are_totalled_separately_and_netted(self) -> None:
        """Invariant: gains sum the winners, losses the losers, net is their difference."""
        report = build_modelo100(
            2026, [_piece("s1"), _piece("s2", proceeds="500", cost="700", fee="5")]
        )
        assert (report.gains_eur, report.losses_eur, report.net_eur) == (
            Decimal("190"),
            Decimal("205"),
            Decimal("-15"),
        )

    def test_stablecoin_is_a_virtual_currency_and_euro_is_fiat(self) -> None:
        """Invariant: only legal tender counts as fiat consideration."""
        assert (
            ConsiderationKind.of(Currency.USDT),
            ConsiderationKind.of(Currency.USDC),
            ConsiderationKind.of(Currency.EUR),
        ) == (
            ConsiderationKind.VIRTUAL_CURRENCY,
            ConsiderationKind.VIRTUAL_CURRENCY,
            ConsiderationKind.FIAT,
        )


class TestRepurchaseFlag:
    """ADR-016 item 8: flag, never defer."""

    def _loss(self) -> DisposalPiece:
        return _piece("s1", proceeds="500", cost="700", fee="5", buy="b1")

    def test_loss_with_a_purchase_within_two_months_is_flagged(self) -> None:
        """Invariant: a same-coin BUY up to two months after a loss sale raises a flag."""
        buy = PurchaseRecord(ref="b9", asset=Currency.BTC, acquired_at=_JUNE + timedelta(days=50))
        report = build_modelo100(2026, [self._loss()], [buy])
        assert [(f.sell_ref, f.loss_eur, f.purchase_refs) for f in report.flags] == [
            ("s1", Decimal("205"), ("b9",))
        ]

    def test_the_flag_does_not_change_the_loss(self) -> None:
        """Invariant: the loss still counts in full in the totals."""
        buy = PurchaseRecord(ref="b9", asset=Currency.BTC, acquired_at=_JUNE)
        report = build_modelo100(2026, [self._loss()], [buy])
        assert report.losses_eur == Decimal("205")

    def test_the_lot_the_sale_consumed_is_not_a_repurchase(self) -> None:
        """Invariant: the BUY whose lot was sold is excluded from the window."""
        own = PurchaseRecord(ref="b1", asset=Currency.BTC, acquired_at=_JUNE - timedelta(days=5))
        assert build_modelo100(2026, [self._loss()], [own]).flags == ()

    def test_purchases_outside_the_window_or_of_other_coins_do_not_flag(self) -> None:
        """Invariant: only the same coin within two calendar months either side counts.

        The sale is 15 June, Madrid; 00:00 on 16 August, Madrid (22:00 UTC on
        15 August) is the first instant past the window's last date.
        """
        purchases = [
            PurchaseRecord(
                ref="b7",
                asset=Currency.BTC,
                acquired_at=datetime(2026, 8, 15, 22, 0, tzinfo=UTC),
            ),
            PurchaseRecord(ref="b8", asset=Currency.ETH, acquired_at=_JUNE),
        ]
        assert build_modelo100(2026, [self._loss()], purchases).flags == ()

    def test_a_gain_is_never_flagged(self) -> None:
        """Invariant: the rule only concerns losses."""
        buy = PurchaseRecord(ref="b9", asset=Currency.BTC, acquired_at=_JUNE)
        assert build_modelo100(2026, [_piece("s1")], [buy]).flags == ()


class TestRepurchaseWindowEdges:
    """Issue #1171: the window counts Madrid calendar dates, never the time of day.

    The loss sale is 12:00 UTC on 15 June 2026, 14:00 in Madrid (CEST, UTC+2),
    so the window runs from 15 April to 15 August, Madrid dates, both included.
    """

    def _flagged(self, sold_at: datetime, bought_at: datetime) -> bool:
        sale = _piece("s1", proceeds="500", cost="700", fee="5", buy="b1", at=sold_at)
        buy = PurchaseRecord(ref="b9", asset=Currency.BTC, acquired_at=bought_at)
        return [f.purchase_refs for f in build_modelo100(2026, [sale], [buy]).flags] == [("b9",)]

    def test_a_buy_late_on_the_last_day_is_inside(self) -> None:
        """23:59:59 Madrid on 15 August is after the sale's time of day, and still flags."""
        assert self._flagged(_JUNE, datetime(2026, 8, 15, 21, 59, 59, tzinfo=UTC)) is True

    def test_a_buy_at_midnight_after_the_last_day_is_outside(self) -> None:
        """00:00 Madrid on 16 August (22:00 UTC on 15 August) is past the window."""
        assert self._flagged(_JUNE, datetime(2026, 8, 15, 22, 0, tzinfo=UTC)) is False

    def test_a_buy_at_midnight_on_the_first_day_is_inside(self) -> None:
        """00:00 Madrid on 15 April (22:00 UTC on 14 April) opens the window."""
        assert self._flagged(_JUNE, datetime(2026, 4, 14, 22, 0, tzinfo=UTC)) is True

    def test_a_buy_just_before_the_first_day_is_outside(self) -> None:
        """23:59:59 Madrid on 14 April is the day before the window."""
        assert self._flagged(_JUNE, datetime(2026, 4, 14, 21, 59, 59, tzinfo=UTC)) is False

    def test_a_sale_after_midnight_madrid_moves_the_window_a_day(self) -> None:
        """22:30 UTC on 15 June is 00:30 on 16 June in Madrid, so 16 August is inside.

        Read on the UTC date the window would end on 15 August; noon UTC on
        16 August would then be outside.
        """
        sold_at = datetime(2026, 6, 15, 22, 30, tzinfo=UTC)
        assert self._flagged(sold_at, datetime(2026, 8, 16, 12, 0, tzinfo=UTC)) is True


_cents = st.decimals(min_value=Decimal("0"), max_value=Decimal("100000"), places=2)


@settings(max_examples=200)
@given(
    st.lists(
        st.tuples(_cents, _cents, _cents, st.sampled_from([Currency.EUR, Currency.USDT])),
        min_size=1,
        max_size=12,
    )
)
def test_every_line_and_the_year_balance(
    sales: list[tuple[Decimal, Decimal, Decimal, Currency]],
) -> None:
    """Property: per line transmission - acquisition = gain; net = gains - losses = sum of lines."""
    pieces = [
        _piece(f"s{i}", proceeds=str(p), cost=str(c), fee=str(f), received=q)
        for i, (p, c, f, q) in enumerate(sales)
    ]
    report = build_modelo100(2026, pieces)
    assert all(t.transmission_eur - t.acquisition_eur == t.gain_eur for t in report.transmissions)
    assert all(s.transmission_eur - s.acquisition_eur == s.gain_eur for s in report.summaries)
    assert report.net_eur == sum((s.gain_eur for s in report.summaries), start=Decimal("0"))
    assert report.net_eur == sum(p - f - c for p, c, f, _ in sales)
