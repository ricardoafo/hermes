"""Tests for ``hermes risk-inputs`` (#1030).

Invariants protected here:

- each followed pair gets one RO-02 line from its latest spread sample,
  saying whether the rule would fire, and a pair with no sample says so;
- the recovery line gives the days since the equity high and the deepest
  drawdown since it;
- the RO-01 lines show the newest recorded correlation of each pair pair;
- RO-02 and the recovery kill are labelled as not acted on in shadow;
- a missing ledger exits 3 and is not created; bad arguments exit 2;
- the command is registered and reachable through ``hermes``.
"""

import io
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.market_data.spread_sample import SpreadSample
from hermes.domain.portfolio.correlation import PairCorrelation
from hermes.domain.portfolio.mark import PortfolioMark
from hermes.domain.portfolio.snapshot import PortfolioSnapshot
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints import risk_inputs as module
from hermes.entrypoints.commands import discover
from tests.support.paths import REPO_ROOT

_NOW = datetime(2026, 10, 17, 12, tzinfo=UTC)
_STRATEGY = StrategyId("swing-donchian-4h")
_BTC = Pair(base=Currency.BTC, quote=Currency.USDT)
_ETH = Pair(base=Currency.ETH, quote=Currency.USDT)
_SOL = Pair(base=Currency.SOL, quote=Currency.USDT)
_CONFIG = REPO_ROOT / "config"


def _snapshot(equity: str, peak: str) -> PortfolioSnapshot:
    seed = PortfolioSnapshot.seed(_STRATEGY, Money(Decimal(5000), Currency.USDT))
    return replace(seed, cash=Decimal(equity), peak_equity=Decimal(peak))


def _ledger(tmp_path: Path) -> Path:
    db = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(db)
    ledger = SqliteLedger(connection)
    ledger.record_spread_sample(
        SpreadSample(
            pair=_BTC,
            sampled_at=_NOW - timedelta(minutes=5),
            book_time=_NOW - timedelta(minutes=5),
            spread=Decimal("0.0012"),
            median_spread_1h=Decimal("0.0001"),
            samples_1h=12,
        )
    )
    ledger.record_spread_sample(
        SpreadSample(
            pair=_ETH,
            sampled_at=_NOW - timedelta(minutes=5),
            book_time=_NOW - timedelta(minutes=5),
            spread=Decimal("0.0002"),
            median_spread_1h=Decimal("0.0001"),
            samples_1h=12,
        )
    )
    for days, equity, peak in ((20, "5000", "5000"), (10, "4600", "5000"), (5, "4800", "5000")):
        ledger.record_portfolio_mark(
            PortfolioMark(
                snapshot=_snapshot(equity, peak),
                pair=_BTC,
                interval=KlineInterval.H4,
                bar_close_time=_NOW - timedelta(days=days),
            )
        )
    for hours, value in ((30, 0.5), (6, 0.84)):
        ledger.record_pair_correlation(
            PairCorrelation(
                pair_a=_BTC,
                pair_b=_ETH,
                window_days=30,
                as_of=_NOW - timedelta(hours=hours),
                correlation=value,
                sample_count=30,
            )
        )
    connection.close()
    return db


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = module.main([*argv, "--config-dir", str(_CONFIG)], stream=out, err=err, now=_NOW)
    return code, out.getvalue(), err.getvalue()


def test_each_pair_gets_its_ro02_line_with_the_verdict(tmp_path: Path) -> None:
    """BTC at 12x its median would fire, ETH at 2x would not, SOL has no sample."""
    code, out, _ = _run(["--db", str(_ledger(tmp_path))])

    lines = out.splitlines()
    start = lines.index("RO-02 spread spike (recorded, not acted on in shadow)")
    assert (code, [line.split("  ")[1] for line in lines[start + 1 : start + 4]]) == (
        0,
        ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    )
    assert "(12.0x, 12 samples)  WOULD FIRE" in lines[start + 1]
    assert "(2.0x, 12 samples)  would not fire" in lines[start + 2]
    assert lines[start + 3] == "  SOL/USDT  no spread sample in the last 24 hours"


def test_the_recovery_line_counts_from_the_equity_high(tmp_path: Path) -> None:
    """The high is 20 days back; the deepest drawdown since is 8%, short of 90 days."""
    _, out, _ = _run(["--db", str(_ledger(tmp_path))])

    lines = out.splitlines()
    start = lines.index("Recovery kill (recorded, not acted on in shadow)")
    assert lines[start + 1] == (
        "  20 days since the equity high (2026-09-27T12:00:00+00:00), deepest drawdown"
        " since 8.00%  would not fire (over 90 days from a drawdown of 8% or more)"
    )


def test_the_ro01_lines_show_the_newest_recorded_correlation(tmp_path: Path) -> None:
    """Of two BTC/ETH rows in the window, only the newer one is shown."""
    _, out, _ = _run(["--db", str(_ledger(tmp_path))])

    lines = out.splitlines()
    start = lines.index("RO-01 correlation lock (acted on in shadow; latest recorded values)")
    assert lines[start + 1 :] == [
        "  BTC/USDT / ETH/USDT  +0.840  (30 of 30 returns)  as of 2026-10-17T06:00:00+00:00"
    ]


def test_an_empty_ledger_says_nothing_is_recorded(tmp_path: Path) -> None:
    """A ledger with no rows exits 0 and names each missing input."""
    db = tmp_path / "ledger.sqlite3"
    with closing(sqlite3.connect(db)) as connection:
        SqliteLedger(connection)

    code, out, _ = _run(["--db", str(db)])

    assert (code, "  no portfolio mark yet" in out, "  no correlation recorded" in out) == (
        0,
        True,
        True,
    )


def test_a_missing_ledger_exits_3_and_is_not_created(tmp_path: Path) -> None:
    """The command never creates a ledger."""
    db = tmp_path / "absent.sqlite3"

    code, _, err = _run(["--db", str(db)])

    assert (code, db.exists(), err) == (3, False, f"ERROR: no ledger at {db}\n")


def test_bad_arguments_exit_2(tmp_path: Path) -> None:
    """An unknown flag is a usage error."""
    code, _, err = _run(["--nope"])

    assert (code, err.startswith("ERROR: could not parse")) == (2, True)


def test_the_command_is_registered() -> None:
    """``hermes risk-inputs`` is in the command registry."""
    assert discover()["risk-inputs"].name == "risk-inputs"
