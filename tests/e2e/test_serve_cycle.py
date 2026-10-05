"""End-to-end tests for one full ``hermes serve`` shadow cycle.

Each test boots the real composition root (:func:`build_composition` with the
repository's ``config/``, the three pre-registered pairs and the swing
strategy at its pre-registered parameters) over a SQLite ledger file, and
feeds it twenty days of recorded Binance spot bars (January 2025). Only the
bar source, the clock and the notification channels are replaced: the feed
replays the recording, the clock follows it, and the channels record what
they are sent. No network is touched and no exchange is wired, so no order
can be placed (ADR-003 §00 I-08).

Invariants protected here (issue #408, ADR-007 Tier 3):

- serve on recorded bars: the ledger, the channels and the per-bar journal
  lines all carry exactly the pinned signals of the recording, and each
  signal becomes exactly one simulated shadow fill (change
  ``353-fill-kernel-in-serve``: a BUY per entry, a SELL per exit);
- a stop that fires mid-run, then a restart, ends with the same ledger as an
  uninterrupted run: positions open at shutdown are restored, bars missed
  while down are caught up, nothing is re-notified;
- a skipped live bar stops the loop with exit 1, and the restart reconciles
  the ledger to the uninterrupted run;
- ``hermes status --json`` over the resulting ledger matches its snapshot.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import time
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from structlog.testing import capture_logs
from syrupy.assertion import SnapshotAssertion

from hermes.application.notification_router import RouterConfig
from hermes.application.signal_notifier import SignalNotifier
from hermes.application.status import UnitState
from hermes.domain.errors import NetworkError
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints import status as status_module
from hermes.infrastructure import sentry_integration
from hermes.infrastructure.config.loader import load_config
from tests.support.recorded_bars import (
    LIVE_FROM,
    LIVE_UNTIL,
    ReplayFeed,
    bar_at,
    closed_by,
    delivered_after,
    delivered_at,
)
from tests.support.serve_loop import (
    BTC,
    PROJECT_ROOT,
    FakeClock,
    RecordingChannel,
    drive,
    events,
)
from tests.unit.application.ledger.fake_fx import FakeFx

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(900)]

HANG_FLOOR_S = 20.0
"""Seconds every serve run gets before the per-bar allowance."""

PER_BAR_BUDGET_S = 0.5
"""Seconds allowed per live bar: about twenty times the 25 ms a bar takes on a
quiet CI runner (CD run 37237426620), so a busy runner sharing the VM does not
time the release gate out while a hang still does."""


def serve_budget(live_bars: int) -> float:
    """Return the hang guard, in seconds, for a serve run over ``live_bars`` bars."""
    return HANG_FLOOR_S + PER_BAR_BUDGET_S * live_bars


SERVE_TIMINGS: list[tuple[int, float]] = []
"""``(live bars, seconds)`` of every serve run; ``conftest.py`` prints them."""

Signal = tuple[str, str, str, str | None]
"""``(pair, kind, occurred_at, exit_reason)`` of one recorded signal."""

EXPECTED_SIGNALS: list[Signal] = [
    ("SOL/USDT", "entry", "2025-01-03T11:59:59.999000+00:00", None),
    ("BTC/USDT", "entry", "2025-01-03T19:59:59.999000+00:00", None),
    ("BTC/USDT", "exit", "2025-01-06T15:59:59.999000+00:00", "chandelier"),
    ("ETH/USDT", "entry", "2025-01-06T15:59:59.999000+00:00", None),
    ("ETH/USDT", "exit", "2025-01-07T15:59:59.999000+00:00", "chandelier"),
    ("SOL/USDT", "exit", "2025-01-07T15:59:59.999000+00:00", "chandelier"),
    ("BTC/USDT", "entry", "2025-01-14T11:59:59.999000+00:00", None),
    ("SOL/USDT", "entry", "2025-01-15T15:59:59.999000+00:00", None),
    ("SOL/USDT", "exit", "2025-01-18T19:59:59.999000+00:00", "chandelier"),
    ("SOL/USDT", "entry", "2025-01-19T07:59:59.999000+00:00", None),
    ("BTC/USDT", "exit", "2025-01-19T23:59:59.999000+00:00", "chandelier"),
    ("SOL/USDT", "exit", "2025-01-19T23:59:59.999000+00:00", "chandelier"),
    ("BTC/USDT", "entry", "2025-01-20T07:59:59.999000+00:00", None),
    ("BTC/USDT", "exit", "2025-01-20T19:59:59.999000+00:00", "chandelier"),
]
"""What the swing strategy signals on the recording, pinned once from a run.

Pre-registration §5 freezes the strategy parameters and the recording never
changes, so any difference here is a behaviour change in the serve path.
"""

STREAMS = 6
"""Three pairs times two intervals: the cursor rows a first boot writes."""

LIVE_BARS = len(delivered_after(LIVE_FROM))
"""Bars the feed delivers after the first boot (426)."""

RESTART_AT = delivered_at(
    bar_at(BTC, KlineInterval.H4, datetime.fromisoformat("2025-01-16T04:00Z"))
)
"""Second boot of the restart flow: the service was down for two 4h closes."""

GAP_BAR_OPEN = datetime.fromisoformat("2025-01-06T12:00Z")
"""Open of the BTC 4h bar the gap flow drops; it carries the first chandelier stop."""


@pytest.fixture
def sentry(monkeypatch: pytest.MonkeyPatch) -> list[Exception]:
    """Replace Sentry's capture with a recorder and return its list."""
    recorded: list[Exception] = []
    monkeypatch.setattr(sentry_integration, "capture", recorded.append)
    return recorded


_EUR_PER_QUOTE = Decimal("0.96")
"""A fixed USDT-to-EUR rate; the pinned results are pairs and sides, not EUR amounts."""


def _fixed_eur_rate() -> FakeFx:
    """Return an FX source that answers every day of the recording with one rate."""
    first = date(2024, 11, 1)
    days = (first + timedelta(days=n) for n in range(120))
    return FakeFx(rates={day: (_EUR_PER_QUOTE, day) for day in days})


class _RecordedBooks:
    """Depth source answering a deep, tight book around each pair's last recorded close.

    Stamped with the replay clock, so the risk overlay sees a fresh book, as it
    would from Binance during a live run; no call leaves the machine (#620, #637).
    """

    _HALF_SPREAD = Decimal("0.0001")

    def __init__(self, clock: FakeClock) -> None:
        """Bind the replay clock that marks what has closed."""
        self.clock = clock

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Return a two-level book one basis point either side of the last close."""
        last = max(
            (b for b in closed_by(self.clock.current) if b.pair == pair), key=lambda b: b.close_time
        )
        mid = last.close
        step = mid * self._HALF_SPREAD
        return Orderbook(
            pair=pair,
            bids=(
                OrderbookLevel(price=mid - step, quantity=Decimal("1000")),
                OrderbookLevel(price=mid - 2 * step, quantity=Decimal("10000")),
            ),
            asks=(
                OrderbookLevel(price=mid + step, quantity=Decimal("1000")),
                OrderbookLevel(price=mid + 2 * step, quantity=Decimal("10000")),
            ),
            snapshot_time=self.clock.current,
        )

    async def aclose(self) -> None:
        """Nothing to release."""


DEPTH_DOWN_FROM = datetime.fromisoformat("2025-01-05T00:00Z")
"""After the first BTC and SOL entries fill and before either exits."""


class _DepthDownFrom(_RecordedBooks):
    """Recorded depth until :data:`DEPTH_DOWN_FROM`, then an outage like Binance's."""

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Fail from the outage on; answer from the recording before it."""
        if self.clock.current >= DEPTH_DOWN_FROM:
            raise NetworkError(f"depth for {pair} unavailable")
        return await super().snapshot(pair)


def _boot(
    db: Path, feed: ReplayFeed, channel: RecordingChannel, books: _RecordedBooks | None = None
) -> serve_module.Composition:
    """Build the production composition over ``db`` with the replay feed and clock.

    The production config has no channel credentials here, so the recording
    channel stands in for Telegram. Depth comes from the recording and FX from
    one fixed rate for every day of it, so no call reaches Binance or the FX
    service (#620, #637). Everything else is what ``hermes serve`` wires.
    """
    config = load_config(PROJECT_ROOT / "config", env={})
    serve_module.check_startup(config, env={})
    composition = serve_module.build_composition(
        config,
        db_path=db,
        env={},
        feed=feed,
        clock=feed.clock,
        orderbooks=books if books is not None else _RecordedBooks(feed.clock),
        fx=_fixed_eur_rate(),
    )
    return dataclasses.replace(
        composition, signal_notifier=SignalNotifier(RouterConfig(telegram=channel, slack=None))
    )


def _serve(
    db: Path,
    *,
    boot_at: datetime,
    live: Sequence[Bar] | None = None,
    depth: Callable[[FakeClock], _RecordedBooks] | None = None,
) -> tuple[int, RecordingChannel]:
    """Boot at ``boot_at`` and serve until the replay idles or the run fails.

    Warm-up sees every bar delivered by ``boot_at``; ``live`` defaults to every
    bar delivered after it. ``depth`` builds the depth source on the replay
    clock; it defaults to :class:`_RecordedBooks`.

    Returns:
        The exit code of :func:`serve_module.run` and the channel it notified.

    """
    clock = FakeClock(boot_at)
    live_bars = delivered_after(boot_at) if live is None else live
    feed = ReplayFeed(closed_by(boot_at), live_bars, clock)
    channel = RecordingChannel()
    books = depth(clock) if depth is not None else None
    started = time.perf_counter()
    code = asyncio.run(
        drive(_boot(db, feed, channel, books), feed, timeout=serve_budget(len(live_bars)))
    )
    SERVE_TIMINGS.append((len(live_bars), time.perf_counter() - started))
    return code, channel


def _ledger_signals(db: Path) -> list[Signal]:
    """Return every SignalEmitted row of the ledger, oldest first."""
    rows = [json.loads(row["payload_json"]) for row in events(db, "SignalEmitted")]
    return [
        (
            f"{p['pair']['base']}/{p['pair']['quote']}",
            p["kind"],
            p["occurred_at"],
            p["exit_reason"],
        )
        for p in rows
    ]


def _ledger_trades(db: Path) -> list[tuple[str, str]]:
    """Return ``(pair, side)`` of every TradeExecuted row, oldest first.

    Fill times are the bar's delivery time, not the signal's close time, so the
    pin compares pair and side; the timing is covered in
    ``tests/integration/entrypoints/test_serve_fills.py``.
    """
    rows = [json.loads(row["payload_json"]) for row in events(db, "TradeExecuted")]
    return [(f"{p['pair']['base']}/{p['pair']['quote']}", p["side"]) for p in rows]


def _signal_order(signal: Signal) -> tuple[str, str]:
    """Sort key of :data:`EXPECTED_SIGNALS`: signal time, then pair."""
    return signal[2], signal[0]


def _notified(channel: RecordingChannel) -> list[Signal]:
    """Return the signals a channel was sent, in order."""
    return [
        (str(e.pair), e.kind.value, e.occurred_at.isoformat(), e.exit_reason)
        for e in channel.seen
        if isinstance(e, SignalEmitted)
    ]


def test_serve_on_recorded_bars_records_notifies_and_logs_exactly_the_pinned_signals(
    tmp_path: Path,
) -> None:
    """One uninterrupted run gives the pinned signals in ledger, channel and journal.

    Also: one BarProcessed row per stream cursor and per live bar, one
    ``serve.bar.processed`` line per live bar, and one simulated
    TradeExecuted row per signal: a BUY per entry, a SELL per exit (change
    ``353-fill-kernel-in-serve``; no exchange is wired, so none is a real order).
    """
    db = tmp_path / "ledger.sqlite3"

    with capture_logs() as logs:
        code, channel = _serve(db, boot_at=LIVE_FROM)

    bar_lines = [line for line in logs if line["event"] == "serve.bar.processed"]
    logged_signals = [
        (line["pair"], line["signal"], line["close_time"])
        for line in bar_lines
        if line["signal"] != "none"
    ]
    assert {
        "exit_code": code,
        "ledger": _ledger_signals(db),
        "notified": _notified(channel),
        "logged": logged_signals,
        "bar_lines": len(bar_lines),
        "bar_rows": len(events(db, "BarProcessed")),
        "trades": sorted(_ledger_trades(db)),
    } == {
        "exit_code": 0,
        "ledger": EXPECTED_SIGNALS,
        "notified": EXPECTED_SIGNALS,
        "logged": [(pair, kind, at) for pair, kind, at, _ in EXPECTED_SIGNALS],
        "bar_lines": LIVE_BARS,
        "bar_rows": STREAMS + LIVE_BARS,
        "trades": sorted(
            (pair, "BUY" if kind == "entry" else "SELL") for pair, kind, _, _ in EXPECTED_SIGNALS
        ),
    }


def test_restart_after_a_stop_rebuilds_open_positions_and_matches_an_uninterrupted_run(
    tmp_path: Path,
) -> None:
    """A run that sees a stop, stops with BTC and SOL open, then restarts, equals one run.

    The first run hits BTC's chandelier stop (2025-01-06) and shuts down after
    the BTC and SOL entries of 14-15 January. The second boot comes two 4h
    closes later: it must restore both open positions from the ledger (or it
    could not emit their exits on the 18th and 19th), catch up the two closes
    it missed, and notify nothing it already notified.
    """
    db = tmp_path / "ledger.sqlite3"
    shutdown_at = RESTART_AT - timedelta(hours=8)
    first_live = [b for b in delivered_after(LIVE_FROM) if delivered_at(b) <= shutdown_at]

    first_code, first_channel = _serve(db, boot_at=LIVE_FROM, live=first_live)
    after_first = _ledger_signals(db)
    second_code, second_channel = _serve(db, boot_at=RESTART_AT)

    assert {
        "codes": (first_code, second_code),
        "first_run": after_first,
        "ledger": _ledger_signals(db),
        "notified": _notified(first_channel) + _notified(second_channel),
        "bar_rows": len(events(db, "BarProcessed")),
    } == {
        "codes": (0, 0),
        "first_run": EXPECTED_SIGNALS[:8],
        "ledger": EXPECTED_SIGNALS,
        "notified": EXPECTED_SIGNALS,
        "bar_rows": STREAMS + LIVE_BARS,
    }


def test_a_skipped_bar_stops_the_loop_and_the_restart_reconciles_the_ledger(
    tmp_path: Path, sentry: list[Exception]
) -> None:
    """Dropping the BTC 4h bar that carries a stop exits 1; the restart fills the hole.

    The first run must fail loudly (exit 1, a BarGapError sent to Sentry) when
    the next BTC 4h bar shows the gap, before recording the stop. Restarting
    from the ledger cursor, as systemd does, must recover the missing bar from
    warm-up and end with the uninterrupted run's ledger, less one row. ETH's
    entry on the same close was recorded before the failure, so the rows are
    compared in signal order rather than insertion order. With BTC's stop not
    yet seen, BTC is still held when ETH's entry comes, so sizing refuses it:
    ETH stays flat and raises no exit on 7 January (#707).
    """
    db = tmp_path / "ledger.sqlite3"
    dropped = bar_at(BTC, KlineInterval.H4, GAP_BAR_OPEN)
    first_live = [b for b in delivered_after(LIVE_FROM) if b != dropped]
    gap_seen_at = delivered_at(bar_at(BTC, KlineInterval.H4, GAP_BAR_OPEN + timedelta(hours=4)))

    first_code, _ = _serve(db, boot_at=LIVE_FROM, live=first_live)
    before_restart = _ledger_signals(db)
    second_code, _ = _serve(db, boot_at=gap_seen_at)

    assert {
        "first_code": first_code,
        "sentry": [type(exc).__name__ for exc in sentry],
        "before_restart": before_restart,
        "second_code": second_code,
        "ledger": sorted(_ledger_signals(db), key=_signal_order),
        "bar_rows": len(events(db, "BarProcessed")),
    } == {
        "first_code": 1,
        "sentry": ["BarGapError"],
        "before_restart": [*EXPECTED_SIGNALS[:2], EXPECTED_SIGNALS[3]],
        "second_code": 0,
        "ledger": [s for s in EXPECTED_SIGNALS if s[:2] != ("ETH/USDT", "exit")],
        "bar_rows": STREAMS + LIVE_BARS,
    }


def test_status_json_after_a_serve_cycle_matches_its_snapshot(
    tmp_path: Path, snapshot: SnapshotAssertion
) -> None:
    """``hermes status --json`` over the ledger one full cycle wrote is stable.

    The report is read just after the last recorded close was delivered, with
    systemd reporting both units running, so every stream must be on time.
    """
    db = tmp_path / "ledger.sqlite3"
    _serve(db, boot_at=LIVE_FROM)
    out = io.StringIO()

    code = status_module.main(
        ["--db", str(db), "--json"],
        stream=out,
        now=LIVE_UNTIL + timedelta(seconds=10),
        unit_state=lambda: UnitState.RUNNING,
        bot_state=lambda: UnitState.RUNNING,
        # A link that never exists: the report must not depend on the host's
        # /opt/hermes/current, so the release reads as unknown everywhere.
        release_link=tmp_path / "no-current-release",
    )

    assert (code, json.loads(out.getvalue())) == snapshot


def test_depth_outage_lets_open_positions_exit_but_blocks_new_entries(tmp_path: Path) -> None:
    """With depth down, exits still fill on the §6.2 book and new entries are declined.

    The standing decision: with a stale or missing order book hermes skips new
    entries (RO-04 halts them) but never strands an open position. BTC and SOL
    enter while depth answers; depth fails from 2025-01-05; both still exit,
    and every later entry signal is declined.
    """
    db = tmp_path / "ledger.sqlite3"

    code, _ = _serve(db, boot_at=LIVE_FROM, depth=_DepthDownFrom)

    assert (code, _ledger_trades(db)) == (
        0,
        [("SOL/USDT", "BUY"), ("BTC/USDT", "BUY"), ("BTC/USDT", "SELL"), ("SOL/USDT", "SELL")],
    )
