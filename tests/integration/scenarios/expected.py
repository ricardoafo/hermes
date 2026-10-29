"""Expected sizes, prices and fees, worked out from the pre-registration text.

Nothing here imports the sizing calculator, the fill model or the fee schedule:
each formula is copied from ``docs/experiments/swing-mvp1/pre-registration-v1.md``
or ``config/``, so a scenario fails when the code drifts from the document.
"""

from decimal import ROUND_DOWN, Decimal, localcontext

from tests.support.fake_market import wire

CAPITAL = Decimal(5000)
"""§2 experiment capital, in USDT."""

RISK_BUDGET = CAPITAL * Decimal("0.01")
"""§5.4 risk per trade: 1% of capital."""

POSITION_CAP = CAPITAL * Decimal("0.05")
"""§5.4 hard cap per position: 5% of capital."""

EXPOSURE_CAP = CAPITAL * Decimal("0.15")
"""§5.5 total exposure cap: 15% of capital."""

TAKER_FEE = Decimal("0.001")
"""§6.1 Binance VIP0 taker fee, 10 bps (``config/fees.yaml``)."""

ENTRY_SLIPPAGE = Decimal("0.0005")
"""§6.2 entry slippage: 5 bps over the bar close."""

STOP_SLIPPAGE = Decimal("0.0015")
"""§6.2 exit slippage on a stop: 15 bps under the trigger."""

DEPTH_LEVELS = 20
"""Levels per side the fake book is generated with; more than any scenario order walks."""


def entry_notional(entry: Decimal, stop: Decimal) -> Decimal:
    """Return the §5.4 notional: ``$50 / (entry - stop) * entry``, capped at $250."""
    with localcontext() as ctx:
        ctx.rounding = ROUND_DOWN
        return min(RISK_BUDGET / (entry - stop) * entry, POSITION_CAP)


def entry_qty(entry: Decimal, stop: Decimal) -> Decimal:
    """Return the base quantity of a §5.4 entry signalled at ``entry`` with ``stop``."""
    with localcontext() as ctx:
        ctx.rounding = ROUND_DOWN
        return entry_notional(entry, stop) / entry


def walk(mid: Decimal, qty: Decimal, *, buy: bool) -> Decimal:
    """Return the average price of a market order for ``qty`` on the fake book around ``mid``.

    The book is the one the fake exchange serves (``wire.depth``): the order
    takes each level's size in turn until it is filled.
    """
    book = wire.depth(mid, levels=DEPTH_LEVELS, update_id=0)
    levels = book["asks"] if buy else book["bids"]
    assert isinstance(levels, list)
    left, cost = qty, Decimal(0)
    for price_text, size_text in levels:
        take = min(left, Decimal(size_text))
        cost += take * Decimal(price_text)
        left -= take
        if left == 0:
            return cost / qty
    raise AssertionError(f"the fake book cannot fill {qty}")


def fee(price: Decimal, qty: Decimal) -> Decimal:
    """Return the §6.1 taker fee on a fill, in the quote asset."""
    return price * qty * TAKER_FEE
