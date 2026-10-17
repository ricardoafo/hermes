"""``hermes risk-inputs`` — the risk inputs shadow records, and whether a rule would fire (#1030).

Usage: ``hermes risk-inputs [--strategy ID] [--db PATH] [--config-dir DIR]``

Under the #514 ruling (the shadow set), shadow runs what the backtest that
gated the strategy runs. RO-02 (spread spike) and the §10 live-phase recovery
kill stay off, but their inputs are recorded, so this command shows, without
acting on anything:

- per pair, the latest spread sample, its 1-hour median, the multiple and
  whether RO-02 (spread at least 10x the median) would fire;
- for the strategy, the days since its equity high, the deepest drawdown
  since then and whether the recovery kill (more than 90 days from a
  drawdown of at least 8%) would fire;
- the latest recorded 30-day pair correlations, which RO-01 acts on in shadow
  (computed in process from the same 1D closes).

The strategy defaults to the enabled roster entry and the pairs come from
``config/market_data.yaml``. The ledger is opened with SQLite's ``mode=ro``
and never migrated, so the command is safe next to ``hermes serve``.

Exit codes: 0 success (also when nothing is recorded yet), 2 usage, 3 config,
no ledger or unreadable.

Reference: issues #514, #835, #1030.
"""

import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, TextIO

from hermes.adapters.ledger.connection import open_ledger
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.risk_inputs.readings import (
    RECOVERY_DAYS,
    RecoveryReading,
    SpreadReading,
    recovery_reading,
    spread_reading,
)
from hermes.domain.errors import LedgerError
from hermes.domain.portfolio.correlation import PairCorrelation
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.serve import DEFAULT_DB_PATH
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.time import now_utc

__all__ = ["main"]

_USAGE: Final = (
    "Usage: hermes risk-inputs [--strategy ID] [--db PATH] [--config-dir DIR]\n"
    "\n"
    "Shows the risk inputs shadow records and whether each rule would fire. Read-only.\n"
    "RO-02 and the recovery kill are recorded only: shadow never acts on them (#514).\n"
    "Exit codes: 0 success, 2 usage, 3 config, no ledger or unreadable.\n"
)
_NOT_ACTED: Final = "recorded, not acted on in shadow"
_SPREAD_LOOKBACK: Final = timedelta(days=1)
_CORRELATION_LOOKBACK: Final = timedelta(days=3)
_EPOCH: Final = datetime.fromisoformat("2000-01-01T00:00:00+00:00")


def main(
    argv: list[str],
    *,
    stream: TextIO,
    err: TextIO | None = None,
    now: datetime | None = None,
) -> int:
    """Run ``hermes risk-inputs``.

    Args:
        argv: Arguments after ``risk-inputs``.
        stream: Output stream.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.
        now: Current UTC time; defaults to the wall clock.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parsed = _parse(argv, stream, err)
    if isinstance(parsed, int):
        return parsed
    if not parsed.db.is_file():
        err.write(f"ERROR: no ledger at {parsed.db}\n")
        return 3
    at = now if now is not None else now_utc()
    try:
        connection = open_ledger(parsed.db, read_only=True)
    except sqlite3.Error as exc:
        err.write(f"ERROR: could not open the ledger: {exc}\n")
        return 3
    try:
        lines = _report(
            SqliteLedger(connection), strategy_id=parsed.strategy_id, pairs=parsed.pairs, now=at
        )
    except LedgerError as exc:
        err.write(f"ERROR: could not read the ledger: {exc}\n")
        return 3
    finally:
        connection.close()
    stream.write("\n".join(lines) + "\n")
    return 0


def _parse(argv: list[str], stream: TextIO, err: TextIO) -> argparse.Namespace | int:
    """Parse ``argv`` and resolve the strategy and pairs, or return the exit code."""
    parser = argparse.ArgumentParser(prog="hermes risk-inputs", add_help=False)
    parser.add_argument("--strategy", default=None)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write("ERROR: could not parse `hermes risk-inputs` arguments.\n" + _USAGE)
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    try:
        config = load_config(parsed.config_dir)
    except ConfigError as exc:
        err.write(f"ERROR: config: {exc}\n")
        return 3
    enabled = next((s.name for s in config.strategies.strategies if s.enabled), None)
    name = parsed.strategy or enabled
    if name is None:
        err.write("ERROR: no strategy is enabled in the roster; pass --strategy ID.\n")
        return 2
    parsed.strategy_id = StrategyId(name)
    parsed.pairs = config.market_data.parsed_pairs()
    return parsed


def _report(
    ledger: SqliteLedger, *, strategy_id: StrategyId, pairs: tuple[Pair, ...], now: datetime
) -> list[str]:
    """Return the report lines for ``strategy_id`` at ``now``."""
    lines = [f"Risk inputs of {strategy_id.value} at {now.isoformat(timespec='seconds')}", ""]
    lines.append(f"RO-02 spread spike ({_NOT_ACTED})")
    for pair in pairs:
        samples = ledger.spread_samples(pair, now - _SPREAD_LOOKBACK, now + timedelta(seconds=1))
        lines.append(_spread_line(pair, spread_reading(samples[-1]) if samples else None))
    lines.append("")
    lines.append(f"Recovery kill ({_NOT_ACTED})")
    marks = ledger.portfolio_marks(strategy_id, _EPOCH, now + timedelta(seconds=1))
    lines.append(_recovery_line(recovery_reading(marks, now=now)))
    lines.append("")
    lines.append("RO-01 correlation lock (acted on in shadow; latest recorded values)")
    lines.extend(_correlation_lines(ledger.pair_correlations(now - _CORRELATION_LOOKBACK, now)))
    return lines


def _spread_line(pair: Pair, reading: SpreadReading | None) -> str:
    """Return one pair's RO-02 line."""
    if reading is None:
        return f"  {pair}  no spread sample in the last 24 hours"
    sample = reading.sample
    multiple = "n/a" if reading.multiple is None else f"{reading.multiple:.1f}x"
    verdict = "WOULD FIRE" if reading.would_fire else "would not fire"
    return (
        f"  {pair}  spread {sample.spread:.6f}  1h median {sample.median_spread_1h:.6f}"
        f"  ({multiple}, {sample.samples_1h} samples)  {verdict}"
        f"  at {sample.book_time.isoformat(timespec='seconds')}"
    )


def _recovery_line(reading: RecoveryReading | None) -> str:
    """Return the recovery kill line."""
    if reading is None:
        return "  no portfolio mark yet"
    verdict = "WOULD FIRE" if reading.would_fire else "would not fire"
    return (
        f"  {reading.days_since_high} days since the equity high"
        f" ({reading.high_at.isoformat(timespec='seconds')}),"
        f" deepest drawdown since {reading.deepest_drawdown:.2%}"
        f"  {verdict} (over {RECOVERY_DAYS} days from a drawdown of 8% or more)"
    )


def _correlation_lines(rows: tuple[PairCorrelation, ...]) -> list[str]:
    """Return one line per pair pair: its newest recorded correlation."""
    latest: dict[tuple[Pair, Pair], PairCorrelation] = {}
    for row in rows:
        latest[(row.pair_a, row.pair_b)] = row
    if not latest:
        return ["  no correlation recorded in the last 3 days"]
    return [
        f"  {row.pair_a} / {row.pair_b}  {row.correlation:+.3f}"
        f"  ({row.sample_count} of {row.window_days} returns)"
        f"  as of {row.as_of.isoformat(timespec='seconds')}"
        for row in sorted(latest.values(), key=lambda r: (str(r.pair_a), str(r.pair_b)))
    ]
