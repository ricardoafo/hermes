#!/usr/bin/env python3
"""Train decision replay for engine PRs (issue #798, ADR-024).

An engine change must not alter a shadowed strategy's decisions without the
Code Owner knowing, because an altered decision restarts that strategy's
shadow clock (ADR-021). CI runs ``record`` once on the PR's base tree and
once on its head tree, then ``diff`` on the two files.

``record`` replays the enabled roster strategy over the Train split of the
data manifest, through the same bars, fill kernel and execution model as
``run_portfolio_backtest.py``, and writes one JSON line per in-window signal:
signal time, pair, kind, side, executed or not, and the rule that refused
it. It writes no price, size, equity or metric and logs no trial, because
nothing here evaluates the strategy. The bars load through the manifest as
an UNTRUSTED run, and any window but Train is refused.

``diff`` matches the two files on signal time, pair, kind and side. A signal
on one side only, or one whose outcome differs, is a changed decision; a
different refusing rule with the same outcome is not, as in
``hermes parity check``. It prints one line per change and the count.

Usage:
    HERMES_DATA_DIR=/srv/hermes-data python scripts/backtest/decision_replay.py \\
        record --out base.jsonl [--config-dir config] [--manifest PATH]
    python scripts/backtest/decision_replay.py diff base.jsonl head.jsonl

Exit codes:
    0 - record: decisions written; diff: no decision changed
    1 - diff: at least one decision changed
    2 - usage error
    3 - record: data, config or fee history missing; diff: a file unreadable
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import structlog
from run_calibration import (
    DATA_MANIFEST_JSON,
    FEE_HISTORY_CSV,
    _load_manifest,
    _strategy_parameters,
)
from run_calibration_full import _enabled_roster
from run_portfolio_backtest import _kernel, _load, _parse_pairs

from hermes.adapters.fees.binance_history import load_binance_fee_history
from hermes.application.backtest.replay import ExecutionModel, replay
from hermes.application.execution.kernel import (
    Declined,
    Executed,
    ExecutionOutcome,
    FillKernel,
    Halted,
    MarketContext,
)
from hermes.application.strategy_protocol.preregistration import PreRegistration
from hermes.domain.errors import DataManifestFormatError, DomainError, InfraError
from hermes.domain.fees.fee_schedule import FeeSchedule
from hermes.domain.fees.fee_schedule_selector import (
    FeeHistoryOutOfRangeError,
    select_fee_schedule,
)
from hermes.domain.market_data.dataset_manifest import DataSplit
from hermes.domain.strategy.signal import StrategySignal
from hermes.domain.strategy.swing.adapter import SwingStrategyAdapter
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.logging import new_correlation_id

EXIT_SAME: Final = 0
EXIT_CHANGED: Final = 1
EXIT_USAGE: Final = 2
EXIT_ERROR: Final = 3

_RULE_ID: Final = re.compile(r"^(RO-\d+):")
_SHOWN: Final = 50
"""Changed decisions printed in full; the count always covers all of them."""

type Key = tuple[str, str, str, str]
"""Signal time (ISO, UTC), pair, kind, side: what two decisions are matched on."""


@dataclass(frozen=True, slots=True)
class Decision:
    """One in-window signal and what became of it.

    Attributes:
        signal_time: Close time of the bar that triggered the signal, ISO UTC.
        pair: ``BASE/QUOTE``.
        kind: ``entry`` or ``exit``.
        side: ``long``.
        executed: Whether a fill was recorded.
        rule: What refused it (a router rule, ``HALT:RO-xx`` or ``kill``);
            empty when executed.
    """

    signal_time: str
    pair: str
    kind: str
    side: str
    executed: bool
    rule: str = ""

    @property
    def key(self) -> Key:
        """The identity two decisions are matched on."""
        return (self.signal_time, self.pair, self.kind, self.side)

    def describe(self) -> str:
        """Return ``entry long executed`` or ``entry long not executed (rule)``."""
        if self.executed:
            return f"{self.kind} {self.side} executed"
        return f"{self.kind} {self.side} not executed ({self.rule or 'unknown'})"


def refusing_rule(outcome: ExecutionOutcome) -> str:
    """Return what refused a signal, or ``""`` when it filled."""
    match outcome:
        case Executed():
            return ""
        case Halted():
            return "kill"
        case Declined(rule="HALT", reason=reason):
            found = _RULE_ID.match(reason)
            return f"HALT:{found.group(1) if found else 'unknown'}"
        case Declined(rule=rule):
            return rule


class RecordingKernel(FillKernel):
    """The production fill kernel, keeping each signal's decision as it executes."""

    def __init__(self, **kwargs: Any) -> None:
        """Compose the kernel exactly as :class:`FillKernel` does."""
        super().__init__(**kwargs)
        self.decisions: list[Decision] = []

    def execute(
        self,
        signal: StrategySignal,
        pair: Pair,
        market: MarketContext,
        *,
        correlation_id: str,
    ) -> ExecutionOutcome:
        """Execute through the production kernel and record the decision."""
        outcome = super().execute(signal, pair, market, correlation_id=correlation_id)
        self.decisions.append(
            Decision(
                signal_time=signal.signal_time.astimezone(UTC).isoformat(),
                pair=str(pair),
                kind=signal.kind.value,
                side=signal.side.value,
                executed=isinstance(outcome, Executed),
                rule=refusing_rule(outcome),
            )
        )
        return outcome


def train_window(manifest_path: Path) -> tuple[datetime, datetime]:
    """Return the Train window as the backtest workflow derives it: whole days, UTC."""
    manifest = _load_manifest(manifest_path)
    train = manifest.splits[DataSplit.TRAIN]
    return (
        datetime.combine(train.start.date(), datetime.min.time(), UTC),
        datetime.combine(train.end.date(), datetime.min.time(), UTC),
    )


def write_decisions(path: Path, decisions: list[Decision]) -> None:
    """Write one JSON object per decision, in replay order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (
        json.dumps(
            {
                "signal_time": d.signal_time,
                "pair": d.pair,
                "kind": d.kind,
                "side": d.side,
                "executed": d.executed,
                "rule": d.rule,
            },
            sort_keys=True,
        )
        for d in decisions
    )
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def read_decisions(path: Path) -> list[Decision]:
    """Read a ``record`` file.

    Raises:
        OSError: The file cannot be read.
        ValueError: A line is not a decision.
    """
    decisions: list[Decision] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            row = json.loads(line)
            decisions.append(
                Decision(
                    signal_time=str(row["signal_time"]),
                    pair=str(row["pair"]),
                    kind=str(row["kind"]),
                    side=str(row["side"]),
                    executed=bool(row["executed"]),
                    rule=str(row.get("rule", "")),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError) as err:
            raise ValueError(f"{path}:{number}: not a decision: {err}") from err
    return decisions


def changed_decisions(base: list[Decision], head: list[Decision]) -> list[str]:
    """Return one line per changed decision, oldest signal first.

    A signal on one side only is a change; a signal on both sides whose
    outcome differs is a change. The refusing rule alone never makes two
    outcomes differ.
    """
    before = {d.key: d for d in base}
    after = {d.key: d for d in head}
    lines: list[str] = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if old is not None and new is not None and old.executed == new.executed:
            continue
        at, pair, _, _ = key
        was = "no signal" if old is None else old.describe()
        now = "no signal" if new is None else new.describe()
        lines.append(f"CHANGED {at} {pair}: base {was}; head {now}")
    return lines


async def _record(args: argparse.Namespace) -> int:
    # The kernel logs fill prices, sizes and notionals; a CI log carries decisions only.
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    try:
        roster = _enabled_roster(Path(args.config_dir))
        start, end = train_window(Path(args.manifest))
        manifest = _load_manifest(Path(args.manifest))
        pairs = _parse_pairs(",".join(PreRegistration().universe()))
        bars, _ = await _load(manifest, pairs, start, end)
        fee_rows = load_binance_fee_history(Path(args.fee_history))
    except (
        ConfigError,
        DataManifestFormatError,
        DomainError,
        InfraError,
        OSError,
        ValueError,
    ) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return EXIT_ERROR

    def fees_at(at: datetime, for_pair: Pair) -> FeeSchedule:
        return select_fee_schedule(fee_rows, at, for_pair)

    strategy_id = StrategyId(roster.name)
    kernel = _kernel(
        strategy_id,
        Money(amount=roster.capital_budget, currency=roster.quote),
        roster.sizing_policy(),
        kernel_type=RecordingKernel,
    )
    assert isinstance(kernel, RecordingKernel)  # noqa: S101 — narrows the type for the reader
    parameters = _strategy_parameters()
    try:
        replay(
            bars,
            strategy={pair: SwingStrategyAdapter(**parameters) for pair in pairs},
            kernel=kernel,
            window_start=start,
            window_end=end,
            fees_at=fees_at,
            correlation_id=new_correlation_id,
            model=ExecutionModel(),
        )
    except FeeHistoryOutOfRangeError as err:
        print(f"ERROR: fee history does not cover a signal: {err}", file=sys.stderr)
        return EXIT_ERROR
    write_decisions(Path(args.out), kernel.decisions)
    print(
        f"Train {start.date()} -> {end.date()} ({strategy_id.value}): "
        f"{len(kernel.decisions)} decisions written to {args.out}. No trial logged."
    )
    return EXIT_SAME


def _diff(args: argparse.Namespace) -> int:
    try:
        base = read_decisions(Path(args.base))
        head = read_decisions(Path(args.head))
    except (OSError, ValueError) as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return EXIT_ERROR
    lines = changed_decisions(base, head)
    for line in lines[:_SHOWN]:
        print(line)
    if len(lines) > _SHOWN:
        print(f"... {len(lines) - _SHOWN} more")
    if lines:
        print(f"{len(lines)} of {len(base)} base decisions changed on Train")
        return EXIT_CHANGED
    print(f"OK: all {len(head)} Train decisions match the base")
    return EXIT_SAME


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run ``record`` or ``diff``."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="Replay Train and write its decisions")
    record.add_argument("--out", required=True, help="Decision file to write (JSON lines)")
    record.add_argument("--config-dir", default="config", help="Directory holding strategies.yaml")
    record.add_argument("--manifest", default=str(DATA_MANIFEST_JSON), help="Data manifest")
    record.add_argument("--fee-history", default=str(FEE_HISTORY_CSV), help="Fee history CSV")
    diff = commands.add_parser("diff", help="Compare a base and a head decision file")
    diff.add_argument("base")
    diff.add_argument("head")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return EXIT_USAGE if exit_.code else EXIT_SAME
    if args.command == "record":
        return asyncio.run(_record(args))
    return _diff(args)


if __name__ == "__main__":
    sys.exit(main())
