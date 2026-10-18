"""Seeded synthetic market paths, with the faults a live run meets, for the generated scenarios.

One seed gives one :class:`GeneratedPath`: three pairs of 4h bars (daily bars
are built from them), the instant serve boots, and every fault the fake market
injects: depth, klines and Frankfurter outages, bars published late, bars sent
twice, and the instants serve is restarted. The same seed always gives the
same path, so a failing run is replayed by its seed alone.

Two generators share the machinery:

- :func:`random_walk` strings random segments together: walks, trends up and
  down, flat days, price gaps, wick spikes and bars that trade no volume.
- :func:`hater` aims at the edges: three pairs breaking out on the same bar
  or one bar apart, held pairs priced so a third entry lands on the exposure
  cap, dips whose low sits next to the chandelier stop, exits on bars with no
  volume and no live book, restarts right after an entry, and bars published
  so late that serve stops and is booted again.

Every price is invented: each pair starts at 100 on 2024-01-01, far before the
pre-registered test window (ADR-012). Nothing reads a recording or a network.

What the plain generator leaves out, and why:

- A bar that never arrives (issue #852: serve crash-loops until someone steps
  in). Every late bar here is published eventually.
- A bar published after the next one, in :func:`random_walk`: serve stops on
  it by design and needs a boot after the bar is out. :func:`hater` has it.
- NaN or non-numeric prices (issue #850).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from tests.integration.scenarios.markets import BTC, ETH, SOL, START
from tests.support.fake_market.script import BARS_PER_DAY, H4, Market, MarketScript, Step

PAIRS = (BTC, ETH, SOL)
HISTORY_DAYS = 300
"""Days of bars before boot: the ``warmup_bars_1d`` floor of 300 closes (#880)."""

LIVE_DAYS = 30
"""Days serve runs on the path."""

LIVE_BARS = LIVE_DAYS * BARS_PER_DAY
_MINUTE = timedelta(minutes=1)
_TICK = Decimal("0.00000001")
_FLOOR = 1.0
"""Lowest close a path may reach; a price never goes to zero."""


type Window = tuple[datetime, datetime]
type LateBar = tuple[Pair, KlineInterval, datetime, datetime]
type DuplicateBar = tuple[Pair, KlineInterval, datetime]


@dataclass(frozen=True)
class GeneratedPath:
    """A market and its faults, from one seed.

    Attributes:
        seed: The seed that built it.
        kind: ``walk`` or ``hater``.
        market: The bars.
        live_from: First boot instant.
        book_outages: Windows when the depth endpoint answers HTTP 503.
        klines_outages: Windows when the klines endpoint answers HTTP 503.
        fx_outages: Windows when Frankfurter answers HTTP 503.
        late_bars: Bars the klines endpoint publishes late.
        duplicate_bars: Bars the klines endpoint sends twice.
        restarts: Instants serve is stopped and booted again on the same ledger.
        notes: What was scripted, in order, for a failure message.

    """

    seed: int
    kind: str
    market: Market
    live_from: datetime
    book_outages: list[Window] = field(default_factory=list)
    klines_outages: list[Window] = field(default_factory=list)
    fx_outages: list[Window] = field(default_factory=list)
    late_bars: list[LateBar] = field(default_factory=list)
    duplicate_bars: list[DuplicateBar] = field(default_factory=list)
    restarts: list[datetime] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def end(self) -> datetime:
        """Return when the run stops: the last bar's close, or once every bar can be out.

        A late bar shown after the last close, or a klines outage still open at
        it, holds bars back past the end of the market. The run then goes on
        until a minute after the last of those ends (the feed polls at least
        every 30 s while it waits), so serve gets to fetch and handle them, as
        it would live (issue #1207).
        """
        held_back = [shown for _, _, _, shown in self.late_bars] + [
            end for _, end in self.klines_outages
        ]
        return max([self.market.end, *(at + _MINUTE for at in held_back)])


@dataclass
class _Builder:
    """Mutable state while one path is generated."""

    rng: random.Random
    closes: dict[Pair, float]
    steps: dict[Pair, list[Step]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def push(
        self,
        pair: Pair,
        close: float,
        *,
        spread: float,
        high: float | None = None,
        low: float | None = None,
        volume: float = 1000.0,
    ) -> None:
        """Append one 4h bar to ``pair``."""
        close = max(close, _FLOOR)
        self.steps.setdefault(pair, []).append(
            Step(
                close=_dec(close),
                high=None if high is None else _dec(max(high, close)),
                low=None if low is None else _dec(max(min(low, close), _FLOOR / 2)),
                volume=_dec(volume),
                spread=_dec(spread),
            )
        )
        self.closes[pair] = close

    def count(self, pair: Pair) -> int:
        """Return how many 4h bars ``pair`` has."""
        return len(self.steps.get(pair, []))


def _dec(value: float) -> Decimal:
    return Decimal(f"{value:.8f}").quantize(_TICK)


def _history(b: _Builder, pair: Pair, *, trend: str, wide: bool = False) -> float:
    """Script ``HISTORY_DAYS`` of bars for ``pair``; return the bar range used.

    A ``wide`` pair's bars span 7 to 12% of the close, so its chandelier stop
    sits over 20% under the price and the 1% risk budget, not the 5% cap, sets
    the size (§5.4).
    """
    rng = b.rng
    spread = rng.uniform(0.07, 0.12) if wide else rng.uniform(0.01, 0.04)
    target = {
        "up": rng.uniform(1.3, 2.0),
        "flat": rng.uniform(0.95, 1.05),
        "down": rng.uniform(0.55, 0.8),
    }[trend]
    bars = HISTORY_DAYS * BARS_PER_DAY
    start = b.closes[pair]
    wobble = 0.0
    for i in range(1, bars + 1):
        wobble = 0.97 * wobble + rng.gauss(0.0, 0.004)
        b.push(pair, start * target ** (i / bars) * math.exp(wobble), spread=spread)
    b.notes.append(f"{pair} history {trend} to {b.closes[pair]:.2f}")
    return spread


def _segment(b: _Builder, pair: Pair, spread: float, budget: int) -> int:
    """Script one random segment of at most ``budget`` bars; return how many bars it took."""
    rng = b.rng
    kind = rng.choices(
        ["walk", "up", "down", "flat", "gap", "spike", "silent"],
        weights=[30, 25, 12, 12, 7, 7, 7],
    )[0]
    length = 1 if kind in {"gap", "spike"} else min(budget, rng.randint(6, 30))
    start = b.count(pair)
    for _ in range(length):
        close = b.closes[pair]
        if kind == "walk":
            b.push(pair, close * math.exp(rng.gauss(0.0, 0.012)), spread=spread)
        elif kind == "up":
            b.push(pair, close * math.exp(rng.gauss(0.008, 0.004)), spread=spread)
        elif kind == "down":
            b.push(pair, close * math.exp(rng.gauss(-0.008, 0.004)), spread=spread)
        elif kind == "flat":
            b.push(pair, close, spread=0.002)
        elif kind == "gap":
            b.push(pair, close * (1 + rng.choice([-1, 1]) * rng.uniform(0.04, 0.15)), spread=spread)
        elif kind == "spike":
            wick = close * rng.uniform(0.05, 0.15)
            if rng.random() < 0.5:
                b.push(pair, close, spread=spread, high=close + wick)
            else:
                b.push(pair, close, spread=spread, low=close - wick)
        else:
            b.push(pair, close * math.exp(rng.gauss(0.0, 0.006)), spread=spread, volume=0.0)
    b.notes.append(f"{pair} bar {start - HISTORY_DAYS * BARS_PER_DAY}: {kind} x{length}")
    return length


def _live_close(path_start: datetime, index: int) -> datetime:
    """Return the close instant of live 4h bar ``index`` (exclusive close)."""
    return path_start + timedelta(days=HISTORY_DAYS) + H4 * (index + 1)


def _long_tail(rng: random.Random, low: int, usual: int, rare: int) -> int:
    """Return minutes, mostly ``low`` to ``usual``, one time in five up to ``rare``.

    While klines are down or a bar is late the feed polls every 5 to 30
    simulated seconds, and every poll is a real request through the adapters:
    a five-hour outage cost more than the rest of the month together.
    """
    return rng.randint(low, usual) if rng.random() < 0.8 else rng.randint(usual, rare)


def _faults(
    rng: random.Random, market: Market, live_from: datetime, notes: list[str]
) -> tuple[list[Window], list[Window], list[Window], list[LateBar], list[DuplicateBar]]:
    """Draw the data faults of a path: outages, late bars and repeated bars."""
    span = market.end - live_from

    def book_window() -> Window:
        at = _live_close(START, rng.randrange(LIVE_BARS))
        return at, at + _MINUTE * rng.randint(1, 360)

    def klines_window() -> Window:
        at = live_from + span * rng.random()
        return at, at + _MINUTE * _long_tail(rng, 10, 30, 240)

    def fx_window() -> Window:
        at = live_from + span * rng.random()
        return at, at + _MINUTE * rng.randint(60, 3 * 24 * 60)

    def late_bar() -> LateBar:
        pair = rng.choice(PAIRS)
        bar = market.h4[pair][HISTORY_DAYS * BARS_PER_DAY + rng.randrange(LIVE_BARS)]
        # Shown before the next bar of the stream closes, so it is waited for (issue #852
        # is a bar that never shows; one shown after the next bar is the hater's).
        return (
            pair,
            KlineInterval.H4,
            bar.open_time,
            bar.close_time + _MINUTE * _long_tail(rng, 1, 15, 230),
        )

    def duplicate_bar() -> DuplicateBar:
        pair = rng.choice(PAIRS)
        interval = rng.choice([KlineInterval.H4, KlineInterval.D1])
        live = [b for b in market.bars(pair, interval) if b.open_time >= live_from]
        return pair, interval, rng.choice(live).open_time

    book = [book_window() for _ in range(rng.randint(0, 3))]
    klines = [klines_window() for _ in range(rng.randint(0, 1))]
    fx = [fx_window() for _ in range(rng.randint(0, 2))]
    late = [late_bar() for _ in range(rng.randint(0, 2))]
    duplicates = [duplicate_bar() for _ in range(rng.randint(0, 3))]
    notes.extend(
        [
            f"book outages {[(s.isoformat(), e.isoformat()) for s, e in book]}",
            f"klines outages {[(s.isoformat(), e.isoformat()) for s, e in klines]}",
            f"fx outages {len(fx)}",
            f"late bars {[(str(p), o.isoformat(), s.isoformat()) for p, _, o, s in late]}",
            f"duplicate bars {len(duplicates)}",
        ]
    )
    return book, klines, fx, late, duplicates


def _quiet(at: datetime, path: _Faults) -> bool:
    """Return whether a boot at ``at`` meets no outage and no unpublished late bar.

    A boot inside a klines outage cannot warm up, and a boot before a late bar
    is out finds a hole in its warm-up: both are other scenarios than a plain
    restart.
    """
    windows = [*path.book, *path.klines, *path.fx]
    if any(start - H4 <= at < end + H4 for start, end in windows):
        return False
    return not any(open_time <= at < shown + H4 for _, _, open_time, shown in path.late)


@dataclass
class _Faults:
    book: list[Window]
    klines: list[Window]
    fx: list[Window]
    late: list[LateBar]
    duplicates: list[DuplicateBar]


def _restarts(
    rng: random.Random, faults: _Faults, wanted: int, *, near: list[int]
) -> list[datetime]:
    """Pick up to ``wanted`` quiet restart instants, some just after the live bars in ``near``."""
    candidates = [*near, *(rng.randrange(1, LIVE_BARS - 1) for _ in range(4 * wanted))]
    instants = [_live_close(START, i) + _MINUTE * rng.randint(10, 180) for i in candidates]
    return sorted(list(dict.fromkeys(at for at in instants if _quiet(at, faults)))[:wanted])


def random_walk(seed: int) -> GeneratedPath:
    """Return the random-walk path of ``seed``: random segments and random faults.

    Most histories trend up so the 1d regime filter lets entries through; the
    live month is a chain of random segments per pair.
    """
    rng = random.Random(f"walk-{seed}")  # noqa: S311 — reproducible test input, not a secret
    b = _Builder(rng=rng, closes=dict.fromkeys(PAIRS, 100.0))
    for pair in PAIRS:
        trend = rng.choices(["up", "flat", "down"], weights=[70, 15, 15])[0]
        spread = _history(b, pair, trend=trend, wide=rng.random() < 0.15)
        left = LIVE_BARS
        while left > 0:
            left -= _segment(b, pair, spread, left)
    return _finish(seed, "walk", b, rng, near=[])


def hater(seed: int) -> GeneratedPath:
    """Return the hater path of ``seed``: synchronized breakouts, cap edges, dips to the stop.

    Every history trends up and one pair's bars are wide, so the risk budget
    sizes it. Within the first eight live days all three pairs climb, then
    break out on the same bar (or one bar apart): three entries back to back.
    After that each pair draws edge segments: holds within 1 bp of the last
    close (held positions stay at their entry value, so the next breakout's
    entry lands on the exposure cap), new breakouts, dips whose low sits within
    about 1% of an estimated chandelier stop, and dips that trade no volume,
    most of them with the depth endpoint down. Restarts follow the breakout.
    """
    rng = random.Random(f"hater-{seed}")  # noqa: S311 — reproducible test input, not a secret
    b = _Builder(rng=rng, closes=dict.fromkeys(PAIRS, 100.0))
    volatile = rng.choice(PAIRS)
    spreads = {pair: _history(b, pair, trend="up", wide=pair == volatile) for pair in PAIRS}
    climb = rng.randint(12, 36)
    stagger = {pair: rng.choice([0, 0, 1]) for pair in PAIRS}
    for pair in PAIRS:
        for _ in range(climb + stagger[pair]):
            b.push(pair, b.closes[pair] * math.exp(rng.gauss(0.006, 0.003)), spread=spreads[pair])
        recent = max(float(s.close) for s in b.steps[pair][-20:])
        jump = recent * rng.uniform(1.02, 1.06)
        b.push(pair, jump, spread=spreads[pair], high=jump * 1.002)
        b.notes.append(
            f"{pair} breakout at live bar {b.count(pair) - 1 - HISTORY_DAYS * BARS_PER_DAY}"
        )
    breakout_bar = max(b.count(p) for p in PAIRS) - 1 - HISTORY_DAYS * BARS_PER_DAY
    for pair in PAIRS:
        while b.count(pair) < HISTORY_DAYS * BARS_PER_DAY + breakout_bar + 1:
            b.push(pair, b.closes[pair], spread=0.002)
    for pair in PAIRS:
        left = HISTORY_DAYS * BARS_PER_DAY + LIVE_BARS - b.count(pair)
        while left > 0:
            left -= _hater_segment(b, pair, spreads[pair], left)
    return _finish(seed, "hater", b, rng, near=[breakout_bar, breakout_bar + 1])


def _stop_estimate(steps: list[Step]) -> float:
    """Estimate the chandelier stop: highest high(22) - 3 x mean true range(22).

    Only aims the dips: the engine's ATR smoothing differs a little, so the
    dips land on both sides of the real stop, which is the point.
    """
    window = steps[-23:]
    highs = [_extreme(s, s.high, 1) for s in window[1:]]
    lows = [_extreme(s, s.low, -1) for s in window[1:]]
    ranges = [
        max(high - low, abs(high - float(prev.close)), abs(low - float(prev.close)))
        for prev, high, low in zip(window, highs, lows, strict=False)
    ]
    return max(highs) - 3 * sum(ranges) / len(ranges)


def _extreme(step: Step, given: Decimal | None, side: int) -> float:
    """Return a step's high (``side`` 1) or low (-1) as ``MarketScript`` fills it in."""
    if given is not None:
        return float(given)
    return float(step.close) + side * float(step.close * step.spread) / 2


def _hater_segment(b: _Builder, pair: Pair, spread: float, budget: int) -> int:
    """Script one edge-seeking segment of at most ``budget`` bars."""
    rng = b.rng
    kind = rng.choices(
        ["dip", "silent_dip", "hold", "breakout", "walk"], weights=[30, 15, 20, 15, 20]
    )[0]
    start = b.count(pair) - HISTORY_DAYS * BARS_PER_DAY
    close = b.closes[pair]
    if kind in {"dip", "silent_dip"}:
        stop = _stop_estimate(b.steps[pair])
        low = max(stop * (1 + rng.uniform(-0.01, 0.01)), _FLOOR)
        volume = 0.0 if kind == "silent_dip" else 1000.0
        b.push(pair, max(close, low * 1.01), spread=spread, low=low, volume=volume)
        length = 1
    elif kind == "hold":
        length = min(budget, rng.randint(2, 12))
        for _ in range(length):
            b.push(pair, close * (1 + rng.choice([-1, 0, 1]) * 0.0001), spread=0.002)
    elif kind == "breakout":
        recent = max(float(s.close) for s in b.steps[pair][-20:])
        jump = recent * rng.uniform(1.005, 1.04)
        b.push(pair, jump, spread=spread, high=jump * 1.001)
        length = 1
    else:
        length = min(budget, rng.randint(3, 12))
        for _ in range(length):
            b.push(pair, b.closes[pair] * math.exp(rng.gauss(0.002, 0.01)), spread=spread)
    b.notes.append(f"{pair} bar {start}: {kind} x{length}")
    return length


def _finish(
    seed: int, kind: str, b: _Builder, rng: random.Random, *, near: list[int]
) -> GeneratedPath:
    """Freeze the bars and draw the faults and restarts."""
    script = MarketScript(start=START, prices=dict.fromkeys(PAIRS, Decimal(100)), steps=b.steps)
    market = script.build()
    live_from = START + timedelta(days=HISTORY_DAYS) + _MINUTE * rng.randint(1, 60)
    book, klines, fx, late, duplicates = _faults(rng, market, live_from, b.notes)
    if kind == "hater":
        book, late = _hater_faults(rng, market, b, book, late)
    faults = _Faults(book=book, klines=klines, fx=fx, late=late, duplicates=duplicates)
    restarts = _restarts(rng, faults, rng.randint(1, 2), near=near)
    b.notes.append(f"restarts {[r.isoformat() for r in restarts]}")
    return GeneratedPath(
        seed=seed,
        kind=kind,
        market=market,
        live_from=live_from,
        book_outages=book,
        klines_outages=klines,
        fx_outages=fx,
        late_bars=late,
        duplicate_bars=duplicates,
        restarts=restarts,
        notes=b.notes,
    )


def _hater_faults(
    rng: random.Random, market: Market, b: _Builder, book: list[Window], late: list[LateBar]
) -> tuple[list[Window], list[LateBar]]:
    """Add the hater's faults: no live book on most silent bars; one bar published very late."""
    book = [
        *book,
        *(
            (bar.close_time, bar.close_time + 10 * _MINUTE)
            for pair in PAIRS
            for bar in market.h4[pair][HISTORY_DAYS * BARS_PER_DAY :]
            if bar.volume == 0 and rng.random() < 0.7
        ),
    ]
    late = list(late)
    if rng.random() < 0.4:
        pair = rng.choice(PAIRS)
        bar = market.h4[pair][HISTORY_DAYS * BARS_PER_DAY + rng.randrange(10, LIVE_BARS - 10)]
        # Shown after the next bar of the stream: serve stops on the gap and is booted again
        # once the bar is out (systemd restarts it; the boots in between would crash too).
        shown = bar.close_time + H4 + _MINUTE * rng.randint(10, 230)
        late.append((pair, KlineInterval.H4, bar.open_time, shown))
        b.notes.append(
            f"{pair} bar at {bar.open_time.isoformat()} shown very late, at {shown.isoformat()}"
        )
    return book, late
