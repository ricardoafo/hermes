"""After a run, serve's portfolio and the ledger's tax lots tell the same story (issue #1019).

The portfolio tracker and the FIFO lot projection are two books kept by
different code from the same fills. Nothing else compares them end to end, so
a fill that reached one and not the other, or a rounding step applied on one
side only, would leave them apart with every unit test green (issue #358).
"""

import os
import sqlite3
from collections.abc import Callable
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.domain.value_objects.strategy_id import StrategyId
from tests.integration.scenarios import paths
from tests.integration.scenarios.ledger import Trade, trades
from tests.integration.scenarios.oracle import run_path
from tests.integration.scenarios.test_generated_paths import GENERATORS, SEEDS_ENV, seeds_for

pytestmark = pytest.mark.integration

_STRATEGY = StrategyId("swing-donchian-4h")
"""The roster name serve runs under (``serve.py``: ``StrategyId(roster.name)``)."""

_QUOTE = "USDT"

_EXACT_DIGITS = 200
"""Digits that hold any sum of ledger decimals without rounding."""


def _lot_total(connection: sqlite3.Connection, asset: str) -> Decimal:
    """Return the open quantity of ``asset``: the sum of its ``lots.remaining_qty``."""
    rows = connection.execute("SELECT remaining_qty FROM lots WHERE asset = ?", (asset,)).fetchall()
    with localcontext() as ctx:
        # The lots hold up to 55 digits; the default 28-digit context would round the sum.
        ctx.prec = _EXACT_DIGITS
        return sum((Decimal(text) for (text,) in rows), Decimal(0))


def _round_trips(done: list[Trade]) -> int:
    """Return how many SELLs closed a position, one per round trip (a position is sold whole)."""
    return sum(1 for t in done if t.side == "SELL")


def _drift(path: paths.GeneratedPath, db: Path) -> dict[str, tuple[Decimal, Decimal]]:
    """Return, per figure, the portfolio's value and the ledger lots' value where they differ."""
    connection = sqlite3.connect(db)
    try:
        mark = SqliteLedger(connection).latest_portfolio_mark(_STRATEGY)
        assert mark is not None, f"serve stored no portfolio mark\n{path.notes}"
        snapshot = mark.snapshot
        held = {pair.base.value: p.quantity for pair, p in snapshot.positions.items()}
        figures = {
            _QUOTE: (snapshot.cash, _lot_total(connection, _QUOTE)),
        }
        for base in sorted({t.pair.split("/")[0] for t in trades(db)}):
            figures[base] = (held.get(base, Decimal(0)), _lot_total(connection, base))
    finally:
        connection.close()
    return {name: pair for name, pair in figures.items() if pair[0] != pair[1]}


def test_after_five_round_trips_the_portfolio_equals_the_ledger_lots(
    tmp_path: Path,
) -> None:
    """Tracker cash equals the USDT lots and each position equals its coin's open lots, exactly.

    Random-walk seed 5 buys and sells across a restart: five exits over ten
    trades. The portfolio mark serve stored on its last bar
    (``SqliteLedger.latest_portfolio_mark``: cash and positions) is compared
    with the ``lots`` table the trades project into: the USDT lots (the quote
    leg, change 405-usdt-fifo) and the open lots of each coin traded.
    """
    path = paths.random_walk(5)
    db = tmp_path / "ledger.sqlite3"
    run_path(path, db)
    done = trades(db)
    assert (_round_trips(done), len(done)) == (5, 10), "\n".join(path.notes)
    assert _drift(path, db) == {}, "\n".join(path.notes)


@pytest.mark.parametrize(
    ("build", "seed"),
    [
        pytest.param(build, seed, id=f"{kind}-{seed}")
        for kind, build in GENERATORS.items()
        for seed in seeds_for(kind, os.environ.get(SEEDS_ENV))
    ],
)
def test_on_every_generated_path_the_portfolio_equals_the_ledger_lots(
    tmp_path: Path, build: Callable[[int], paths.GeneratedPath], seed: int
) -> None:
    """The cash and position equality holds on each seeded path, outages and restarts included.

    Each path closes at least one position, so the lots have been drawn down
    and refilled and not only opened.
    """
    path = build(seed)
    db = tmp_path / "ledger.sqlite3"
    run_path(path, db)
    assert _round_trips(trades(db)) >= 1, "\n".join(path.notes)
    assert _drift(path, db) == {}, "\n".join(path.notes)
