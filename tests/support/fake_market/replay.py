"""Recorded Train-window bars as the fake market's script.

Issue #971, part 2. Instead of scripted steps, the service can replay 4h bars
ingested in the Binance Vision layout the backtests read (``/srv/hermes-data``
on server-01, ADR-023), through the same reader the backtests use.

Only the Train window replays. A window that reaches the Validation start
(2024-07-01, pre-registration §4) is refused before any file is opened, and
the reader sees only the month folders that overlap the window, so a
Validation or OOS archive in the same directory is never read.
"""

import asyncio
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from hermes.adapters._shared.wire import binance_symbol
from hermes.adapters.market_data.binance_historical import BinanceHistoricalAdapter
from hermes.domain.errors import HermesError
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.project_root import load_pre_registration
from tests.support.fake_market.script import BARS_PER_DAY, H4, Market, _day_bar

REPLAY_LIMIT: Final = load_pre_registration().data_windows().validation_start
"""Exclusive end of what may replay: the first instant of the Validation window."""


class ReplayError(ValueError):
    """A replay the fake market refuses: a bad window, or no bars to serve."""


def market_from_recording(
    data_dir: Path, pairs: Sequence[Pair], start: datetime, end: datetime
) -> Market:
    """Build a market from the recorded 4h bars of ``pairs`` opening in ``[start, end)``.

    Daily bars are aggregated from each UTC day's six recorded 4h bars, as the
    scripted market does; a day with a missing 4h bar has no daily bar.

    Args:
        data_dir: Root of the Binance Vision layout, holding ``klines/``.
        pairs: Pairs to replay; each needs at least one bar in the window.
        start: Open time of the first bar; 00:00 UTC.
        end: Exclusive end of the window; no later than :data:`REPLAY_LIMIT`.

    Returns:
        The replayed market.

    Raises:
        ReplayError: The window is malformed or reaches the Validation window,
            a pair has no bars in it, or an archive fails to read.

    """
    _check_window(start, end)
    if not pairs:
        raise ReplayError("replay needs at least one pair")
    h4: dict[Pair, list[Bar]] = {}
    with tempfile.TemporaryDirectory(prefix="fake-market-replay-") as view:
        for pair in pairs:
            _link_months(data_dir, Path(view), pair, start, end)
            reader = BinanceHistoricalAdapter.create(Path(view))
            try:
                bars = asyncio.run(_read(reader, pair, start, end))
            except HermesError as exc:
                raise ReplayError(f"recorded {pair} bars failed to read: {exc}") from exc
            if not bars:
                raise ReplayError(
                    f"no recorded 4h bars for {pair} from {start.isoformat()} "
                    f"to {end.isoformat()} in {data_dir}"
                )
            h4[pair] = bars
    return Market(start=start, h4=h4, d1={pair: _days(bars) for pair, bars in h4.items()})


def _check_window(start: datetime, end: datetime) -> None:
    for name, value in (("start", start), ("end", end)):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ReplayError(f"replay {name} must be UTC-aware, got {value.isoformat()}")
    if start != datetime.combine(start.date(), datetime.min.time(), tzinfo=UTC):
        raise ReplayError(f"replay start must be 00:00 UTC, got {start.isoformat()}")
    if end <= start:
        raise ReplayError(f"replay end {end.isoformat()} is not after start {start.isoformat()}")
    if end > REPLAY_LIMIT:
        raise ReplayError(
            f"replay end {end.isoformat()} reaches the Validation window, which starts "
            f"{REPLAY_LIMIT.isoformat()}: only Train-window bars replay"
        )


def _link_months(data_dir: Path, view: Path, pair: Pair, start: datetime, end: datetime) -> None:
    """Link into ``view`` only the archives of month folders that overlap the window."""
    symbol = binance_symbol(pair)
    source = data_dir / "klines" / symbol / KlineInterval.H4.value
    target = view / "klines" / symbol / KlineInterval.H4.value
    target.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        return
    for year in sorted(source.iterdir()):
        for month in sorted(year.iterdir()) if year.is_dir() else ():
            first = _month_start(year.name, month.name)
            if first is None or not month.is_dir():
                continue
            following = datetime(
                first.year + first.month // 12, first.month % 12 + 1, 1, tzinfo=UTC
            )
            if first < end and following > start:
                linked = target / year.name / month.name
                linked.mkdir(parents=True)
                for archive in month.glob("*.zip"):
                    (linked / archive.name).symlink_to(archive.resolve())


def _month_start(year: str, month: str) -> datetime | None:
    if not (year.isdigit() and month.isdigit() and 1 <= int(month) <= 12):
        return None
    return datetime(int(year), int(month), 1, tzinfo=UTC)


async def _read(
    reader: BinanceHistoricalAdapter, pair: Pair, start: datetime, end: datetime
) -> list[Bar]:
    bars: list[Bar] = []
    async for bar in reader.stream_bars(pair, KlineInterval.H4):
        if bar.open_time >= end:
            break
        if bar.open_time >= start:
            bars.append(bar)
    return bars


def _days(h4: list[Bar]) -> list[Bar]:
    by_day: dict[datetime, list[Bar]] = {}
    for bar in h4:
        day = datetime.combine(bar.open_time.date(), datetime.min.time(), tzinfo=UTC)
        by_day.setdefault(day, []).append(bar)
    return [
        _day_bar(six[0].pair, six)
        for day, six in by_day.items()
        if [b.open_time for b in six] == [day + H4 * i for i in range(BARS_PER_DAY)]
    ]
