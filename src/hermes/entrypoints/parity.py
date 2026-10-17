"""``hermes parity`` — compare what serve decided with a replay of the same bars.

Usage: ``hermes parity check [--date YYYY-MM-DD] [--db PATH] [--config-dir DIR]
[--alert] [--offline]`` and ``hermes parity window [--since ISO] [--out FILE]
[--db PATH] [--config-dir DIR] [--offline]``

Reads the bars, signals and fills serve stored for one UTC day, replays the
bars through the same strategy and fill kernel, and compares the decisions:
which signals fired and which were executed. Prices are not compared. The
ledger is read with SQLite's ``mode=ro`` and the command is safe next to
``hermes serve``; its only write is the ``--alert`` alert, appended to the
ledger ``events`` table before it is sent (#136). Without ``--date`` it checks the previous UTC
day. A bar belongs to the day it closes in, so the day's 1d bar is the one
closing at its end.

The strategy is warmed up from the stored bars before the day. When they fall
short of the configured floors (45 4h, 300 1d), the gap is filled from the same
public Binance klines read serve warms up with (no API key); stored bars win
wherever both exist. ``--offline`` uses stored bars only and exits 3 on a
shortfall.

Prints one line per mismatch naming the bar close time, the pair and both
decisions, or ``OK — N signals match for YYYY-MM-DD``. ``--alert`` also sends
one alert through the notification router on a mismatch; it names bars and
decisions and carries no P&L, equity or position values.

Exit codes: 0 parity, 1 mismatch, 2 usage, 3 no data, no ledger or bad config.

``window`` replays the whole shadow window, from day 0 (``--since``, or the
strategy's latest day-0 record beside the ledger) to now, from flat in one
continuous run, and judges ADR-021 Done rule 3: M-09 P&L correlation and M-10
Sharpe match against the shadow serve recorded. It prints and writes (``--out``)
pass, block or insufficient only, never a value (ADR-012 D-06); ``hermes
certify --shadow-parity`` reads the file. Exit codes: 0 both pass, 1 otherwise,
2 usage, 3 no data, no ledger, no day-0 record or bad config.

Reference: openspec/changes/358-parity-check/change.md;
openspec/changes/965-shadow-parity-window/change.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final, TextIO

from hermes.adapters.clock.wall import WallClock
from hermes.adapters.console.shadow_records import (
    RunsDirectory,
    ShadowRecordError,
    runs_dir_for,
)
from hermes.adapters.fills.orderbook_walk import OrderbookWalkFillModel
from hermes.adapters.ledger.parity_reader import SqliteParityReader
from hermes.adapters.market_data.binance_live_klines import BinanceLiveKlinesAdapter
from hermes.adapters.notifier.slack_adapter import SlackAdapter
from hermes.adapters.notifier.telegram_adapter import TelegramAdapter
from hermes.application.calibration.shadow_parity import MetricStatus
from hermes.application.calibration.shadow_window import (
    ShadowWindowError,
    shadow_daily_equity,
    window_verdicts,
)
from hermes.application.notification_router import EventClass, RouterConfig, route
from hermes.application.parity.check import (
    HistoryFetcher,
    ParityDataError,
    check_day,
    day_window,
    replay_window,
)
from hermes.application.parity.decisions import ParityReport, parity_alert
from hermes.domain.errors import InfraError, LedgerError
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints._anomaly_record import record_anomaly
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.serve import (
    DEFAULT_DB_PATH,
    StartupError,
    _build_kernel,
    _fees_at,
    _load_experiment,
    _slack_config,
    _telegram_config,
)
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.config.models import MarketDataConfig, StrategyConfig
from hermes.ports.bar_feed import ClosedBarFeedPort

__all__ = ["main"]

_USAGE: Final = (
    "Usage: hermes parity check [--date YYYY-MM-DD] [--db PATH] [--config-dir DIR]\n"
    "                           [--alert] [--offline]\n"
    "\n"
    "Replays a UTC day of stored live bars and compares the decisions with the ones\n"
    "serve recorded (signals, executed or not). Without --date it checks the\n"
    "previous UTC day. Missing warm-up bars are read from the public Binance\n"
    "klines; --offline uses stored bars only. --alert sends one alert on a mismatch\n"
    "and records it in the ledger, the command's only write.\n"
    "Exit codes: 0 parity, 1 mismatch, 2 usage, 3 no data, no ledger or bad config.\n"
    "\n"
    "Usage: hermes parity window [--since ISO] [--out FILE] [--db PATH]\n"
    "                            [--config-dir DIR] [--offline]\n"
    "\n"
    "Replays the shadow window from day 0 (--since, or the latest day-0 record) to\n"
    "now and judges M-09 P&L correlation and M-10 Sharpe match against the shadow.\n"
    "Prints and writes (--out, for hermes certify --shadow-parity) the statuses only.\n"
    "Exit codes: 0 both pass, 1 otherwise, 2 usage, 3 no data, no day-0 record or\n"
    "bad config.\n"
)

_MIN_WARMUP: Final = {KlineInterval.H4: 45, KlineInterval.D1: 300}
"""Floors of ``MarketDataConfig.warmup_bars_*``: fewer bars leave an indicator unsettled."""

RouterFactory = Callable[[Mapping[str, str]], RouterConfig]
HistoryFactory = Callable[[MarketDataConfig], AbstractContextManager[HistoryFetcher]]


def main(
    argv: list[str],
    *,
    stream: TextIO,
    err: TextIO | None = None,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    router_factory: RouterFactory | None = None,
    history_factory: HistoryFactory | None = None,
) -> int:
    """Dispatch ``hermes parity <subcommand>``.

    Args:
        argv: Arguments after the ``parity`` token.
        stream: Output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.
        now: Run time; defaults to the wall clock. Injected by tests.
        env: Environment snapshot for the alert credentials.
        router_factory: Builds the alert channels from ``env``; defaults to
            Telegram and Slack. Injected by tests so no socket opens.
        history_factory: Opens the exchange warm-up read for a market-data
            config; defaults to the public Binance klines adapter serve uses.
            Injected by tests so no request leaves the process.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    if argv and argv[0] in ("-h", "--help"):
        stream.write(_USAGE)
        return 0
    if not argv or argv[0] not in ("check", "window"):
        err.write("ERROR: expected the subcommand `check` or `window`.\n" + _USAGE)
        return 2
    at = now if now is not None else datetime.now(UTC)
    if argv[0] == "window":
        return _window(
            argv[1:], stream, err, at, history_factory=history_factory or _binance_history
        )
    return _check(
        argv[1:],
        stream,
        err,
        at,
        env=env if env is not None else dict(os.environ),
        router_factory=router_factory or _router_from_env,
        history_factory=history_factory or _binance_history,
    )


def _check(
    argv: list[str],
    stream: TextIO,
    err: TextIO,
    at: datetime,
    *,
    env: Mapping[str, str],
    router_factory: RouterFactory,
    history_factory: HistoryFactory,
) -> int:
    request = _parse(argv, stream, err, at)
    if isinstance(request, int):
        return request
    parsed, day = request
    db: Path = parsed.db
    if not db.is_file():
        err.write(f"ERROR: no ledger at {db}.\n")
        return 3
    try:
        report = _run(
            day,
            db,
            parsed.config_dir,
            now=at,
            history_factory=None if parsed.offline else history_factory,
        )
    except (ConfigError, StartupError, ParityDataError, InfraError, FileNotFoundError) as exc:
        err.write(_failure_line(exc))
        return 3

    if report.in_parity:
        stream.write(f"OK — {report.matched} signals match for {day.isoformat()}\n")
        return 0
    stream.writelines(mismatch.line() + "\n" for mismatch in report.mismatches)
    if parsed.alert:
        _send_alert(report, at, env, router_factory, err, db=db)
    return 1


def _failure_line(exc: Exception) -> str:
    """Return the ERROR line for a failure that makes the check exit 3."""
    if isinstance(exc, LedgerError | FileNotFoundError):
        return f"ERROR: could not read the ledger: {exc}\n"
    if isinstance(exc, InfraError):
        return f"ERROR: could not read warm-up bars from the exchange: {exc}\n"
    return f"ERROR: {exc}\n"


def _parse(
    argv: list[str], stream: TextIO, err: TextIO, at: datetime
) -> tuple[argparse.Namespace, date] | int:
    """Parse argv; return the request, or the exit code to stop with."""
    parser = argparse.ArgumentParser(prog="hermes parity check", add_help=False)
    parser.add_argument("--date", default=None)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--alert", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write("ERROR: bad arguments.\n" + _USAGE)
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    try:
        day = (
            date.fromisoformat(parsed.date)
            if parsed.date is not None
            else (at - timedelta(days=1)).date()
        )
    except ValueError:
        err.write(f"ERROR: --date must be YYYY-MM-DD, got {parsed.date!r}.\n" + _USAGE)
        return 2
    if day_window(day)[1] > at:
        err.write(f"ERROR: {day} has not closed yet.\n")
        return 2
    return parsed, day


def _run(
    day: date,
    db: Path,
    config_dir: Path,
    *,
    now: datetime,
    history_factory: HistoryFactory | None,
) -> ParityReport:
    """Compose the strategy and kernel as serve does and check ``day``."""
    config = load_config(config_dir)
    roster = _enabled(config.strategies.strategies)
    strategy_id = StrategyId(roster.name)
    pre_registration, fee_rows = _load_experiment(None)
    fill_model = OrderbookWalkFillModel()
    market = config.market_data
    with (
        SqliteParityReader.open(db) as source,
        history_factory(market) if history_factory is not None else nullcontext() as history,
    ):
        return check_day(
            day=day,
            source=source,
            strategy_id=strategy_id,
            pairs=market.parsed_pairs(),
            intervals=tuple(market.intervals),
            warmup_bars={i: market.warmup_bars(i) for i in market.intervals},
            min_warmup_bars=_MIN_WARMUP,
            seed=PortfolioSnapshot.seed(
                strategy_id, Money(amount=roster.capital_budget, currency=roster.quote)
            ),
            make_strategy=SwingStrategyAdapter,
            make_kernel=lambda tracker: _build_kernel(
                fill_model, tracker, strategy_id, pre_registration, roster.sizing_policy()
            ),
            fees_at=_fees_at(fee_rows),
            history=history,
            now=now,
        )


_STATUS_LINES: Final = (("m09", "M-09 P&L correlation"), ("m10", "M-10 Sharpe match"))


def _window(
    argv: list[str],
    stream: TextIO,
    err: TextIO,
    at: datetime,
    *,
    history_factory: HistoryFactory,
) -> int:
    parsed = _parse_window(argv, stream, err)
    if isinstance(parsed, int):
        return parsed
    db: Path = parsed.db
    if not db.is_file():
        err.write(f"ERROR: no ledger at {db}.\n")
        return 3
    try:
        config = load_config(parsed.config_dir)
        roster = _enabled(config.strategies.strategies)
        since = parsed.since or _day_zero(db, roster.name)
        if since >= at:
            err.write(f"ERROR: day 0 ({since.isoformat()}) is not in the past.\n")
            return 2
        statuses = _judge_window(
            roster,
            config.market_data,
            db,
            since=since,
            now=at,
            history_factory=None if parsed.offline else history_factory,
        )
    except (
        ConfigError,
        StartupError,
        ParityDataError,
        ShadowWindowError,
        ShadowRecordError,
        InfraError,
        FileNotFoundError,
    ) as exc:
        err.write(_failure_line(exc))
        return 3

    stream.writelines(f"{label}: {statuses[key]}\n" for key, label in _STATUS_LINES)
    if parsed.out is not None:
        document = {
            "strategy_id": roster.name,
            "since": since.isoformat(),
            "until": at.isoformat(),
            **{key: str(statuses[key]) for key, _ in _STATUS_LINES},
        }
        parsed.out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0 if all(status is MetricStatus.OK for status in statuses.values()) else 1


def _parse_window(argv: list[str], stream: TextIO, err: TextIO) -> argparse.Namespace | int:
    """Parse ``window`` argv; return the namespace, or the exit code to stop with."""
    parser = argparse.ArgumentParser(prog="hermes parity window", add_help=False)
    parser.add_argument("--since", default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write("ERROR: bad arguments.\n" + _USAGE)
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    if parsed.since is not None:
        try:
            since = datetime.fromisoformat(parsed.since)
        except ValueError:
            since = None
        if since is None or since.tzinfo is None:
            err.write(
                f"ERROR: --since must be an ISO time with a UTC offset, got {parsed.since!r}.\n"
                + _USAGE
            )
            return 2
        parsed.since = since
    return parsed


def _enabled(strategies: list[StrategyConfig]) -> StrategyConfig:
    """Return the roster entry serve runs: the first enabled one."""
    roster = next((s for s in strategies if s.enabled), None)
    if roster is None:
        raise StartupError("no enabled strategy to check")
    return roster


def _day_zero(db: Path, strategy_id: str) -> datetime:
    """Return day 0 of the strategy's latest counted run, recorded beside the ledger."""
    record = RunsDirectory(runs_dir_for(db)).latest_run(strategy_id)
    if record is None:
        raise ParityDataError(
            f"no day-0 record for {strategy_id} in {runs_dir_for(db)}; pass --since"
        )
    return record.day_zero_at


def _judge_window(
    roster: StrategyConfig,
    market: MarketDataConfig,
    db: Path,
    *,
    since: datetime,
    now: datetime,
    history_factory: HistoryFactory | None,
) -> dict[str, MetricStatus]:
    """Replay the window as serve composes it and return the two statuses."""
    strategy_id = StrategyId(roster.name)
    pre_registration, fee_rows = _load_experiment(None)
    fill_model = OrderbookWalkFillModel()
    seed = PortfolioSnapshot.seed(
        strategy_id, Money(amount=roster.capital_budget, currency=roster.quote)
    )
    with (
        SqliteParityReader.open(db) as source,
        history_factory(market) if history_factory is not None else nullcontext() as history,
    ):
        replayed = replay_window(
            start=since,
            end=now,
            source=source,
            pairs=market.parsed_pairs(),
            intervals=tuple(market.intervals),
            warmup_bars={i: market.warmup_bars(i) for i in market.intervals},
            min_warmup_bars=_MIN_WARMUP,
            seed=seed,
            make_strategy=SwingStrategyAdapter,
            make_kernel=lambda tracker: _build_kernel(
                fill_model, tracker, strategy_id, pre_registration, roster.sizing_policy()
            ),
            fees_at=_fees_at(fee_rows),
            history=history,
            now=now,
        )
        shadow = [t for t in source.trades(strategy_id) if since <= t.occurred_at <= now]
    m09, m10 = window_verdicts(
        shadow_trades=shadow,
        replay_trades=replayed.run.trades,
        shadow_equity=shadow_daily_equity(seed, shadow, replayed.bars, start=since),
        replay_equity=replayed.run.daily_equity,
    )
    return {"m09": m09.status, "m10": m10.status}


@contextmanager
def _warm_up_reads(feed: ClosedBarFeedPort) -> Iterator[HistoryFetcher]:
    """Yield a sync fetcher over ``feed.warm_up`` and close the feed afterwards."""
    with asyncio.Runner() as runner:
        try:
            yield lambda pair, interval, count: runner.run(feed.warm_up(pair, interval, count))
        finally:
            runner.run(feed.aclose())


def _binance_history(config: MarketDataConfig) -> AbstractContextManager[HistoryFetcher]:
    """Open serve's public klines adapter for warm-up reads (no API key)."""
    return _warm_up_reads(BinanceLiveKlinesAdapter.create(config, WallClock()))


def _send_alert(
    report: ParityReport,
    at: datetime,
    env: Mapping[str, str],
    factory: RouterFactory,
    err: TextIO,
    *,
    db: Path,
) -> None:
    """Record the mismatch alert, then send it to every channel the router names."""
    alert = parity_alert(report, now=at)
    if alert is None:
        return
    record_anomaly(db, alert, err)
    channels = factory(env)
    adapters = route(EventClass.ANOMALY_DETECTED, channels)
    if not adapters:
        err.write("WARNING: --alert given but no channel is configured; no alert sent\n")
        return
    for adapter in adapters:
        try:
            adapter.notify(alert)
        except InfraError as exc:
            err.write(f"WARNING: the alert could not be sent: {exc}\n")
        finally:
            if isinstance(adapter, TelegramAdapter | SlackAdapter):
                adapter.close()


def _router_from_env(env: Mapping[str, str]) -> RouterConfig:
    """Wire Telegram and Slack from ``env``; a channel without credentials is left out."""
    telegram = _telegram_config(env)
    slack = _slack_config(env)
    return RouterConfig(
        telegram=TelegramAdapter.create(telegram) if telegram is not None else None,
        slack=SlackAdapter.create(slack) if slack is not None else None,
    )
