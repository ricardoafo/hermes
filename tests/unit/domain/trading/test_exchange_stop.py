"""Unit tests for the exchange-held stop values (#1080).

Rule (Code Owner decision 2026-10-19): the exchange stop is a backstop at the
strategy's own level. Rounded to the exchange's step it may only go down, by
less than one step, so it never fires before the strategy's stop would, and
the quantity never exceeds what is held.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from hermes.domain.trading.exchange_stop import EXCHANGE_STEP, Protection, RestingStop
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
ENTRY = datetime(2024, 10, 29, 3, 59, 59, 999000, tzinfo=UTC)


def _protection(qty: str = "0.5", stop: str = "140") -> Protection:
    return Protection(BTC, ENTRY, Decimal(qty), Decimal(stop))


def test_a_stop_rounds_down_to_eight_decimals() -> None:
    """140.123456789 rests at 140.12345678, not 140.12345679."""
    assert _protection(stop="140.123456789").on_exchange_steps().stop_price == Decimal(
        "140.12345678"
    )


def test_a_quantity_rounds_down_to_eight_decimals() -> None:
    """0.123456789 BTC sells 0.12345678: never more than held."""
    assert _protection(qty="0.123456789").on_exchange_steps().qty == Decimal("0.12345678")


def test_a_value_already_on_the_step_is_unchanged() -> None:
    """140.12345678 is on the step and stays exactly as it is."""
    assert _protection(stop="140.12345678").on_exchange_steps().stop_price == Decimal(
        "140.12345678"
    )


def test_a_quantity_below_one_step_cannot_rest() -> None:
    """0.000000009 BTC rounds to zero: refused rather than an order for nothing."""
    with pytest.raises(ValueError, match="positive qty"):
        _protection(qty="0.000000009").on_exchange_steps()


def test_a_naive_entry_time_is_refused() -> None:
    """Entry times are UTC, as the stop marks key them."""
    with pytest.raises(ValueError, match="entry_time"):
        Protection(BTC, datetime(2024, 10, 29, 4), Decimal("0.5"), Decimal(140))


@pytest.mark.parametrize(("qty", "stop"), [("0", "140"), ("0.5", "0"), ("-1", "140")])
def test_a_quantity_or_stop_that_is_not_positive_is_refused(qty: str, stop: str) -> None:
    """A stop for nothing, or at no price, has no meaning on the exchange."""
    with pytest.raises(ValueError, match="positive qty and stop"):
        _protection(qty=qty, stop=stop)


@given(
    stop=st.decimals(min_value=Decimal(1), max_value=Decimal(10**6), places=14),
)
@example(stop=Decimal("140.999999999999"))
def test_a_rounded_stop_is_never_above_the_strategy_stop_nor_a_step_below(stop: Decimal) -> None:
    """Rounding down moves the stop by less than one step, and never up."""
    rounded = _protection(stop=str(stop)).on_exchange_steps().stop_price
    assert (rounded <= stop, stop - rounded < EXCHANGE_STEP) == (True, True)


def test_a_resting_stop_protects_the_wanted_one_at_the_same_pair_qty_and_price() -> None:
    """Same pair, quantity and stop price: it is the stop the position wants."""
    resting = RestingStop("BTCUSDT:7", BTC, Decimal("0.5"), Decimal(140))
    assert resting.protects(_protection()) is True


@pytest.mark.parametrize(
    "resting",
    [
        RestingStop("ETHUSDT:7", ETH, Decimal("0.5"), Decimal(140)),
        RestingStop("BTCUSDT:7", BTC, Decimal("0.4"), Decimal(140)),
        RestingStop("BTCUSDT:7", BTC, Decimal("0.5"), Decimal("140.00000001")),
    ],
    ids=["other pair", "other qty", "one step above"],
)
def test_a_resting_stop_that_differs_in_anything_does_not_protect(resting: RestingStop) -> None:
    """Another pair, quantity or price, even by one step, is not the wanted stop."""
    assert resting.protects(_protection()) is False
