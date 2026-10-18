"""``hermes failover-check`` — can Kraken stand in for Binance right now (#31)?

Step 1 of the failover drill in ``docs/runbook/failover-to-kraken.md``. For
every traded pair it fetches the last closed bars from the Binance public
market-data host that ``hermes serve`` reads and from Kraken's public API,
then compares the closes (:mod:`hermes.domain.market_data.venue_parity`).
A pair passes when Kraken answers, has every bar and every close is within
the tolerance.

Read-only: public GET endpoints, no API key, nothing written. Safe to run on
hermes-01 next to a running ``hermes serve``.

Exit codes: 0 every pair passes, 1 at least one pair fails, 2 usage,
3 config failure.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, TextIO

from hermes.adapters._shared.rate_limiter import RateLimitConfig
from hermes.adapters.exchanges.binance_klines import BinanceKlinesAdapter, BinanceKlinesConfig
from hermes.adapters.exchanges.kraken_public import KrakenPublicAdapter
from hermes.adapters.exchanges.kraken_public import KrakenPublicConfig as KrakenWireConfig
from hermes.domain.errors import InfraError
from hermes.domain.market_data.kline import Kline, KlineInterval
from hermes.domain.market_data.venue_parity import PairParity, ParityVerdict, compare_closes
from hermes.domain.value_objects.pair import Pair
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.config.root import HermesConfig
from hermes.infrastructure.time import now_utc

__all__ = ["FetchKlines", "Venues", "main"]

FetchKlines = Callable[[Pair, KlineInterval, datetime, datetime], Iterable[Kline]]
"""``(pair, interval, since, until)`` to the venue's candles in ``[since, until)``."""

_INTERVALS: Final[dict[str, KlineInterval]] = {"4h": KlineInterval.H4, "1d": KlineInterval.D1}
_INTERVAL_LENGTH: Final[dict[KlineInterval, timedelta]] = {
    KlineInterval.H4: timedelta(hours=4),
    KlineInterval.D1: timedelta(days=1),
}
_DEFAULT_BARS: Final = 6
_MAX_BARS: Final = 500
# Binance and Kraken USDT books sit a few bps apart on a calm day; 50 bps
# still catches a wrong symbol (another quote currency or asset), which is
# off by percent, not bps.
_DEFAULT_TOLERANCE_BPS: Final = Decimal(50)
_EXIT_FAIL: Final = 1
_EXIT_USAGE: Final = 2
_EXIT_CONFIG: Final = 3


@dataclass(frozen=True, slots=True)
class Venues:
    """The two candle sources and how to name them in the report.

    Args:
        primary: Venue ``hermes serve`` reads today.
        backup: Venue the drill fails over to.
        primary_name: Label for the primary in the report.
        backup_name: Label for the backup in the report.
    """

    primary: FetchKlines
    backup: FetchKlines
    primary_name: str = "binance"
    backup_name: str = "kraken"


@dataclass(frozen=True, slots=True)
class _Outcome:
    pair: Pair
    parity: PairParity | None
    error: str


def _usage() -> str:
    return (
        "Usage: hermes failover-check [--interval 4h|1d] [--bars N] [--tolerance-bps X]\n"
        "                             [--json] [--config-dir DIR]\n"
        "\n"
        "Compares the last N closed bars (default 6 of 4h) of every traded pair on\n"
        "Binance and Kraken. Read-only. Step 1 of docs/runbook/failover-to-kraken.md.\n"
        "Exit codes: 0 Kraken ready, 1 not ready, 2 usage, 3 config failure.\n"
    )


def _parse(argv: list[str], stream: TextIO, err: TextIO) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser(prog="hermes failover-check", add_help=False)
    parser.add_argument("--interval", choices=sorted(_INTERVALS), default="4h")
    parser.add_argument("--bars", type=int, default=_DEFAULT_BARS)
    parser.add_argument("--tolerance-bps", default=str(_DEFAULT_TOLERANCE_BPS))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write(f"ERROR: could not parse `hermes failover-check` arguments.\n{_usage()}")
        return None
    if parsed.help:
        stream.write(_usage())
        return parsed
    if not 1 <= parsed.bars <= _MAX_BARS:
        err.write(f"ERROR: --bars must be between 1 and {_MAX_BARS}.\n")
        return None
    try:
        parsed.tolerance_bps = Decimal(parsed.tolerance_bps)
    except InvalidOperation:
        err.write("ERROR: --tolerance-bps must be a number.\n")
        return None
    if not parsed.tolerance_bps.is_finite() or parsed.tolerance_bps < 0:
        err.write("ERROR: --tolerance-bps must be zero or more.\n")
        return None
    return parsed


def _closed_window(now: datetime, interval: KlineInterval, bars: int) -> tuple[datetime, datetime]:
    """Return ``[since, until)`` covering the last ``bars`` closed bars before ``now``."""
    step = int(_INTERVAL_LENGTH[interval].total_seconds())
    until = datetime.fromtimestamp(int(now.timestamp()) // step * step, tz=UTC)
    return until - _INTERVAL_LENGTH[interval] * bars, until


def _closes(
    fetch: FetchKlines, pair: Pair, interval: KlineInterval, since: datetime, until: datetime
) -> dict[datetime, Decimal]:
    return {k.open_time: k.close.amount for k in fetch(pair, interval, since, until)}


def _check_pair(
    venues: Venues,
    pair: Pair,
    interval: KlineInterval,
    window: tuple[datetime, datetime],
    tolerance_bps: Decimal,
) -> _Outcome:
    since, until = window
    fetched: list[dict[datetime, Decimal]] = []
    for name, fetch in ((venues.primary_name, venues.primary), (venues.backup_name, venues.backup)):
        try:
            fetched.append(_closes(fetch, pair, interval, since, until))
        except InfraError as exc:
            return _Outcome(pair=pair, parity=None, error=f"{name}: {exc}")
    parity = compare_closes(
        pair=pair,
        interval=interval,
        primary=fetched[0],
        backup=fetched[1],
        tolerance_bps=tolerance_bps,
    )
    return _Outcome(pair=pair, parity=parity, error="")


def _passed(outcome: _Outcome) -> bool:
    return outcome.parity is not None and outcome.parity.verdict is ParityVerdict.OK


def _status(outcome: _Outcome) -> str:
    return "unreachable" if outcome.parity is None else outcome.parity.verdict.value


def _detail(outcome: _Outcome) -> str:
    parity = outcome.parity
    if parity is None:
        return outcome.error
    parts = [f"{parity.compared} bars"]
    if parity.worst is not None:
        parts.append(f"max {parity.worst.bps:.2f} bps at {parity.worst.open_time:%Y-%m-%d %H:%M}Z")
    if parity.missing_on_backup:
        parts.append(f"{len(parity.missing_on_backup)} missing on backup")
    if parity.missing_on_primary:
        parts.append(f"{len(parity.missing_on_primary)} missing on primary")
    return ", ".join(parts)


def _pair_json(outcome: _Outcome) -> dict[str, object]:
    parity = outcome.parity
    if parity is None:
        return {"pair": str(outcome.pair), "status": _status(outcome), "error": outcome.error}
    return {
        "pair": str(outcome.pair),
        "status": _status(outcome),
        "compared": parity.compared,
        "max_divergence_bps": str(parity.worst.bps) if parity.worst is not None else None,
        "missing_on_backup": [t.isoformat() for t in parity.missing_on_backup],
        "missing_on_primary": [t.isoformat() for t in parity.missing_on_primary],
    }


def _as_json(
    outcomes: list[_Outcome], venues: Venues, interval: KlineInterval, tolerance: Decimal
) -> str:
    report = {
        "ready": all(_passed(o) for o in outcomes),
        "primary": venues.primary_name,
        "backup": venues.backup_name,
        "interval": interval.value,
        "tolerance_bps": str(tolerance),
        "pairs": [_pair_json(o) for o in outcomes],
    }
    return json.dumps(report, indent=2) + "\n"


def _as_text(
    outcomes: list[_Outcome],
    venues: Venues,
    interval: KlineInterval,
    tolerance: Decimal,
    window: tuple[datetime, datetime],
) -> str:
    since, until = window
    lines = [
        f"failover-check: {venues.primary_name} vs {venues.backup_name}, {interval.value} bars "
        f"{since:%Y-%m-%d %H:%M}Z to {until:%Y-%m-%d %H:%M}Z, tolerance {tolerance} bps"
    ]
    lines.extend(f"  {o.pair!s:<10} {_status(o):<12} {_detail(o)}" for o in outcomes)
    ready = all(_passed(o) for o in outcomes)
    lines.append(f"verdict: {'READY' if ready else 'NOT READY'}")
    return "\n".join(lines) + "\n"


def _live_venues(config: HermesConfig) -> tuple[Venues, Callable[[], None]]:
    binance = BinanceKlinesAdapter.create(
        BinanceKlinesConfig(
            base_url=config.market_data.base_url,
            rate_limit=RateLimitConfig(
                max_weight=config.market_data.rate_limit_max_weight,
                window_seconds=config.market_data.rate_limit_window_seconds,
            ),
            timeout_sec=config.market_data.timeout_sec,
        )
    )
    kraken = KrakenPublicAdapter.create(
        KrakenWireConfig(
            base_url=config.kraken_public.base_url,
            rate_limit=RateLimitConfig(
                max_weight=config.kraken_public.requests_per_second, window_seconds=1
            ),
            timeout_sec=config.kraken_public.timeout_sec,
        )
    )

    def close() -> None:
        binance.close()
        kraken.close()

    venues = Venues(
        primary=lambda p, i, s, u: binance.fetch(pair=p, interval=i, since=s, until=u),
        backup=lambda p, i, s, u: kraken.fetch_klines(pair=p, interval=i, since=s, until=u),
    )
    return venues, close


def _no_resources() -> None:
    """Injected venues own nothing for this command to release."""


def main(
    argv: list[str],
    *,
    stream: TextIO,
    venues: Venues | None = None,
    now: datetime | None = None,
    err: TextIO | None = None,
) -> int:
    """Parse ``argv`` and compare the primary and backup venue.

    Args:
        argv: Arguments after the ``failover-check`` token.
        stream: Output stream.
        venues: Candle sources; defaults to the live Binance and Kraken
            public adapters built from the config.
        now: Current time; defaults to the wall clock.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parsed = _parse(argv, stream, err)
    if parsed is None:
        return _EXIT_USAGE
    if parsed.help:
        return 0
    try:
        config = load_config(parsed.config_dir)
    except ConfigError as exc:
        err.write(f"ERROR: config: {exc}\n")
        return _EXIT_CONFIG
    close = _no_resources
    if venues is None:
        venues, close = _live_venues(config)
    interval = _INTERVALS[parsed.interval]
    window = _closed_window(now or now_utc(), interval, parsed.bars)
    try:
        outcomes = [
            _check_pair(venues, pair, interval, window, parsed.tolerance_bps)
            for pair in config.market_data.parsed_pairs()
        ]
    finally:
        close()
    if parsed.json:
        stream.write(_as_json(outcomes, venues, interval, parsed.tolerance_bps))
    else:
        stream.write(_as_text(outcomes, venues, interval, parsed.tolerance_bps, window))
    return 0 if all(_passed(o) for o in outcomes) else _EXIT_FAIL
