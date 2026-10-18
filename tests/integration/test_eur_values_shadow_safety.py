"""Shadow-test safety: EUR recording never changes what the strategy does.

Runs the production ``hermes serve`` composition on the fake market's
chandelier-exit script (one ENTRY, one EXIT on BTC/USDT): the swing strategy,
the risk overlay and sizing, the ``ShadowExecutor`` and ``FillKernel`` over the
real Binance depth adapter, the portfolio tracker and the ``TradeRecorder``. The
run happens twice on two fresh ledgers: once with Frankfurter answering, once
with it answering HTTP 503 for the whole run. Every signal, trade, fill-quality
row, lot quantity, portfolio mark and stop mark must be byte-identical; only the
ledger's EUR columns may differ.

Columns left out of the comparison, and why:

- EUR columns (``eur_*``, ``price_eur``, ``fee_eur``, ``value_eur``, the lot's
  ``cost_basis_per_unit`` and the consumption's ``*_eur``): the point of the
  test is that only these differ.
- ``correlation_id``, ``lot_id``, ``payload_sha256`` (which hashes the
  correlation id) and ``recorded_at``: drawn from fresh ULIDs and the wall clock
  on every run, with or without FX.

Reference: issue #386 "Shadow-test safety", change ``trade-eur-values`` R-5,
ADR-016 item 9.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tests.integration.scenarios import markets
from tests.support.fake_market import harness

pytestmark = pytest.mark.integration

_RUN_IDS = ("correlation_id", "payload_sha256")
"""Event payload fields that differ between any two runs, FX or not."""

_BEHAVIOUR_QUERIES = (
    "SELECT pair_base, pair_quote, side, qty_amount, qty_asset, price_amount, fee_amount,"
    " fee_currency, executed_at FROM trades ORDER BY id",
    "SELECT asset, original_qty, remaining_qty, opened_at, strategy_id,"
    " cost_basis_native_per_unit, native_currency, trade_id FROM lots ORDER BY opened_at, asset",
    "SELECT consumed_qty, consumed_asset, consumed_at, trade_id FROM lot_consumptions ORDER BY id",
    "SELECT strategy_id, asset, amount, seeded_at FROM capital_seeds ORDER BY id",
    "SELECT strategy_id, pair_base, pair_quote, interval, bar_close_time, quote, starting_cash,"
    " cash, market_value, equity, realized_pnl, peak_equity, positions_json"
    " FROM portfolio_marks ORDER BY id",
    "SELECT strategy_id, pair_base, pair_quote, entry_time, bar_close_time, stop_price,"
    " chandelier_level, atr, high_since_entry, stop_moved FROM stop_marks"
    " ORDER BY pair_base, bar_close_time",
)

_EVENT_TYPES = ("SignalEmitted", "TradeExecuted", "FillQualityRecorded", "ShadowCapitalSeeded")


def _serve(db: Path, *, fx_down: bool) -> int:
    """Run serve on the chandelier-exit market against a fresh ledger; return its exit code."""
    scenario = markets.chandelier_exit()
    return harness.serve(
        scenario.market, db, live_from=scenario.live_from, fx_down=fx_down
    ).exit_code


def _behaviour(db: Path) -> list[str]:
    """Return everything serve decided and recorded, minus EUR values and per-run ids."""
    with closing(sqlite3.connect(db)) as conn:
        trace = [repr(row) for query in _BEHAVIOUR_QUERIES for row in conn.execute(query)]
        rows = conn.execute(
            "SELECT event_type, occurred_at, payload_json FROM events"
            " WHERE event_type IN (?, ?, ?, ?) ORDER BY id",
            _EVENT_TYPES,
        ).fetchall()
    for event_type, occurred_at, payload_json in rows:
        payload = {k: v for k, v in json.loads(payload_json).items() if k not in _RUN_IDS}
        trace.append(f"{event_type} {occurred_at} {json.dumps(payload, sort_keys=True)}")
    return trace


def _statuses(db: Path) -> list[tuple[str, str]]:
    """Return ``(side, eur_status)`` of every trade, oldest first."""
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute("SELECT side, eur_status FROM trades ORDER BY id").fetchall()


def test_strategy_sizing_and_fills_are_identical_with_and_without_fx(tmp_path: Path) -> None:
    """Invariant (A-5.1): FX availability changes only ledger EUR columns, never behaviour."""
    with_fx, without_fx = tmp_path / "with-fx.sqlite3", tmp_path / "without-fx.sqlite3"
    exit_codes = (_serve(with_fx, fx_down=False), _serve(without_fx, fx_down=True))

    assert "\n".join(_behaviour(with_fx)).encode() == "\n".join(_behaviour(without_fx)).encode()
    assert (exit_codes, _statuses(with_fx), _statuses(without_fx)) == (
        (0, 0),
        [("BUY", "ok"), ("SELL", "ok")],
        [("BUY", "pending"), ("SELL", "pending")],
    )
