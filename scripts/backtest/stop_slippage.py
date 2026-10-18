#!/usr/bin/env python3
"""Stop-exit slippage on 1-minute Train bars (issue #1104).

Every backtest fills a resting long stop at ``min(stop, 4h open)`` and then
charges a fixed exit slippage (pre-registration v1 §6.2: 15 bps). This script
measures, from 1-minute bars, how far below that model fill the market really
traded when the stop was first reached.

It runs no strategy and logs no trial. Stop levels are synthetic: for each
closed 4h bar, a resting stop at ``previous close - m x ATR(22, 4h)`` for each
multiple ``m``. An event is a 4h bar whose low reaches that stop; the touch
minute is the first 1-minute bar inside it whose low reaches the stop.

A 1-minute bar can't show the order of trades inside the minute, so each event
gets two bounds, both in bps below the model fill:

* optimistic: fill at ``min(stop, touch-minute open)``; only a minute that
  opens through the stop costs anything.
* pessimistic: fill at the touch minute's low; the worst trade the minute saw.

With ``--trade-days N``, the script also takes ``N`` event days per pair,
spread evenly across Train, downloads each day's aggregated
trades from the public archive, and takes the first trade at or below the stop
inside the event's 4h bar as the trigger price. That is the closest the public
data gets to a real stop-market fill; the table reports it in bps below the
model fill. Each day file is checksum-verified, read once and deleted.

Only the Train window of the swing-mvp1 data manifest is measured: reading
stops at the first bar after it, so a data directory that also holds
Validation months (the default ingest) works unchanged. Data comes from
``scripts/data/ingest_binance_vision.py --interval 4h --interval 1m``.

Usage:
    python scripts/backtest/stop_slippage.py --data-dir PATH [--pair BTC/USDT]...
        [--multiple 2.0]... [--threshold-bps 15] [--trade-days 60]

Exit codes:
    0 - table printed
    1 - missing or unreadable data
"""

import argparse
import asyncio
import csv
import hashlib
import io
import json
import sys
import tempfile
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final

import httpx

from hermes.adapters.market_data.binance_historical import BinanceHistoricalAdapter
from hermes.domain.errors import HermesError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.swing.engines.atr import AtrEngine
from hermes.domain.value_objects.pair import Pair

ATR_PERIOD: Final[int] = 22
DEFAULT_MULTIPLES: Final[tuple[Decimal, ...]] = (Decimal("1.5"), Decimal("2.0"), Decimal("3.0"))
DEFAULT_PAIRS: Final[tuple[str, ...]] = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
DEFAULT_THRESHOLD_BPS: Final[Decimal] = Decimal(15)
PERCENTILES: Final[tuple[int, ...]] = (50, 90, 99)
EXPERIMENT_MANIFEST: Final[Path] = Path(__file__).resolve().parents[2] / (
    "docs/experiments/swing-mvp1/data-manifest.json"
)
_BPS: Final[Decimal] = Decimal(10_000)
_ZERO: Final[Decimal] = Decimal(0)
_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)
DAILY_AGG_TRADES_URL: Final[str] = "https://data.binance.vision/data/spot/daily/aggTrades"
_HTTP_TIMEOUT_SECONDS: Final[int] = 120
# AtrEngine reads only the last 2p + 1 bars; passing just those keeps the scan linear.
_ATR_WINDOW: Final[int] = 2 * ATR_PERIOD + 1


@dataclass(frozen=True, slots=True)
class StopEvent:
    """A 4h bar whose low reached a resting long stop."""

    multiple: Decimal
    bar_open: datetime
    bar_close: datetime
    stop: Decimal
    model_fill: Decimal


@dataclass(frozen=True, slots=True)
class Minute:
    """The fields of a 1-minute bar the measurement reads."""

    open_time: datetime
    open: Decimal
    low: Decimal


@dataclass(frozen=True, slots=True)
class Slippage:
    """Bounds on how far below the model fill the stop really filled, in bps."""

    optimistic_bps: Decimal
    pessimistic_bps: Decimal


@dataclass(frozen=True, slots=True)
class Summary:
    """Slippage distribution for one pair and one stop multiple."""

    pair: str
    multiple: Decimal
    events: int
    unmatched: int
    optimistic: dict[int, Decimal]
    pessimistic: dict[int, Decimal]
    optimistic_over: Decimal
    pessimistic_over: Decimal


def train_window(manifest_path: Path = EXPERIMENT_MANIFEST) -> tuple[datetime, datetime]:
    """Return the Train split's ``(start, end)`` from the experiment data manifest."""
    train = json.loads(manifest_path.read_text(encoding="utf-8"))["splits"]["train"]
    return datetime.fromisoformat(train["start"]), datetime.fromisoformat(train["end"])


def stop_events(bars: Sequence[Bar], multiples: Iterable[Decimal]) -> list[StopEvent]:
    """Return every stop event in ``bars`` for each multiple.

    The stop resting during bar ``t`` uses only bars before it: the previous
    close and ATR(22) over the bars up to ``t - 1``.

    Args:
        bars: Closed 4h bars in ascending ``open_time`` order.
        multiples: ATR multiples below the previous close.
    """
    chosen = tuple(multiples)
    events: list[StopEvent] = []
    for t in range(ATR_PERIOD + 1, len(bars)):
        atr = AtrEngine.value(bars[max(0, t - _ATR_WINDOW) : t], period=ATR_PERIOD)
        if atr is None:
            continue
        bar, previous_close = bars[t], bars[t - 1].close
        for m in chosen:
            stop = previous_close - m * atr
            if stop > _ZERO and bar.low <= stop:
                events.append(
                    StopEvent(m, bar.open_time, bar.close_time, stop, min(stop, bar.open))
                )
    return events


def measure(event: StopEvent, minutes: Sequence[Minute]) -> Slippage | None:
    """Return the slippage bounds of ``event`` from its 1-minute bars.

    Args:
        event: The stop event.
        minutes: 1-minute bars of the event's 4h bar, ascending.

    Returns:
        The bounds, or ``None`` when no minute reaches the stop (a gap in the
        minute archive).
    """
    for minute in minutes:
        if event.bar_open <= minute.open_time < event.bar_close and minute.low <= event.stop:
            optimistic_fill = min(event.stop, minute.open)
            return Slippage(
                _bps_below(event.model_fill, optimistic_fill),
                _bps_below(event.model_fill, minute.low),
            )
    return None


def percentile(values: Sequence[Decimal], q: int) -> Decimal:
    """Return the nearest-rank ``q``-th percentile of ``values`` (``0`` when empty)."""
    if not values:
        return _ZERO
    ordered = sorted(values)
    rank = max(1, -(-q * len(ordered) // 100))
    return ordered[rank - 1]


def summarize(
    pair: str,
    multiple: Decimal,
    measured: Sequence[Slippage | None],
    threshold_bps: Decimal,
) -> Summary:
    """Summarize the measured events of one pair and multiple."""
    found = [s for s in measured if s is not None]
    optimistic = [s.optimistic_bps for s in found]
    pessimistic = [s.pessimistic_bps for s in found]
    return Summary(
        pair=pair,
        multiple=multiple,
        events=len(found),
        unmatched=len(measured) - len(found),
        optimistic={q: percentile(optimistic, q) for q in PERCENTILES},
        pessimistic={q: percentile(pessimistic, q) for q in PERCENTILES},
        optimistic_over=_share_over(optimistic, threshold_bps),
        pessimistic_over=_share_over(pessimistic, threshold_bps),
    )


@dataclass(frozen=True, slots=True)
class TradeSummary:
    """Trigger-trade slippage for one pair and one stop multiple."""

    pair: str
    multiple: Decimal
    events: int
    unmatched: int
    slippage: dict[int, Decimal]
    over: Decimal


def first_trade_at_or_below(
    trades: Iterable[tuple[datetime, Decimal]], event: StopEvent
) -> Decimal | None:
    """Return the price of the first trade inside the event's bar at or below its stop.

    Args:
        trades: ``(time, price)`` in ascending time.
        event: The stop event.

    Returns:
        The trigger price, or ``None`` when no trade in the bar reaches the stop.
    """
    for at, price in trades:
        if at >= event.bar_close:
            return None
        if at >= event.bar_open and price <= event.stop:
            return price
    return None


def trigger_slippage(event: StopEvent, price: Decimal) -> Decimal:
    """Return how far ``price`` is below the event's model fill, in bps."""
    return _bps_below(event.model_fill, price)


def sample_days(events: Sequence[StopEvent], count: int) -> list[date]:
    """Return up to ``count`` distinct event days, spread evenly across the window.

    Evenly spaced rather than random, so reruns pick the same days and every
    year of the window is represented.
    """
    days = sorted({e.bar_open.date() for e in events})
    if count >= len(days):
        return days
    return [days[i * len(days) // count] for i in range(count)]


def summarize_trades(
    pair: str,
    multiple: Decimal,
    measured: Sequence[Decimal | None],
    threshold_bps: Decimal,
) -> TradeSummary:
    """Summarize the trigger-trade slippage of one pair and multiple."""
    found = [v for v in measured if v is not None]
    return TradeSummary(
        pair=pair,
        multiple=multiple,
        events=len(found),
        unmatched=len(measured) - len(found),
        slippage={q: percentile(found, q) for q in PERCENTILES},
        over=_share_over(found, threshold_bps),
    )


def render_trades(summaries: Sequence[TradeSummary], threshold_bps: Decimal) -> str:
    """Render the trigger-trade summaries as a Markdown table."""
    cols = " | ".join(f"p{q}" for q in PERCENTILES)
    lines = [
        f"| pair | m | events | unmatched | trigger {cols} | > {threshold_bps} bps |",
        "|" + " --- |" * (5 + len(PERCENTILES)),
    ]
    for s in summaries:
        values = " | ".join(_fmt(s.slippage[q]) for q in PERCENTILES)
        lines.append(
            f"| {s.pair} | {s.multiple} | {s.events} | {s.unmatched} | {values} | {s.over:.1%} |"
        )
    return "\n".join(lines)


def read_trades(archive: Path) -> Iterator[tuple[datetime, Decimal]]:
    """Yield ``(time, price)`` from one aggregated-trades archive, header rows skipped."""
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            with bundle.open(name) as raw:
                for row in csv.reader(io.TextIOWrapper(raw, encoding="utf-8")):
                    if not row or not row[0].isdigit():
                        continue
                    yield _EPOCH + timedelta(milliseconds=int(row[5])), Decimal(row[1])


def render(summaries: Sequence[Summary], threshold_bps: Decimal) -> str:
    """Render the summaries as a Markdown table."""
    cols = " | ".join(f"p{q}" for q in PERCENTILES)
    head = (
        f"| pair | m | events | unmatched | optimistic {cols} | > {threshold_bps} bps "
        f"| pessimistic {cols} | > {threshold_bps} bps |"
    )
    lines = [head, "|" + " --- |" * (6 + 2 * len(PERCENTILES))]
    for s in summaries:
        opt = " | ".join(_fmt(s.optimistic[q]) for q in PERCENTILES)
        pes = " | ".join(_fmt(s.pessimistic[q]) for q in PERCENTILES)
        lines.append(
            f"| {s.pair} | {s.multiple} | {s.events} | {s.unmatched} | {opt} "
            f"| {s.optimistic_over:.1%} | {pes} | {s.pessimistic_over:.1%} |"
        )
    return "\n".join(lines)


def _bps_below(reference: Decimal, price: Decimal) -> Decimal:
    return (reference - price) / reference * _BPS


def _share_over(values: Sequence[Decimal], threshold: Decimal) -> Decimal:
    if not values:
        return _ZERO
    return Decimal(sum(1 for v in values if v > threshold)) / Decimal(len(values))


def _fmt(value: Decimal) -> str:
    return f"{value:.1f}"


async def _collect(
    adapter: BinanceHistoricalAdapter,
    pair: Pair,
    interval: KlineInterval,
    start: datetime,
    end: datetime,
) -> list[Bar]:
    bars: list[Bar] = []
    async for bar in adapter.stream_bars(pair, interval):
        if bar.open_time > end:
            break
        if bar.open_time >= start:
            bars.append(bar)
    return bars


def read_minutes(folder: Path) -> Iterator[Minute]:
    """Yield the 1-minute bars archived under ``folder``, oldest first.

    Read straight from the archive CSVs rather than through ``Bar``: the
    archive holds a few malformed rows (BTCUSDT 2020-12-21, a close time before
    its open time) that ``Bar`` rightly refuses, and the measurement only
    needs the open time, open and low. Header rows are skipped. Times are read
    as milliseconds, the Train-era format; the later microsecond format lands
    far past Train, where reading stops.

    Raises:
        OSError: ``folder`` has no archives.
    """
    archives = sorted(folder.rglob("*.zip"))
    if not archives:
        raise OSError(f"no 1m archives under {folder}")
    for archive in archives:
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                with bundle.open(name) as raw:
                    for row in csv.reader(io.TextIOWrapper(raw, encoding="utf-8")):
                        if not row or not row[0].isdigit():
                            continue
                        opened = _EPOCH + timedelta(milliseconds=int(row[0]))
                        yield Minute(opened, Decimal(row[1]), Decimal(row[3]))


def _fetch_day(client: httpx.Client, symbol: str, day: date, folder: Path) -> Path:
    name = f"{symbol}-aggTrades-{day.isoformat()}.zip"
    url = f"{DAILY_AGG_TRADES_URL}/{symbol}/{name}"
    expected = client.get(url + ".CHECKSUM").raise_for_status().text.split()[0]
    path = folder / name
    digest = hashlib.sha256()
    with client.stream("GET", url) as response, path.open("wb") as out:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != expected:
        raise OSError(f"checksum mismatch for {name}")
    return path


def _trade_slippage(
    symbol: str, events: Sequence[StopEvent], days: Sequence[date], end: datetime
) -> dict[StopEvent, Decimal | None]:
    by_day: dict[date, list[StopEvent]] = defaultdict(list)
    for e in events:
        if e.bar_open.date() in days:
            by_day[e.bar_open.date()].append(e)
    measured: dict[StopEvent, Decimal | None] = {}
    with (
        httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client,
        tempfile.TemporaryDirectory() as tmp,
    ):
        for day in days:
            if datetime.combine(day, datetime.min.time(), tzinfo=UTC) > end:
                raise ValueError(f"{symbol} trades on {day} are past Train")
            path = _fetch_day(client, symbol, day, Path(tmp))
            for e in by_day[day]:
                price = first_trade_at_or_below(read_trades(path), e)
                measured[e] = None if price is None else trigger_slippage(e, price)
            path.unlink()
    return measured


def minutes_by_bar(
    minutes: Iterable[Minute], events: Sequence[StopEvent], start: datetime, end: datetime
) -> dict[datetime, list[Minute]]:
    """Group the minutes that fall inside an event's 4h bar by that bar's open time.

    Reading stops at the first minute after ``end``: archives are in time
    order, so nothing past the Train window is grouped or read further.
    """
    wanted = {e.bar_open: e.bar_close for e in events}
    by_bar: dict[datetime, list[Minute]] = defaultdict(list)
    opens = sorted(wanted)
    i = 0
    for minute in minutes:
        if minute.open_time > end:
            break
        if minute.open_time < start:
            continue
        while i < len(opens) and wanted[opens[i]] <= minute.open_time:
            i += 1
        if i < len(opens) and opens[i] <= minute.open_time:
            by_bar[opens[i]].append(minute)
    return by_bar


async def _run(
    data_dir: Path,
    pairs: Sequence[str],
    multiples: Sequence[Decimal],
    threshold_bps: Decimal,
    trade_days: int,
) -> tuple[list[Summary], list[TradeSummary]]:
    start, end = train_window()
    adapter = BinanceHistoricalAdapter.create(data_dir)
    summaries: list[Summary] = []
    trade_summaries: list[TradeSummary] = []
    for name in pairs:
        pair = Pair.from_str(name).unwrap()
        bars = await _collect(adapter, pair, KlineInterval.H4, start, end)
        events = stop_events(bars, multiples)
        folder = data_dir / "klines" / name.replace("/", "") / KlineInterval.M1.value
        minutes = minutes_by_bar(read_minutes(folder), events, start, end)
        for m in multiples:
            chosen = [e for e in events if e.multiple == m]
            measured = [measure(e, minutes.get(e.bar_open, [])) for e in chosen]
            summaries.append(summarize(name, m, measured, threshold_bps))
        if trade_days > 0:
            days = sample_days(events, trade_days)
            symbol = name.replace("/", "")
            by_event = _trade_slippage(symbol, events, days, end)
            for m in multiples:
                slips = [v for e, v in by_event.items() if e.multiple == m]
                trade_summaries.append(summarize_trades(name, m, slips, threshold_bps))
    return summaries, trade_summaries


def main(argv: list[str] | None = None) -> int:
    """Print the slippage table for the Train window."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--pair", action="append", choices=list(DEFAULT_PAIRS))
    parser.add_argument("--multiple", action="append", type=Decimal)
    parser.add_argument("--threshold-bps", type=Decimal, default=DEFAULT_THRESHOLD_BPS)
    parser.add_argument("--trade-days", type=int, default=0)
    args = parser.parse_args(argv)
    pairs = tuple(args.pair) if args.pair else DEFAULT_PAIRS
    multiples = tuple(args.multiple) if args.multiple else DEFAULT_MULTIPLES
    try:
        summaries, trade_summaries = asyncio.run(
            _run(args.data_dir, pairs, multiples, args.threshold_bps, args.trade_days)
        )
    except (ValueError, OSError, HermesError, httpx.HTTPError) as exc:
        print(f"stop_slippage: {exc}", file=sys.stderr)
        return 1
    print(render(summaries, args.threshold_bps))
    if trade_summaries:
        print()
        print(render_trades(trade_summaries, args.threshold_bps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
