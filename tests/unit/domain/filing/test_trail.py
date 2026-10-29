"""Tests for the tax pack trail (change 568-tax-pack R-2).

Reference: ADR-016 item 3.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hermes.domain.filing.modelo100 import DisposalPiece, build_modelo100
from hermes.domain.filing.trail import TradeRate, build_trail
from hermes.domain.value_objects.currency import Currency

_JUNE = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
_JAN = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def _piece(
    sell: str,
    *,
    proceeds: str | None = "1000",
    cost: str | None = "800",
    fee: str | None = "10",
    buy: str = "b1",
) -> DisposalPiece:
    return DisposalPiece(
        sell_ref=sell,
        asset=Currency.BTC,
        received=Currency.USDT,
        disposed_at=_JUNE,
        buy_ref=buy,
        acquired_at=_JAN,
        qty=Decimal("1"),
        proceeds_eur=None if proceeds is None else Decimal(proceeds),
        cost_eur=None if cost is None else Decimal(cost),
        fee_eur=None if fee is None else Decimal(fee),
    )


def test_gain_is_proceeds_minus_sell_fee_minus_acquisition() -> None:
    """Invariant: each line's gain nets the sell fee and the lot's acquisition value."""
    (line,) = build_trail([_piece("s1")], {})
    assert line.gain_eur == Decimal("190")


def test_a_pending_value_leaves_the_gain_unknown() -> None:
    """Invariant: a piece with any pending EUR value has no gain, never a guessed one."""
    (line,) = build_trail([_piece("s1", fee=None)], {})
    assert (line.sell_fee_eur, line.gain_eur) == (None, None)


def test_each_line_carries_the_rates_of_its_sell_and_its_buy() -> None:
    """Invariant: the sell rate is looked up by the SELL id and the buy rate by the BUY id."""
    sell_rate = TradeRate(rate=Decimal("1.10"), rate_date=date(2026, 6, 15))
    buy_rate = TradeRate(rate=Decimal("1.05"), rate_date=date(2026, 1, 9))
    (line,) = build_trail([_piece("7", buy="3")], {"7": sell_rate, "3": buy_rate})
    assert (line.sell_rate, line.buy_rate) == (sell_rate, buy_rate)


def test_a_trade_without_a_stored_rate_has_none() -> None:
    """Invariant: a missing rate shows as missing, not as another trade's rate."""
    rate = TradeRate(rate=Decimal("1.10"), rate_date=date(2026, 6, 15))
    (line,) = build_trail([_piece("7", buy="3")], {"7": rate})
    assert (line.sell_rate, line.buy_rate) == (rate, None)


def test_thirty_decimal_shares_are_not_rounded() -> None:
    """Invariant: gains keep every decimal the ledger stores (no 28-digit rounding)."""
    share = "0.123456789012345678901234567891"
    (line,) = build_trail([_piece("s1", proceeds="1" + share[1:], cost=share, fee="0")], {})
    assert line.gain_eur == Decimal("1")


_cents = st.decimals(min_value=Decimal("0"), max_value=Decimal("100000"), places=2)
_maybe = st.one_of(st.none(), _cents)


@settings(max_examples=200)
@given(
    st.lists(
        st.tuples(st.integers(min_value=0, max_value=5), _maybe, _maybe, _maybe),
        min_size=1,
        max_size=15,
    )
)
def test_trail_gains_of_priced_sales_add_up_to_the_modelo100_net(
    pieces: list[tuple[int, Decimal | None, Decimal | None, Decimal | None]],
) -> None:
    """Property: the trail of fully priced sales sums to the Modelo 100 net (R-2, D-1)."""
    disposal = [
        _piece(
            f"s{sale}",
            proceeds=None if p is None else str(p),
            cost=None if c is None else str(c),
            fee=None if f is None else str(f),
            buy=f"b{i}",
        )
        for i, (sale, p, c, f) in enumerate(pieces)
    ]
    trail = build_trail(disposal, {})
    report = build_modelo100(2026, disposal)
    priced = {t.sell_ref for t in report.transmissions}
    total = sum(
        (line.gain_eur for line in trail if line.sell_ref in priced and line.gain_eur is not None),
        start=Decimal("0"),
    )
    assert total == report.net_eur
