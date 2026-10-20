"""Tests for the quote-currency leg of trades (change 405-usdt-fifo D-2, D-4, R-3).

Reference: ADR-016 item 4.
"""

import dataclasses
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, DefaultContext, localcontext

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from returns.result import Failure

from hermes.domain.fx.eur_pricing import EurPricing
from hermes.domain.lots.eur_fifo import EurTrade, FifoEurProjection, exact_sum, replay_fifo_eur
from hermes.domain.lots.quote_leg import (
    CapitalSeed,
    LegTrade,
    QuoteLegError,
    base_leg_trade,
    net_base_qty,
    quote_leg_qty,
    quote_leg_trades,
    quote_unit_eur,
    seed_timestamp,
)
from hermes.domain.trading.order import OrderSide
from hermes.domain.trading.trade_record import TradeEur
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId

_T0 = datetime(2026, 10, 3, 11, 45, tzinfo=UTC)
_BTC_USDT = Pair(base=Currency.BTC, quote=Currency.USDT)
_SWING = StrategyId("swing")
_SATS = Decimal("1e-8")


def _seed(amount: str = "1000", rate: str | None = "1.25", at: datetime = _T0) -> CapitalSeed:
    return CapitalSeed(
        strategy_id=_SWING,
        amount=Money(amount=Decimal(amount), currency=Currency.USDT),
        seeded_at=at,
        eur_rate=None if rate is None else Decimal(rate),
    )


def _trade(
    ref: int,
    side: OrderSide,
    *,
    qty: str = "1",
    price: str = "100",
    fee: str = "1",
    fee_currency: Currency = Currency.USDT,
    rate: str | None = "1.25",
) -> LegTrade:
    return LegTrade(
        ref=str(ref),
        pair=_BTC_USDT,
        side=side,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Money(amount=Decimal(fee), currency=fee_currency),
        executed_at=_T0 + timedelta(hours=ref),
        eur_rate=None if rate is None else Decimal(rate),
        price_eur=None if rate is None else Decimal(price) / Decimal(rate),
        fee_eur=None if rate is None else Decimal(fee) / Decimal(rate),
    )


class TestSeedTimestamp:
    """D-1: the seed is dated by evidence already in the ledger."""

    def test_earliest_activity_dates_the_seed(self) -> None:
        """Invariant (A-1.1): with activity in the ledger, the seed takes its earliest time."""
        assert seed_timestamp(_T0, _T0 + timedelta(days=1)) == _T0

    def test_an_empty_ledger_is_seeded_now(self) -> None:
        """Invariant (A-1.2): without activity, the seed is dated now."""
        assert seed_timestamp(None, _T0) == _T0


class TestCapitalSeed:
    """The seed opens one quote lot at the seed-date rate."""

    def test_seed_is_a_buy_of_its_amount_at_one_over_the_rate(self) -> None:
        """Invariant (D-4): 1000 USDT at 1.25 opens a lot of 1000 units at 0.8 EUR, no fee."""
        assert _seed().leg("seed:1") == EurTrade(
            ref="seed:1",
            side=OrderSide.BUY,
            qty=Decimal("1000"),
            executed_at=_T0,
            price_eur=Decimal("0.8"),
            fee_eur=Decimal("0"),
        )

    def test_pending_seed_has_no_eur_values(self) -> None:
        """Invariant: a seed without a rate stays unpriced in the replay."""
        leg = _seed(rate=None).leg("seed:1")
        assert (leg.price_eur, leg.fee_eur) == (None, None)

    def test_seed_value_is_amount_over_rate(self) -> None:
        """Invariant: 1000 USDT at 1.25 is worth 800 EUR."""
        assert _seed().value_eur == Decimal("800")

    def test_pending_seed_has_no_value(self) -> None:
        """Invariant: an unpriced seed has no EUR value, whatever its amount."""
        assert [_seed(amount, rate=None).value_eur for amount in ("1", "1000")] == [None, None]

    def test_naive_seed_date_is_rejected(self) -> None:
        """Invariant: a seed date must be timezone-aware."""
        with pytest.raises(ValueError, match="timezone-aware"):
            _seed(at=_T0.replace(tzinfo=None))

    def test_non_positive_seed_amount_is_rejected(self) -> None:
        """Invariant: a seed holds a positive amount."""
        with pytest.raises(ValueError, match="amount"):
            _seed(amount="0")


class TestQuoteUnit:
    """One quote unit is worth 1 / rate EUR."""

    def test_unit_value_is_cut_to_30_decimals(self) -> None:
        """Invariant: 1 / 3 is stored with exactly 30 decimals."""
        assert quote_unit_eur(Decimal("3")) == Decimal("0." + "3" * 30)


class TestBaseLeg:
    """D-2: a fee in the received base asset reduces the lot quantity."""

    def test_buy_with_a_base_fee_opens_the_net_quantity(self) -> None:
        """Invariant: BUY 1 BTC with a 0.001 BTC fee opens 0.999 BTC."""
        fee = Money(amount=Decimal("0.001"), currency=Currency.BTC)
        assert net_base_qty(
            side=OrderSide.BUY, qty=Decimal("1"), fee=fee, base=Currency.BTC
        ) == Decimal("0.999")

    def test_buy_with_a_quote_fee_opens_the_full_quantity(self) -> None:
        """Invariant: a fee in the quote currency leaves the base quantity whole."""
        fee = Money(amount=Decimal("1"), currency=Currency.USDT)
        assert net_base_qty(
            side=OrderSide.BUY, qty=Decimal("1"), fee=fee, base=Currency.BTC
        ) == Decimal("1")

    def test_sell_with_a_base_fee_disposes_of_the_full_quantity(self) -> None:
        """Invariant: only a BUY nets the base fee out of the quantity."""
        fee = Money(amount=Decimal("0.001"), currency=Currency.BTC)
        assert net_base_qty(
            side=OrderSide.SELL, qty=Decimal("1"), fee=fee, base=Currency.BTC
        ) == Decimal("1")

    def test_base_leg_trade_carries_the_net_quantity(self) -> None:
        """Invariant: the base replay trade of a base-fee BUY uses the net quantity."""
        trade = _trade(1, OrderSide.BUY, fee="0.01", fee_currency=Currency.BTC)
        assert base_leg_trade(trade).qty == Decimal("0.99")


class TestQuoteLegQty:
    """D-4: how many quote units a trade moves."""

    def test_buy_disposes_of_cost_plus_quote_fee(self) -> None:
        """Invariant: BUY 2 @ 100 with a 1 USDT fee disposes of 201 USDT."""
        assert quote_leg_qty(_trade(1, OrderSide.BUY, qty="2")).unwrap() == Decimal("201")

    def test_sell_acquires_proceeds_minus_quote_fee(self) -> None:
        """Invariant: SELL 2 @ 100 with a 1 USDT fee acquires 199 USDT."""
        assert quote_leg_qty(_trade(1, OrderSide.SELL, qty="2")).unwrap() == Decimal("199")

    def test_buy_with_a_base_fee_disposes_of_the_gross_cost(self) -> None:
        """Invariant (D-2): a base fee adds no quote units to a BUY."""
        trade = _trade(1, OrderSide.BUY, qty="2", fee="0.002", fee_currency=Currency.BTC)
        assert quote_leg_qty(trade).unwrap() == Decimal("200")

    def test_sell_with_a_base_fee_is_refused(self) -> None:
        """Invariant (Out of scope): a base fee on a SELL has no quote leg."""
        trade = _trade(7, OrderSide.SELL, fee="0.001", fee_currency=Currency.BTC)
        assert quote_leg_qty(trade) == Failure(
            QuoteLegError(ref="7", reason="SELL fee in BTC is not supported")
        )

    def test_fee_in_a_third_asset_is_refused(self) -> None:
        """Invariant (Out of scope): a fee in neither leg's asset has no quote leg."""
        trade = _trade(7, OrderSide.BUY, fee="0.01", fee_currency=Currency.EUR)
        assert quote_leg_qty(trade) == Failure(
            QuoteLegError(ref="7", reason="BUY fee in EUR is not supported")
        )

    def test_sell_whose_fee_eats_the_proceeds_is_refused(self) -> None:
        """Invariant: a quote leg never moves a non-positive quantity."""
        trade = _trade(7, OrderSide.SELL, qty="1", price="1", fee="1")
        assert quote_leg_qty(trade) == Failure(
            QuoteLegError(ref="7", reason="quote quantity 0 is not > 0")
        )


class TestQuoteLegTrades:
    """D-4: the quote asset's replay sequence."""

    def test_buy_of_base_is_a_disposal_of_quote_at_one_over_the_rate(self) -> None:
        """Invariant: BUY 2 @ 100 fee 1 at 1.25 is a SELL of 201 USDT at 0.8 EUR, no fee."""
        legs = quote_leg_trades(
            Currency.USDT, {"seed:1": _seed()}, [_trade(1, OrderSide.BUY, qty="2")]
        ).unwrap()
        assert legs[1] == EurTrade(
            ref="1",
            side=OrderSide.SELL,
            qty=Decimal("201"),
            executed_at=_T0 + timedelta(hours=1),
            price_eur=Decimal("0.8"),
            fee_eur=Decimal("0"),
        )

    def test_sell_of_base_is_an_acquisition_of_quote(self) -> None:
        """Invariant: SELL 2 @ 100 fee 1 is a BUY of 199 USDT."""
        legs = quote_leg_trades(
            Currency.USDT, {"seed:1": _seed()}, [_trade(1, OrderSide.SELL, qty="2")]
        ).unwrap()
        assert (legs[1].side, legs[1].qty) == (OrderSide.BUY, Decimal("199"))

    def test_pending_trade_leaves_its_quote_leg_unpriced(self) -> None:
        """Invariant: a trade without a rate gives a quote leg without EUR values."""
        legs = quote_leg_trades(
            Currency.USDT, {"seed:1": _seed()}, [_trade(1, OrderSide.BUY, rate=None)]
        ).unwrap()
        assert (legs[1].price_eur, legs[1].fee_eur) == (None, None)

    def test_execution_order_is_restored_and_the_seed_goes_first_on_a_tie(self) -> None:
        """Invariant: the seed precedes a trade at the same instant; trades follow fill time."""
        late, early = _trade(2, OrderSide.BUY), _trade(1, OrderSide.BUY)
        tied_seed = _seed(at=early.executed_at)
        legs = quote_leg_trades(Currency.USDT, {"seed:9": tied_seed}, [late, early]).unwrap()
        assert [leg.ref for leg in legs] == ["seed:9", "1", "2"]

    def test_trade_ids_order_numerically_at_the_same_instant(self) -> None:
        """Invariant: trade 10 follows trade 9 when both filled at the same time."""
        nine = _trade(9, OrderSide.BUY)
        ten = dataclasses.replace(nine, ref="10")
        legs = quote_leg_trades(Currency.USDT, {"seed:1": _seed()}, [ten, nine]).unwrap()
        assert [leg.ref for leg in legs] == ["seed:1", "9", "10"]

    def test_trade_quoted_in_another_currency_is_refused(self) -> None:
        """Invariant: only trades quoted in the replayed asset feed its leg."""
        eur_pair = Pair(base=Currency.BTC, quote=Currency.EUR)
        trade = dataclasses.replace(_trade(1, OrderSide.BUY), pair=eur_pair)
        assert quote_leg_trades(Currency.USDT, {}, [trade]) == Failure(
            QuoteLegError(ref="1", reason=f"pair is quoted in {eur_pair}")
        )

    def test_seed_held_in_another_currency_is_refused(self) -> None:
        """Invariant: a seed feeds only the leg of the currency it is held in."""
        eur_seed = CapitalSeed(
            strategy_id=_SWING, amount=Money(Decimal("5"), Currency.EUR), seeded_at=_T0
        )
        assert quote_leg_trades(Currency.USDT, {"seed:1": eur_seed}, []) == Failure(
            QuoteLegError(ref="seed:1", reason=f"seed is held in {Currency.EUR}")
        )

    def test_a_refused_trade_fails_the_whole_leg(self) -> None:
        """Invariant: a trade with an unsupported fee fails the derivation."""
        bad = _trade(2, OrderSide.SELL, fee="0.1", fee_currency=Currency.BTC)
        result = quote_leg_trades(Currency.USDT, {"seed:1": _seed()}, [bad])
        assert result == Failure(QuoteLegError(ref="2", reason="SELL fee in BTC is not supported"))


# ---------------------------------------------------------------------------
# R-3: EUR conservation over all assets
# ---------------------------------------------------------------------------


def _priced(
    ref: int, side: OrderSide, qty: Decimal, price: Decimal, fee: Money, rate: Decimal
) -> LegTrade:
    pricing = EurPricing(quote=Currency.USDT, rate=rate, rate_date=date(2026, 10, 2), source="t")
    eur = TradeEur.price(
        pricing, price=Money(amount=price, currency=Currency.USDT), fee=fee, base=Currency.BTC
    )
    assert eur is not None
    return LegTrade(
        ref=str(ref),
        pair=_BTC_USDT,
        side=side,
        qty=qty,
        price=price,
        fee=fee,
        executed_at=_T0 + timedelta(minutes=ref),
        eur_rate=rate,
        price_eur=eur.price_eur,
        fee_eur=eur.fee_eur,
    )


@st.composite
def _seeded_history(draw: st.DrawFn) -> tuple[CapitalSeed, list[LegTrade]]:
    """Draw a seed and a feasible sequence of priced BTC/USDT trades after it."""
    seed = _seed(
        amount=str(Decimal(draw(st.integers(10_000, 10_000_000))) / 100),
        rate=str(Decimal(draw(st.integers(9_000, 13_000))) / 10_000),
    )
    usdt, btc = seed.amount.amount, Decimal(0)
    trades: list[LegTrade] = []
    for ref in range(1, draw(st.integers(1, 12)) + 1):
        price = Decimal(draw(st.integers(10_000, 10_000_000))) / 100
        rate = Decimal(draw(st.integers(9_000, 13_000))) / 10_000
        share = Decimal(draw(st.integers(1, 100))) / 100
        if btc == 0 or draw(st.booleans()):
            qty = (usdt * share / (price * Decimal("1.001"))).quantize(_SATS, ROUND_DOWN)
            if qty <= 0:
                continue
            if draw(st.booleans()):
                fee = Money((qty * Decimal("0.001")).quantize(_SATS, ROUND_DOWN), Currency.BTC)
                usdt -= price * qty
                btc += qty - fee.amount
            else:
                fee = Money((price * qty / 1000).quantize(_SATS, ROUND_DOWN), Currency.USDT)
                usdt -= price * qty + fee.amount
                btc += qty
            trades.append(_priced(ref, OrderSide.BUY, qty, price, fee, rate))
        else:
            qty = (btc * share).quantize(_SATS, ROUND_DOWN)
            if qty <= 0:
                continue
            fee = Money((price * qty / 1000).quantize(_SATS, ROUND_DOWN), Currency.USDT)
            usdt += price * qty - fee.amount
            btc -= qty
            trades.append(_priced(ref, OrderSide.SELL, qty, price, fee, rate))
    return seed, trades


def _gains(projection: FifoEurProjection) -> list[Decimal]:
    return [c.gain_eur for c in projection.consumptions if c.gain_eur is not None]


def _remaining_basis(projection: FifoEurProjection) -> list[Decimal]:
    consumed: defaultdict[str, list[Decimal]] = defaultdict(list)
    for piece in projection.consumptions:
        assert piece.cost_eur is not None
        consumed[piece.buy_ref].append(piece.cost_eur)
    basis: list[Decimal] = []
    for lot in projection.lots:
        assert lot.acquisition_eur is not None
        basis.append(lot.acquisition_eur - exact_sum(consumed[lot.buy_ref]))
    return basis


def _half_ulp(value: Decimal, precision: int) -> Decimal:
    """Largest rounding error of ``value`` once rounded to ``precision`` significant digits."""
    return Decimal(5).scaleb(value.adjusted() - precision)


def _rounding_bound(trades: list[LegTrade]) -> Decimal:
    """How far the two legs' EUR values of the same trades can drift apart (#608).

    The base leg values a trade at ``price_eur`` and ``fee_eur``, which
    ``EurPricing.to_eur`` divides in the default Decimal context. The quote leg
    values it at ``quote_unit_eur(rate)``, cut to that function's quantum. The
    replay itself adds and splits without loss, so the identity can miss by at
    most the sum of both roundings over every trade.
    """
    to_eur_precision = DefaultContext.prec
    quantum_exponent = quote_unit_eur(Decimal(3)).as_tuple().exponent
    assert isinstance(quantum_exponent, int)
    half_quantum = Decimal(5).scaleb(quantum_exponent - 1)
    with localcontext() as ctx:
        ctx.prec = 100
        bound = Decimal(0)
        for t in trades:
            assert t.price_eur is not None
            assert t.fee_eur is not None
            fee_quote = t.fee.amount * t.price if t.fee.currency == Currency.BTC else t.fee.amount
            bound += t.qty * _half_ulp(t.price_eur, to_eur_precision)
            bound += _half_ulp(t.fee_eur, to_eur_precision)
            bound += (t.price * t.qty + fee_quote) * half_quantum
        return bound


def _usdt(amount: str) -> Money:
    return Money(Decimal(amount), Currency.USDT)


_DRIFT_PAST_1E_18 = (
    _seed(amount="14297.03", rate="0.9"),
    [
        _priced(
            1,
            OrderSide.BUY,
            Decimal("117.41862763"),
            Decimal("104.61"),
            _usdt("12.28316263"),
            Decimal("1.3"),
        ),
        _priced(
            2,
            OrderSide.SELL,
            Decimal("1.17418627"),
            Decimal("100.02"),
            _usdt("0.11744211"),
            Decimal("0.9002"),
        ),
        _priced(
            3,
            OrderSide.SELL,
            Decimal("1.16244441"),
            Decimal("100"),
            _usdt("0.11624444"),
            Decimal("0.9"),
        ),
        _priced(
            4,
            OrderSide.SELL,
            Decimal("113.93117698"),
            Decimal("18232.22"),
            _usdt("2077.21828355"),
            Decimal("1.3"),
        ),
        _priced(
            5,
            OrderSide.SELL,
            Decimal("0.02301639"),
            Decimal("12537.23"),
            _usdt("0.28856177"),
            Decimal("1.3"),
        ),
        _priced(
            6,
            OrderSide.BUY,
            Decimal("9172.29651424"),
            Decimal("101.83"),
            _usdt("934.01495404"),
            Decimal("0.9"),
        ),
        _priced(
            7,
            OrderSide.SELL,
            Decimal("9081.69007464"),
            Decimal("24903.15"),
            _usdt("226162.69018227"),
            Decimal("1.2997"),
        ),
        _priced(
            8,
            OrderSide.SELL,
            Decimal("90.81690074"),
            Decimal("74736.54"),
            _usdt("6787.34093483"),
            Decimal("1.028"),
        ),
        _priced(
            9,
            OrderSide.BUY,
            Decimal("502346.24964460"),
            Decimal("465.07"),
            _usdt("233626.17032221"),
            Decimal("1.3"),
        ),
        _priced(
            10,
            OrderSide.SELL,
            Decimal("231079.69681403"),
            Decimal("12151.31"),
            _usdt("2807921.03069329"),
            Decimal("0.9"),
        ),
    ],
)


@settings(max_examples=300)
@given(history=_seeded_history())
# Missed by 1.06e-18 against the former fixed 1e-18 tolerance (#608).
@example(history=_DRIFT_PAST_1E_18)
def test_realized_gains_equal_remaining_basis_minus_seed_value(
    history: tuple[CapitalSeed, list[LegTrade]],
) -> None:
    """Invariant (R-3 A-3.1): sum of gains == sum of remaining basis - seed value, all assets.

    Exact up to the rounding the code itself does, see :func:`_rounding_bound`.
    """
    seed, trades = history
    base = replay_fifo_eur(Currency.BTC, [base_leg_trade(t) for t in trades]).unwrap()
    quote_legs = quote_leg_trades(Currency.USDT, {"seed:1": seed}, trades).unwrap()
    quote = replay_fifo_eur(Currency.USDT, quote_legs).unwrap()
    assert seed.value_eur is not None
    with localcontext() as ctx:
        ctx.prec = 100
        gains = exact_sum([*_gains(base), *_gains(quote)])
        basis = exact_sum([*_remaining_basis(base), *_remaining_basis(quote)])
        assert abs(gains - (basis - seed.value_eur)) <= _rounding_bound(trades)
