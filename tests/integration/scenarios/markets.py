"""Scripted synthetic markets, one per engine path.

All prices are invented round numbers starting at 100 on 2024-01-01, long before
the pre-registered test window (ADR-012). BTC, ETH and SOL are the three pairs of
the repository config; a pair a scenario does not use stays flat, which never
meets the §5.1 momentum gate.

Each builder returns the market and the instant serve boots at. Boot is one day
before the move that matters, after the 200 daily bars EMA(200) needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from tests.support.fake_market.script import Market, MarketScript

BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
SOL = Pair(base=Currency.SOL, quote=Currency.USDT)

START = datetime(2024, 1, 1, tzinfo=UTC)
_RAMP_DAYS = 302
_WIDE = Decimal("0.03")
"""Bar range of a volatile pair: wide enough that the chandelier stop sits well below."""
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class Scenario:
    """A scripted market and when serve boots on it.

    Attributes:
        market: The bars.
        live_from: Boot instant.
        breakout_at: Close time of the bar scripted to break out.

    """

    market: Market
    live_from: datetime
    breakout_at: datetime


def _script() -> MarketScript:
    return MarketScript(
        start=START, prices={BTC: Decimal(100), ETH: Decimal(100), SOL: Decimal(100)}
    )


def _boot_before_breakout() -> datetime:
    return START + timedelta(days=_RAMP_DAYS - 1)


def _scenario(script: MarketScript, live_from: datetime, pair: Pair, breakout: Decimal) -> Scenario:
    """Build the market and find the close time of ``pair``'s first bar closing at ``breakout``."""
    market = script.build()
    at = next(b.close_time for b in market.h4[pair] if b.close == breakout)
    return Scenario(market, live_from, at)


def chandelier_exit() -> Scenario:
    """BTC trends 100 to 150, breaks out to 156, holds two days, then gaps down to 120.

    The breakout bar meets all three §5.1 conditions. The gap bar's low of 115
    is far under the chandelier stop, so the position exits on that bar (§5.2).
    """
    script = _script()
    script.ramp(BTC, days=_RAMP_DAYS, to=Decimal(150))
    script.bar(BTC, close=Decimal(156), high=Decimal(157))
    script.hold(BTC, days=2)
    script.bar(BTC, close=Decimal(120), low=Decimal(115))
    script.hold(BTC, days=1)
    return _scenario(script, _boot_before_breakout(), BTC, Decimal(156))


def time_stop() -> Scenario:
    """Script the chandelier breakout, then BTC holding at 156 for 91 days.

    Flat bars keep their lows above the chandelier stop and the daily close
    above EMA(200), so only the 90-day time stop can close the position (§5.2).
    """
    script = _script()
    script.ramp(BTC, days=_RAMP_DAYS, to=Decimal(150))
    script.bar(BTC, close=Decimal(156), high=Decimal(157))
    script.hold(BTC, days=91)
    return _scenario(script, _boot_before_breakout(), BTC, Decimal(156))


def regime_break() -> Scenario:
    """Volatile BTC at 150 for 240 days, rallies to 170, then closes a day at 148.

    Bars span 8% of the close, so the chandelier stop at entry sits about 36
    under the price: wide enough that the 1% risk budget, not the 5% cap, sets
    the size (§5.4). The flat history puts EMA(200) at about 150, so the daily
    close at 148 is a regime break (§5.2) while every low stays over the stop.
    """
    wide = Decimal("0.08")
    script = MarketScript(
        start=START, prices={BTC: Decimal(150), ETH: Decimal(100), SOL: Decimal(100)}
    )
    script.hold(BTC, days=240, spread=wide)
    script.ramp(BTC, days=5, to=Decimal(156), spread=wide)
    script.bar(BTC, close=Decimal(170), high=Decimal(171), low=Decimal(155))
    script.hold(BTC, days=2, spread=wide)
    script.ramp(BTC, days=1, to=Decimal(148), spread=wide)
    script.hold(BTC, days=3, spread=wide)
    return _scenario(script, START + timedelta(days=244), BTC, Decimal(170))


def regime_filter_blocks() -> Scenario:
    """BTC falls 150 to 100, then jumps to 108: a breakout with strong ADX below EMA(200).

    Donchian and ADX both hold on the jump bar, but the daily close is under
    EMA(200), so §5.1 forbids the entry.
    """
    script = MarketScript(
        start=START, prices={BTC: Decimal(150), ETH: Decimal(100), SOL: Decimal(100)}
    )
    script.ramp(BTC, days=_RAMP_DAYS, to=Decimal(100))
    script.bar(BTC, close=Decimal(108), high=Decimal(109))
    script.hold(BTC, days=2)
    return _scenario(script, _boot_before_breakout(), BTC, Decimal(108))


def momentum_gate_blocks() -> Scenario:
    """BTC trends 100 to 150 through choppy bars, then closes at 155 above every recent high.

    Bars alternate a long upper wick and a long lower wick, so the up and down
    directional moves cancel and ADX(14) stays near 4. The jump bar is a
    breakout with the daily close above EMA(200), but §5.1 also needs ADX at
    25 or more.
    """
    script = _script()
    bars = _RAMP_DAYS * 6
    wick, tick = Decimal(3), Decimal("0.1")
    for i in range(bars):
        trend = (Decimal(100) + Decimal(50) * Decimal(i + 1) / Decimal(bars)).quantize(_CENT)
        if i % 2 == 0:
            script.bar(BTC, close=trend, high=trend + wick, low=trend - tick)
        else:
            script.bar(BTC, close=trend, high=trend + tick, low=trend - wick)
    script.bar(BTC, close=Decimal(155), high=Decimal("155.5"))
    script.hold(BTC, days=1)
    return _scenario(script, _boot_before_breakout(), BTC, Decimal(155))


def three_breakouts(btc_climbs_to: Decimal = Decimal(175)) -> Scenario:
    """BTC breaks out and climbs to ``btc_climbs_to``; four days later ETH, then SOL, break out.

    Each entry is capped at 250 notional (§5.4). With BTC at 175 its position
    is worth about 280 by the ETH and SOL breakouts, so ETH fits under the 750
    exposure cap and SOL would take it to about 780 (§5.5). With BTC back at
    156 all three fit: 3 x 250 is the cap itself.
    """
    script = _script()
    script.ramp(BTC, days=_RAMP_DAYS, to=Decimal(150), spread=_WIDE)
    script.bar(BTC, close=Decimal(156), high=Decimal(157))
    script.ramp(BTC, days=4, to=btc_climbs_to, spread=_WIDE)
    script.hold(BTC, days=3, spread=_WIDE)
    script.ramp(ETH, days=306, to=Decimal(150))
    script.bar(ETH, close=Decimal(156), high=Decimal(157))
    script.hold(ETH, days=2)
    script.ramp(SOL, days=306, to=Decimal(150))
    script.bar(SOL, close=Decimal(150))
    script.bar(SOL, close=Decimal(156), high=Decimal(157))
    script.hold(SOL, days=2)
    return _scenario(script, _boot_before_breakout(), BTC, Decimal(156))


def three_breakouts_within_a_day() -> Scenario:
    """BTC breaks out, then ETH one bar later and SOL two bars later.

    All three entries come inside RO-03's 24-hour slippage window, so a BTC
    fill that slips over 0.5% still counts when ETH and SOL break out.
    """
    script = _script()
    script.ramp(BTC, days=_RAMP_DAYS, to=Decimal(150), spread=_WIDE)
    script.bar(BTC, close=Decimal(156), high=Decimal(157))
    script.hold(BTC, days=3, spread=_WIDE)
    script.ramp(ETH, days=_RAMP_DAYS, to=Decimal(150))
    script.bar(ETH, close=Decimal(150))
    script.bar(ETH, close=Decimal(156), high=Decimal(157))
    script.hold(ETH, days=2)
    script.ramp(SOL, days=_RAMP_DAYS, to=Decimal(150))
    script.bar(SOL, close=Decimal(150))
    script.bar(SOL, close=Decimal(150))
    script.bar(SOL, close=Decimal(156), high=Decimal(157))
    script.hold(SOL, days=2)
    return _scenario(script, _boot_before_breakout(), BTC, Decimal(156))


def thin_exit_bar() -> Scenario:
    """Script the chandelier breakout, then a gap-down bar that trades no volume at all.

    With no volume the §6.2 book has no depth, so when the live book is also
    down nothing can price the exit.
    """
    script = _script()
    script.ramp(BTC, days=_RAMP_DAYS, to=Decimal(150))
    script.bar(BTC, close=Decimal(156), high=Decimal(157))
    script.hold(BTC, days=2)
    script.bar(BTC, close=Decimal(120), low=Decimal(115), volume=Decimal(0))
    script.hold(BTC, days=1)
    return _scenario(script, _boot_before_breakout(), BTC, Decimal(156))


def btc_daily_crash_then_eth_breakout() -> Scenario:
    """BTC drops 15% in one day; the next day ETH breaks out.

    The BTC daily log-return is about -16%, under the -10% macro halt
    (RO-07), so the ETH entry is refused.
    """
    script = _script()
    script.ramp(BTC, days=_RAMP_DAYS, to=Decimal(100))
    script.ramp(BTC, days=1, to=Decimal(85))
    script.hold(BTC, days=3)
    script.ramp(ETH, days=_RAMP_DAYS + 1, to=Decimal(150))
    script.bar(ETH, close=Decimal(156), high=Decimal(157))
    script.hold(ETH, days=2)
    return _scenario(script, _boot_before_breakout(), ETH, Decimal(156))


def btc_crash_with_entries_during_and_after_the_halt() -> Scenario:
    """BTC drops 15% in one day; ETH and SOL break out the next day, ETH again a day later.

    The BTC daily log-return of about -16% puts RO-07 in force for the whole
    next day, so the ETH breakout at 04:00 and the SOL one at 16:00 are
    refused. The day after, BTC closes flat (log-return 0) and RO-07 lets
    entries through again: ETH's second breakout, to 165, fills.
    """
    script = _script()
    script.ramp(BTC, days=_RAMP_DAYS, to=Decimal(100))
    script.ramp(BTC, days=1, to=Decimal(85))
    script.hold(BTC, days=5)
    script.ramp(ETH, days=_RAMP_DAYS + 1, to=Decimal(150))
    script.bar(ETH, close=Decimal(156), high=Decimal(157))
    script.hold(ETH, days=1.5)
    script.bar(ETH, close=Decimal(165), high=Decimal(166))
    script.hold(ETH, days=2)
    script.ramp(SOL, days=_RAMP_DAYS + 1.5, to=Decimal(150))
    script.bar(SOL, close=Decimal(156), high=Decimal(157))
    script.hold(SOL, days=2)
    return _scenario(script, _boot_before_breakout(), ETH, Decimal(156))


def entry_then_exit_then_two_breakouts() -> Scenario:
    """BTC breaks out and exits two days later; SOL breaks out in between, ETH after.

    BTC's bar at 150 with a low of 148 is under the chandelier stop, so the
    position exits on it (§5.2) without a daily drop big enough for RO-07.
    On a thin book the BTC entry slips past the RO-03 limit, which refuses
    the SOL entry; the BTC exit is a later fill, so RO-03 judges the ETH
    entry by the exit's slippage.
    """
    script = _script()
    script.ramp(BTC, days=_RAMP_DAYS, to=Decimal(150))
    script.bar(BTC, close=Decimal(156), high=Decimal(157))
    script.hold(BTC, days=2)
    script.bar(BTC, close=Decimal(150), low=Decimal(148))
    script.hold(BTC, days=3)
    script.ramp(SOL, days=_RAMP_DAYS + 1, to=Decimal(150))
    script.bar(SOL, close=Decimal(156), high=Decimal(157))
    script.hold(SOL, days=4)
    script.ramp(ETH, days=_RAMP_DAYS + 4, to=Decimal(150))
    script.bar(ETH, close=Decimal(156), high=Decimal(157))
    script.hold(ETH, days=2)
    return _scenario(script, _boot_before_breakout(), BTC, Decimal(156))
