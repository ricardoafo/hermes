"""Serve alerts execution-quality control-chart breaches, and trades exactly as before (#1181).

Every executed shadow fill feeds the EWMA + CUSUM charts on fee (bps of
notional), slippage (bps from the book mid) and decision-to-fill latency (ms)
of change ``362-control-charts`` (ADR-005 Category B item 3). Invariants:

- the first fill of a breach episode is one ``WARNING`` anomaly in the ledger
  and one alert on every channel; later fills of the same episode add nothing
  (#362 D-2);
- a run that stays in control records nothing;
- a restart replays the ledger's past fills, so it keeps the baseline and does
  not alert an episode that was already alerted;
- a fill that did not measure its latency still feeds the fee and slippage
  charts and leaves the latency chart alone;
- a monitor error is logged and never stops a fill from being recorded;
- alert only (#362 D-2): signals, fills and halts are identical whether the
  charts breach or not.

Everything from the feed to the SQLite ledger is real except the scripted
feed, a clock stepped to a few seconds after each bar's close, and a depth
source answering a one-level book around the close with a scripted
half-spread. The charts run with a four-fill baseline so a few trades cover
Phase I; the parameters are otherwise the textbook defaults of #362 D-1.

The scripted market repeats one cycle of twelve 4h bars after eleven flat bars
at 1000: a breakout to 2000 (the entry fills against a 2000 mid), two flat
bars, a crash to 500 (the exit fills against a 500 mid) and eight flat bars at
1000. The small-period strategy of ``tests/support/serve_loop.py`` enters on
every breakout and exits on every crash, so each cycle is one BUY and one SELL.
"""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from hermes.adapters.clock import SimulatedClock
from hermes.application.monitoring.execution_quality import (
    ExecutionObservation,
    ExecutionQualityMonitor,
)
from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.bar_schedule import close_time_of, delivery_key
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.orderbook import Orderbook, OrderbookLevel
from hermes.domain.monitoring.control_chart import ChartParameters
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import serve as serve_module
from tests.support.serve_loop import (
    BTC,
    H4_BASE,
    FakeFeed,
    RecordingChannel,
    build,
    d1_bar,
    events,
    run_once,
    signal_kinds,
)

pytestmark = pytest.mark.integration

_H4 = timedelta(hours=4)
_DELAY = timedelta(seconds=6)
_FLAT_UNTIL = 11
_CYCLE = 12
_BASELINE = ChartParameters(baseline_size=4)
_NEVER_BREACHES = ChartParameters(baseline_size=4, ewma_l=1e9, cusum_h=1e9)
_SLIPPAGE = "execution_quality.slippage_bps"

_CALM = ("4", "6")
"""Entry and exit slippage in bps of a cycle in control."""
_STEPPED = ("12", "12")
"""Entry and exit slippage in bps of a cycle after the step."""

_STEP_REASON = (
    "slippage_bps out of control (ewma_high, cusum_high): "
    "12.00 bps against a baseline of 5.00 ± 1.15 bps"
)
"""The first stepped fill after a baseline of 4, 6, 4, 6 bps, worked by hand.

Baseline: mean 5, sample sigma sqrt((1 + 1 + 1 + 1) / 3) = 1.1547.
First Phase-II point, 12 bps (#362 D-1: λ 0.2, L 3, k 0.5, h 5):

- EWMA = 0.2 * 12 + 0.8 * 5 = 6.4; its limit is
  5 + 3 * 1.1547 * sqrt(0.2 / 1.8 * (1 - 0.8 ** 2)) = 5 + 0.6928 = 5.6928, so
  EWMA_HIGH;
- z = (12 - 5) / 1.1547 = 6.062; CUSUM+ = 6.062 - 0.5 = 5.562 > 5, so
  CUSUM_HIGH.

The second stepped fill keeps both out (CUSUM+ 11.12), so the episode stays
open and nothing else is alerted.
"""


@pytest.fixture(autouse=True)
def _uncached_serve_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give serve a logger ``capture_logs`` can see (see ``test_serve_fills.py``)."""
    monkeypatch.setattr(serve_module, "_log", structlog.get_logger(serve_module.__name__))
    cached = structlog.get_config()["cache_logger_on_first_use"]
    structlog.configure(cache_logger_on_first_use=False)
    yield
    structlog.configure(cache_logger_on_first_use=cached)


def _bar(index: int) -> Bar:
    """Return 4h bar ``index`` of the repeating cycle described in the module docstring."""
    step = (index - _FLAT_UNTIL) % _CYCLE if index >= _FLAT_UNTIL else _CYCLE
    close, low = "1000", "998"
    if step < 3:
        close, low = "2000", "1998"
    elif step == 3:
        close, low = "500", "100"
    open_time = H4_BASE + _H4 * index
    price = Decimal(close)
    return Bar(
        pair=BTC,
        interval=KlineInterval.H4,
        open_time=open_time,
        open=price,
        high=price + 2,
        low=Decimal(low),
        close=price,
        volume=Decimal("100"),
        close_time=close_time_of(open_time, KlineInterval.H4),
    )


def _entry_bar(cycle: int) -> int:
    return _FLAT_UNTIL + _CYCLE * cycle


@dataclass(frozen=True)
class _Fill:
    """What the book looks like when one fill's bar arrives."""

    slippage_bps: str
    delay: timedelta = _DELAY


class _Books:
    """Depth source answering one deep level each side of ``mid``, ``half`` away."""

    def __init__(self, clock: SimulatedClock) -> None:
        """Start with no price; the feed sets it per bar."""
        self.clock = clock
        self.mid = Decimal("1000")
        self.half = Decimal("1")

    async def snapshot(self, pair: Pair) -> Orderbook:
        """Return the scripted book stamped now."""
        return Orderbook(
            pair=pair,
            bids=(OrderbookLevel(price=self.mid - self.half, quantity=Decimal("1000")),),
            asks=(OrderbookLevel(price=self.mid + self.half, quantity=Decimal("1000")),),
            snapshot_time=self.clock.now(),
        )

    async def aclose(self) -> None:
        """Nothing to release."""


class _Feed(FakeFeed):
    """Delivers each live bar after its close, with the book its fill walks."""

    def __init__(
        self,
        clock: SimulatedClock,
        books: _Books,
        warm: Sequence[Bar],
        live_bars: Sequence[Bar],
        fills: Mapping[int, _Fill],
    ) -> None:
        """Script the bars and, per fill bar index, the slippage and delivery delay."""
        super().__init__(warm=warm, live_bars=live_bars)
        self.clock = clock
        self.books = books
        self.fills = fills

    async def stream_closed_bars(
        self,
        pairs: Sequence[Pair],
        intervals: Sequence[KlineInterval],
        *,
        resume_after: Mapping[tuple[Pair, KlineInterval], datetime] | None = None,
    ) -> AsyncIterator[Bar]:
        """Step the clock and the book to each bar, then yield it."""
        for bar in self.live_bars:
            index = (bar.open_time - H4_BASE) // _H4
            fill = self.fills.get(index, _Fill("1"))
            self.clock.set(bar.close_time + fill.delay)
            self.books.mid = bar.close
            self.books.half = bar.close * Decimal(fill.slippage_bps) / Decimal("10000")
            yield bar
        self.drained.set()
        await asyncio.Event().wait()


def _script(cycles: Sequence[tuple[str, str]], *, first: int = 0) -> dict[int, _Fill]:
    """Map each cycle's entry and exit bar to its slippage."""
    fills: dict[int, _Fill] = {}
    for offset, (entry, exit_) in enumerate(cycles):
        start = _entry_bar(first + offset)
        fills[start] = _Fill(entry)
        fills[start + 3] = _Fill(exit_)
    return fills


def _boot(
    db: Path,
    cycles: Sequence[tuple[str, str]],
    *,
    first: int = 0,
    monitor: ExecutionQualityMonitor | None = None,
    telegram: RecordingChannel | None = None,
    fills: Mapping[int, _Fill] | None = None,
) -> int:
    """Run one boot over cycles ``first ..`` of the market and return the exit code."""
    start = _entry_bar(first)
    end = _entry_bar(first + len(cycles))
    clock = SimulatedClock(start=_bar(start - 1).close_time + _DELAY)
    books = _Books(clock)
    warm = sorted(
        [d1_bar(i) for i in range(12)] + [_bar(i) for i in range(start)], key=delivery_key
    )
    feed = _Feed(
        clock,
        books,
        warm,
        [_bar(i) for i in range(start, end)],
        _script(cycles, first=first) if fills is None else fills,
    )
    composition = replace(
        build(db, feed, clock.now(), telegram=telegram, books=books, clock=clock),
        execution_quality=monitor if monitor is not None else ExecutionQualityMonitor(_BASELINE),
    )
    return run_once(composition, feed)


def _quality_rows(db: Path) -> list[tuple[str, str, str]]:
    """Return ``(source, severity, reason)`` of every execution-quality anomaly, oldest first."""
    payloads = (json.loads(row["payload_json"]) for row in events(db, "AnomalyDetected"))
    return [
        (p["source"], p["severity"], p["reason"])
        for p in payloads
        if p["source"].startswith("execution_quality.")
    ]


def _quality_alerts(channel: RecordingChannel) -> list[tuple[str, str]]:
    """Return ``(source, reason)`` of every execution-quality alert the channel received."""
    return [
        (e.source, e.reason)
        for e in channel.seen
        if isinstance(e, AnomalyDetected) and e.source.startswith("execution_quality.")
    ]


def _trades(db: Path) -> list[tuple[str, str, str, str]]:
    """Return side, quantity, price and fee of every recorded trade, oldest first."""
    payloads = (json.loads(row["payload_json"]) for row in events(db, "TradeExecuted"))
    return [
        (p["side"], json.dumps(p["qty"]), json.dumps(p["price"]), json.dumps(p["fee"]))
        for p in payloads
    ]


def _decisions(db: Path) -> list[tuple[str, str, str]]:
    """Return the time, kind and prices of every recorded signal, oldest first."""
    decisions = []
    for row in events(db, "SignalEmitted"):
        p = json.loads(row["payload_json"])
        fields = {k: p[k] for k in ("kind", "entry_price", "stop_price", "exit_reason")}
        decisions.append((row["occurred_at"], p["kind"], json.dumps(fields, sort_keys=True)))
    return decisions


def _rows(db: Path, event_type: str) -> int:
    connection = sqlite3.connect(db)
    try:
        (count,) = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = ?", (event_type,)
        ).fetchone()
    finally:
        connection.close()
    return int(count)


# SEV-1: S12 — the execution-quality alert is a safety control; a breach must not pass silently.
def test_a_slippage_step_after_the_baseline_is_recorded_once_with_its_reason(
    tmp_path: Path,
) -> None:
    """Fills of 4, 6, 4, 6 then 12, 12 bps record one slippage WARNING on the first 12 (#362)."""
    db = tmp_path / "ledger.sqlite3"

    assert _boot(db, [_CALM, _CALM, _STEPPED]) == 0

    assert _quality_rows(db) == [(_SLIPPAGE, "WARNING", _STEP_REASON)]


# SEV-1: S12 — the bus records a breach but notifies nobody; serve must alert it itself.
def test_the_slippage_breach_is_alerted_once_on_every_channel(tmp_path: Path) -> None:
    """The breach reaches the alert channel once, with the reason it was recorded with."""
    db = tmp_path / "ledger.sqlite3"
    telegram = RecordingChannel()

    _boot(db, [_CALM, _CALM, _STEPPED], telegram=telegram)

    assert _quality_alerts(telegram) == [(_SLIPPAGE, _STEP_REASON)]


def test_a_breach_episode_that_continues_is_not_alerted_again(tmp_path: Path) -> None:
    """Six stepped fills in a row stay one episode: one row and one alert (#362 D-2)."""
    db = tmp_path / "ledger.sqlite3"
    telegram = RecordingChannel()

    _boot(db, [_CALM, _CALM, _STEPPED, _STEPPED, _STEPPED], telegram=telegram)

    assert (len(_quality_rows(db)), len(_quality_alerts(telegram)), len(_trades(db))) == (1, 1, 10)


def test_a_run_that_stays_in_control_records_nothing(tmp_path: Path) -> None:
    """Three cycles of 4 and 6 bps fills, fee and latency unchanged, record and alert nothing."""
    db = tmp_path / "ledger.sqlite3"
    telegram = RecordingChannel()

    _boot(db, [_CALM, _CALM, _CALM], telegram=telegram)

    assert (_quality_rows(db), _quality_alerts(telegram), len(_trades(db))) == ([], [], 6)


# SEV-1: S12 — a restart that forgot the baseline would silence the alert for 30 more fills.
def test_a_restart_keeps_the_baseline_from_the_ledger(tmp_path: Path) -> None:
    """Baseline fills of the first boot make the second boot's first stepped fill alert."""
    db = tmp_path / "ledger.sqlite3"
    _boot(db, [_CALM, _CALM])
    telegram = RecordingChannel()

    _boot(db, [_STEPPED], first=2, telegram=telegram)

    assert (_quality_rows(db), _quality_alerts(telegram)) == (
        [(_SLIPPAGE, "WARNING", _STEP_REASON)],
        [(_SLIPPAGE, _STEP_REASON)],
    )


def test_a_restart_does_not_alert_an_episode_already_alerted(tmp_path: Path) -> None:
    """An episode open when serve stopped stays open after the restart: nothing new."""
    db = tmp_path / "ledger.sqlite3"
    _boot(db, [_CALM, _CALM, _STEPPED])
    telegram = RecordingChannel()

    _boot(db, [_STEPPED], first=3, telegram=telegram)

    assert (len(_quality_rows(db)), _quality_alerts(telegram), len(_trades(db))) == (1, [], 8)


def test_a_fill_without_latency_still_feeds_slippage_and_leaves_latency_alone(
    tmp_path: Path,
) -> None:
    """Stepped fills delivered before their bar close have no latency; only slippage alerts.

    Latency was 6000 ms on every baseline fill (a flat chart, #362 D-3), so a
    missing latency read as 0 ms, or any number but 6000, would alert latency too.
    """
    db = tmp_path / "ledger.sqlite3"
    early = timedelta(seconds=-1)
    fills = _script([_CALM, _CALM])
    fills[_entry_bar(2)] = _Fill("12", delay=early)
    fills[_entry_bar(2) + 3] = _Fill("12", delay=early)

    _boot(db, [_CALM, _CALM, _STEPPED], fills=fills)

    quality = [json.loads(r["payload_json"])["delta_ms"] for r in events(db, "FillQualityRecorded")]
    assert (quality, _quality_rows(db)) == (
        [6000, 6000, 6000, 6000, None, None],
        [(_SLIPPAGE, "WARNING", _STEP_REASON)],
    )


class _BrokenMonitor(ExecutionQualityMonitor):
    """A monitor whose charts raise on every fill."""

    def observe(
        self, observation: ExecutionObservation, *, at: datetime, correlation_id: str
    ) -> list[AnomalyDetected]:
        """Fail like a bug in the charts."""
        raise RuntimeError("chart broke")


class _UnprimeableMonitor(ExecutionQualityMonitor):
    """A monitor that cannot replay the ledger's past fills."""

    def prime(self, observations: Iterable[ExecutionObservation]) -> None:
        """Fail like a bug in the replay."""
        raise RuntimeError("replay broke")


# SEV-1: F1 — an alert-only monitor must never cost the ledger a fill.
def test_a_monitor_error_is_logged_and_the_fills_are_still_recorded(tmp_path: Path) -> None:
    """Each failing observation is one log line; both fills and the run's exit code stand."""
    db = tmp_path / "ledger.sqlite3"

    with capture_logs() as logs:
        code = _boot(db, [_CALM], monitor=_BrokenMonitor(_BASELINE))

    failures = [
        (e["error"], e["detail"]) for e in logs if e["event"] == "serve.execution_quality.failed"
    ]
    assert (code, [t[0] for t in _trades(db)], failures) == (
        0,
        ["BUY", "SELL"],
        [("RuntimeError", "chart broke"), ("RuntimeError", "chart broke")],
    )


def test_a_failed_replay_at_start_up_is_logged_and_serve_still_fills(tmp_path: Path) -> None:
    """The replay failing is one log line; the boot still trades its cycle."""
    db = tmp_path / "ledger.sqlite3"
    _boot(db, [_CALM])

    with capture_logs() as logs:
        code = _boot(db, [_CALM], first=1, monitor=_UnprimeableMonitor(_BASELINE))

    failures = [
        (e["error"], e["detail"]) for e in logs if e["event"] == "serve.execution_quality.failed"
    ]
    assert (code, len(_trades(db)), failures) == (0, 4, [("RuntimeError", "replay broke")])


def test_a_failed_breach_alert_is_logged_and_the_breach_is_still_recorded(tmp_path: Path) -> None:
    """A channel failing on the alert is logged under its own name; the row stays."""
    db = tmp_path / "ledger.sqlite3"
    broken = RecordingChannel(raises=RuntimeError("telegram bug"))

    with capture_logs() as logs:
        code = _boot(db, [_CALM, _CALM, _STEPPED], telegram=broken)

    failed = [e["event"] for e in logs if e["event"] == "serve.execution_quality.alert_failed"]
    assert (code, failed, _quality_rows(db)) == (
        0,
        ["serve.execution_quality.alert_failed"],
        [(_SLIPPAGE, "WARNING", _STEP_REASON)],
    )


def test_the_breach_journal_line_names_the_metric_and_no_figure(tmp_path: Path) -> None:
    """The journal gets the source and severity of a breach; its bps figures stay in the ledger."""
    db = tmp_path / "ledger.sqlite3"

    with capture_logs() as logs:
        _boot(db, [_CALM, _CALM, _STEPPED])

    lines = [
        {k: v for k, v in e.items() if k not in {"correlation_id", "log_level"}}
        for e in logs
        if e["event"] == "serve.execution_quality.breach"
    ]
    assert lines == [
        {"event": "serve.execution_quality.breach", "source": _SLIPPAGE, "severity": "WARNING"}
    ]


# SEV-1: S5 — an alert-only monitor must not become a trade input (#362 D-2, ADR-005).
def test_signals_fills_and_halts_are_the_same_whether_the_charts_breach_or_not(
    tmp_path: Path,
) -> None:
    """One market run twice: a breaching chart and a chart that never breaches trade alike."""
    breaching_db = tmp_path / "breaching.sqlite3"
    quiet_db = tmp_path / "quiet.sqlite3"
    breaching_channel, quiet_channel = RecordingChannel(), RecordingChannel()
    market = [_CALM, _CALM, _STEPPED, _CALM]

    _boot(breaching_db, market, telegram=breaching_channel)
    _boot(
        quiet_db,
        market,
        telegram=quiet_channel,
        monitor=ExecutionQualityMonitor(_NEVER_BREACHES),
    )

    assert (len(_quality_rows(breaching_db)), len(_quality_rows(quiet_db))) == (1, 0)
    assert (
        _decisions(breaching_db),
        _trades(breaching_db),
        _rows(breaching_db, "RiskHaltRecorded"),
        signal_kinds(breaching_channel),
    ) == (
        _decisions(quiet_db),
        _trades(quiet_db),
        _rows(quiet_db, "RiskHaltRecorded"),
        signal_kinds(quiet_channel),
    )
