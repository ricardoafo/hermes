"""Tests for ``hermes parity check`` (#358).

Invariants protected here:

- a day whose stored bars replay to the decisions serve recorded prints
  ``OK — N signals match for YYYY-MM-DD`` and exits 0;
- a mismatch prints one line naming the bar close time, the pair and both
  decisions, and exits 1;
- ``--alert`` sends exactly one alert on a mismatch, none on parity and none
  without the flag, and the alert carries no price, equity or P&L;
- usage errors exit 2; a missing ledger, a day with no stored bars, fewer
  than 300 stored 1d bars offline (#880) and a config that cannot run exit 3;
- the ledger file is byte-identical after any run without ``--alert``;
- an ``--alert`` alert is recorded once as an ``AnomalyDetected`` row, and a
  run in parity records none (#136);
- the command is registered as ``parity``;
- a day with bars serve reached late after an outage prints that it was not
  compared, exits 0 and sends no alert (#1078).
"""

import hashlib
import io
import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import respx

from hermes.adapters.clock import SimulatedClock
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.notification_router import RouterConfig
from hermes.application.parity.check import HistoryFetcher
from hermes.domain.errors import ExchangeError
from hermes.domain.events.bar_missed import BarMissed
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints import parity as module
from hermes.entrypoints.commands import discover
from hermes.infrastructure.config.models import MarketDataConfig
from hermes.ports.bar_feed import SourceState
from tests.support.anomaly_rows import recorded_anomalies
from tests.support.day_zero import write_day_zero
from tests.support.parity import D1, DAY_END, DAY_START, H4, bar, signal_event
from tests.support.paths import REPO_ROOT
from tests.support.unknown_signal import config_with_unknown_signal, refusal_lines
from tests.unit.adapters.market_data.fake_binance_public import (
    BTC_USDT,
    START,
    FakeBinancePublic,
    SimulatedSleep,
    build_adapter,
)

_NOW = datetime(2026, 10, 4, 0, 5, tzinfo=UTC)
_CONFIG = REPO_ROOT / "config"
_PAIRS = ("BTC", "ETH", "SOL")
_PRICE = "100"


class _Channel:
    """A notifier that records the events it is asked to send."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    def notify_urgent(self, *, event: str, correlation_id: str, message: str) -> None:
        raise AssertionError("the parity alert goes through notify")

    def notify_report(
        self,
        *,
        event: str,
        correlation_id: str,
        title: str,
        body: str,
        attachments: list[Path] | None = None,
    ) -> None:
        raise AssertionError("the parity alert goes through notify")

    def notify(self, event: DomainEvent) -> None:
        self.events.append(event)


def _insert_bars(connection: sqlite3.Connection, *, history: bool = True, daily: int = 300) -> None:
    """Store flat bars: ``daily`` days of 1d history, 10 days of 4h history and the test day."""
    rows: list[tuple[str, ...]] = []
    for base in _PAIRS:
        closes = [(D1, DAY_START - timedelta(days=n)) for n in range(daily if history else 0)]
        closes.append((D1, DAY_END))
        closes += [(H4, DAY_END - timedelta(hours=4 * n)) for n in range(66 if history else 6)]
        for interval, close in closes:
            span = timedelta(days=1) if interval is D1 else timedelta(hours=4)
            rows.append(
                (
                    base,
                    "USDT",
                    interval.value,
                    (close - span).isoformat(),
                    close.isoformat(),
                    _PRICE,
                    _PRICE,
                    _PRICE,
                    _PRICE,
                    "1000",
                    close.isoformat(),
                )
            )
    connection.executemany(
        "INSERT INTO live_bars (pair_base, pair_quote, interval, open_time, close_time, open, "
        "high, low, close, volume, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """Build a migrated ledger with a flat day of bars for BTC, ETH and SOL and no signals."""
    path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(path)
    SqliteLedger(connection)
    _insert_bars(connection)
    connection.close()
    return path


def _with_signal(path: Path, event: SignalEmitted) -> None:
    connection = sqlite3.connect(path)
    SqliteLedger(connection).append_event(event)
    connection.close()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _NoNetwork:
    """A warm-up read whose fetcher fails the test: stored bars must have been enough."""

    def __call__(self, config: MarketDataConfig) -> AbstractContextManager[HistoryFetcher]:
        _ = config

        def fetch(pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
            raise AssertionError(f"the exchange was read for {pair} {interval.value} x{count}")

        return nullcontext(fetch)


def _flat_history(config: MarketDataConfig) -> AbstractContextManager[HistoryFetcher]:
    """Serve flat bars at 100 up to the test day end, as the exchange would."""
    _ = config

    def fetch(pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
        span = timedelta(days=1) if interval is D1 else timedelta(hours=4)
        closes = [DAY_END - span * n for n in range(count)][::-1]
        return [bar(close, interval, pair=pair) for close in closes]

    return nullcontext(fetch)


def _run(
    argv: list[str],
    *,
    channel: _Channel | None = None,
    history: Callable[[MarketDataConfig], AbstractContextManager[HistoryFetcher]] | None = None,
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    channels = RouterConfig(telegram=channel)
    code = module.main(
        argv,
        stream=out,
        err=err,
        now=_NOW,
        env={},
        router_factory=lambda _env: channels,
        history_factory=history or _NoNetwork(),
    )
    return code, out.getvalue(), err.getvalue()


def _args(path: Path, *extra: str) -> list[str]:
    return [
        "check",
        "--date",
        "2026-10-03",
        "--db",
        str(path),
        "--config-dir",
        str(_CONFIG),
        *extra,
    ]


def test_a_quiet_day_prints_the_ok_line_and_exits_zero(ledger: Path) -> None:
    """Flat bars give no signal live or in the replay: parity with zero signals."""
    assert _run(_args(ledger)) == (0, "OK — 0 signals match for 2026-10-03\n", "")


def test_without_a_date_the_previous_utc_day_is_checked(ledger: Path) -> None:
    """The nightly run at 00:05 UTC checks the day that just closed."""
    code, out, _ = _run(["check", "--db", str(ledger), "--config-dir", str(_CONFIG)])

    assert (code, out) == (0, "OK — 0 signals match for 2026-10-03\n")


def test_a_recorded_signal_the_replay_does_not_produce_is_one_mismatch_line(
    ledger: Path,
) -> None:
    """Serve emitted an entry the strategy does not produce from the stored bars."""
    _with_signal(ledger, signal_event(DAY_START + timedelta(hours=8)))

    code, out, err = _run(_args(ledger))

    assert (code, err) == (1, "")
    assert out == (
        "MISMATCH 2026-10-03T08:00:00+00:00 BTC/USDT: "
        "serve entry long not executed; replay no signal\n"
    )


def test_alert_sends_one_message_naming_the_bar_and_decisions_without_money(
    ledger: Path,
) -> None:
    """--alert on a mismatch: one alert with the bar and both decisions, no price or equity."""
    _with_signal(ledger, signal_event(DAY_START + timedelta(hours=8)))
    channel = _Channel()

    code, _, _ = _run(_args(ledger, "--alert"), channel=channel)

    assert code == 1
    assert len(channel.events) == 1
    reason = getattr(channel.events[0], "reason", "")
    assert (
        "2026-10-03T08:00:00+00:00 BTC/USDT: serve entry long not executed, replay no signal"
        in (reason)
    )
    for figure in ("2618.45", "2548.45", "equity", "P&L", "pnl", _PRICE + "."):
        assert figure.lower() not in reason.lower()


def test_no_alert_is_sent_without_the_flag(ledger: Path) -> None:
    """A manual run stays quiet unless --alert is given."""
    _with_signal(ledger, signal_event(DAY_START + timedelta(hours=8)))
    channel = _Channel()

    assert _run(_args(ledger), channel=channel)[0] == 1
    assert channel.events == []


def test_no_alert_is_sent_on_parity_even_with_the_flag(ledger: Path) -> None:
    """Parity never alerts."""
    channel = _Channel()

    assert _run(_args(ledger, "--alert"), channel=channel)[0] == 0
    assert channel.events == []


def test_the_ledger_is_byte_identical_after_a_mismatching_run(ledger: Path) -> None:
    """The check only reads: a run that finds a mismatch leaves the file untouched."""
    _with_signal(ledger, signal_event(DAY_START + timedelta(hours=8)))
    before = _digest(ledger)

    assert _run(_args(ledger))[0] == 1
    assert _digest(ledger) == before


def test_a_mismatch_alert_is_recorded_once_and_matches_the_sent_alert(ledger: Path) -> None:
    """#136: --alert on a mismatch leaves exactly the sent alert in the ledger."""
    _with_signal(ledger, signal_event(DAY_START + timedelta(hours=8)))
    channel = _Channel()

    _run(_args(ledger, "--alert"), channel=channel)

    (sent,) = channel.events
    assert [
        (r["source"], r["reason"], r["correlation_id"]) for r in recorded_anomalies(ledger)
    ] == [(getattr(sent, "source", None), getattr(sent, "reason", None), sent.correlation_id)]


def test_parity_with_the_alert_flag_records_no_anomaly(ledger: Path) -> None:
    """#136: no mismatch, no alert, no AnomalyDetected row."""
    assert _run(_args(ledger, "--alert"), channel=_Channel())[0] == 0
    assert recorded_anomalies(ledger) == []


def test_a_missing_ledger_exits_three_and_is_not_created(tmp_path: Path) -> None:
    """A wrong --db path is reported, never created."""
    path = tmp_path / "absent.sqlite3"

    code, out, err = _run(_args(path))

    assert (code, out, err) == (3, "", f"ERROR: no ledger at {path}.\n")
    assert not path.exists()


def test_a_day_with_no_stored_bars_exits_three_and_leaves_the_ledger_untouched(
    tmp_path: Path,
) -> None:
    """A ledger that never stored the day's bars is 'no data', not parity, and stays as it was."""
    path = tmp_path / "empty.sqlite3"
    connection = sqlite3.connect(path)
    SqliteLedger(connection)
    connection.close()
    before = _digest(path)

    code, out, err = _run(_args(path))

    assert (code, out) == (3, "")
    assert err == "ERROR: no stored BTC/USDT 4h bars close on 2026-10-03\n"
    assert _digest(path) == before


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["verify"],
        ["check", "--date", "yesterday"],
        ["check", "--date", "2026-10-04"],
        ["check", "--bogus"],
    ],
)
def test_usage_errors_exit_two(argv: list[str]) -> None:
    """A missing or unknown subcommand, a bad or unfinished date and unknown flags exit 2."""
    code, out, err = _run(argv)

    assert (code, out) == (2, "")
    assert err.startswith("ERROR: ")


def test_a_config_that_cannot_load_exits_three(ledger: Path, tmp_path: Path) -> None:
    """A config directory with no config files is a startup problem, not a mismatch."""
    code, out, err = _run(
        ["check", "--date", "2026-10-03", "--db", str(ledger), "--config-dir", str(tmp_path)]
    )

    assert (code, out) == (3, "")
    assert err.startswith("ERROR: ")


def test_an_unknown_signal_exits_three_naming_it(ledger: Path, tmp_path: Path) -> None:
    """Contract §2a (#1119): an unknown ``signal:`` stops parity at config load, exit 3."""
    config_dir = config_with_unknown_signal(tmp_path)

    code, _, err = _run(
        ["check", "--date", "2026-10-03", "--db", str(ledger), "--config-dir", str(config_dir)]
    )

    lines = err.splitlines()
    assert (code, lines[0], lines[-1]) == (3, *refusal_lines(config_dir))


def test_the_command_is_registered_as_parity() -> None:
    """`hermes parity` is discovered from commands/parity.py."""
    assert discover()["parity"].name == "parity"


@pytest.fixture
def short_ledger(tmp_path: Path) -> Path:
    """Build a migrated ledger that stored the test day bars but no history before it."""
    path = tmp_path / "short.sqlite3"
    connection = sqlite3.connect(path)
    SqliteLedger(connection)
    _insert_bars(connection, history=False)
    connection.close()
    return path


def test_stored_bars_short_and_the_exchange_fills_the_gap_gives_a_verdict(
    short_ledger: Path,
) -> None:
    """Only the day is stored; the missing warm-up comes from the exchange read: parity."""
    assert _run(_args(short_ledger), history=_flat_history) == (
        0,
        "OK — 0 signals match for 2026-10-03\n",
        "",
    )


def test_offline_with_too_little_history_exits_three_and_never_reads_the_exchange(
    short_ledger: Path,
) -> None:
    """--offline keeps stored-only behaviour: the shortfall is exit 3, the exchange untouched."""
    before = _digest(short_ledger)

    code, out, err = _run(_args(short_ledger, "--offline"), history=_NoNetwork())

    assert (code, out) == (3, "")
    assert err == (
        "ERROR: BTC/USDT 4h: 0 bars before 2026-10-03, 45 needed to warm the strategy up\n"
    )
    assert _digest(short_ledger) == before


def test_offline_with_299_daily_bars_exits_three_naming_the_300_bar_floor(
    tmp_path: Path,
) -> None:
    """#880: 299 stored 1d bars are one short of the 300 the EMA(200) regime needs.

    Below 300 daily bars the EMA seed, and so the regime, depends on the
    warm-up length, so the replay would not decide what serve decided.
    """
    path = tmp_path / "daily-299.sqlite3"
    connection = sqlite3.connect(path)
    SqliteLedger(connection)
    _insert_bars(connection, daily=299)
    connection.close()

    code, out, err = _run(_args(path, "--offline"), history=_NoNetwork())

    assert (code, out, err) == (
        3,
        "",
        "ERROR: BTC/USDT 1d: 299 bars before 2026-10-03, 300 needed to warm the strategy up\n",
    )


def test_an_exchange_failure_while_filling_the_gap_exits_three(short_ledger: Path) -> None:
    """A klines outage is no data with the cause named, never a parity verdict."""

    def broken(config: MarketDataConfig) -> AbstractContextManager[HistoryFetcher]:
        _ = config

        def fetch(pair: Pair, interval: KlineInterval, count: int) -> list[Bar]:
            raise ExchangeError(f"HTTP 503 for {pair} {interval.value} {count}")

        return nullcontext(fetch)

    code, _, err = _run(_args(short_ledger), history=broken)

    assert code == 3
    assert err.startswith(
        "ERROR: could not read warm-up bars from the exchange: Exchange error: HTTP 503 for "
    )


def test_the_default_warm_up_read_drives_the_klines_adapter_and_closes_it() -> None:
    """The real wrapper returns the adapter closed bars from a faked public klines host."""
    fake = FakeBinancePublic(SimulatedClock(start=START))
    adapter = build_adapter(fake.clock, SimulatedSleep(fake.clock))

    with respx.mock(assert_all_called=False) as router:
        fake.install(router)
        with module._warm_up_reads(adapter) as fetch:
            bars = fetch(BTC_USDT, H4, 3)

    assert [b.open_time for b in bars] == [
        datetime(2026, 9, 30, 8, tzinfo=UTC),
        datetime(2026, 9, 30, 12, tzinfo=UTC),
        datetime(2026, 9, 30, 16, tzinfo=UTC),
    ]
    assert adapter.health().state is SourceState.STOPPED


# ---------------------------------------------------------------------------
# hermes parity window (#965)
# ---------------------------------------------------------------------------

_INSUFFICIENT = "M-09 P&L correlation: insufficient\nM-10 Sharpe match: insufficient\n"


def _window_args(path: Path, *extra: str) -> list[str]:
    return ["window", "--db", str(path), "--config-dir", str(_CONFIG), *extra]


def test_window_prints_statuses_only_and_exits_one_when_not_both_pass(ledger: Path) -> None:
    """One flat day has no round trip and too few days: both insufficient, exit 1."""
    code, out, err = _run(_window_args(ledger, "--since", DAY_START.isoformat()))

    assert (code, out, err) == (1, _INSUFFICIENT, "")


def test_window_out_file_holds_the_window_and_the_two_statuses_only(
    ledger: Path, tmp_path: Path
) -> None:
    """The file certify reads names the strategy, the window and two statuses, no value."""
    out_file = tmp_path / "parity.json"

    _run(_window_args(ledger, "--since", DAY_START.isoformat(), "--out", str(out_file)))

    assert json.loads(out_file.read_text(encoding="utf-8")) == {
        "strategy_id": "swing-donchian-4h",
        "since": DAY_START.isoformat(),
        "until": _NOW.isoformat(),
        "m09": "insufficient",
        "m10": "insufficient",
    }


def test_window_defaults_to_the_latest_day_zero_record(ledger: Path, tmp_path: Path) -> None:
    """Without --since, day 0 is read from the run record beside the ledger."""
    write_day_zero(ledger, "swing-donchian-4h", DAY_START)
    out_file = tmp_path / "parity.json"

    code, out, _ = _run(_window_args(ledger, "--out", str(out_file)))

    assert (code, out) == (1, _INSUFFICIENT)
    assert json.loads(out_file.read_text(encoding="utf-8"))["since"] == DAY_START.isoformat()


def test_window_without_since_or_a_day_zero_record_exits_three(ledger: Path) -> None:
    """No counted run has started, so there is no window to judge."""
    code, out, err = _run(_window_args(ledger))

    assert (code, out) == (3, "")
    assert err.startswith("ERROR: no day-0 record for swing-donchian-4h")


def test_window_leaves_the_ledger_byte_identical(ledger: Path) -> None:
    """The window replay only reads the ledger."""
    before = _digest(ledger)

    _run(_window_args(ledger, "--since", DAY_START.isoformat()))

    assert _digest(ledger) == before


def test_window_on_a_missing_ledger_exits_three(tmp_path: Path) -> None:
    """No ledger, nothing replayed, and none created."""
    missing = tmp_path / "none.sqlite3"

    code, _, err = _run(_window_args(missing, "--since", DAY_START.isoformat()))

    assert (code, err, missing.exists()) == (3, f"ERROR: no ledger at {missing}.\n", False)


@pytest.mark.parametrize(
    "since",
    ["yesterday", "2026-10-03T00:00:00", _NOW.isoformat(), (_NOW + timedelta(days=1)).isoformat()],
)
def test_window_usage_errors_exit_two(ledger: Path, since: str) -> None:
    """A non-ISO or offset-less --since, and a day 0 that is not in the past, exit 2."""
    code, out, err = _run(_window_args(ledger, "--since", since))

    assert (code, out) == (2, "")
    assert err.startswith("ERROR: ")


def test_an_outage_day_prints_not_compared_and_sends_no_alert(ledger: Path) -> None:
    """#1078: the unfilled missed entry would be a mismatch; the outage line replaces it."""
    late = DAY_START + timedelta(hours=8)
    _with_signal(ledger, signal_event(late))
    connection = sqlite3.connect(ledger)
    SqliteLedger(connection).append_event(
        BarMissed.from_bar(bar(late), handled_at=late + timedelta(hours=3), correlation_id="m1")
    )
    connection.close()
    channel = _Channel()

    code, out, err = _run(_args(ledger, "--alert"), channel=channel)

    assert (code, out, err, channel.events) == (
        0,
        "OK — 2026-10-03 not compared: serve reached 1 bar late after an outage\n",
        "",
        [],
    )
