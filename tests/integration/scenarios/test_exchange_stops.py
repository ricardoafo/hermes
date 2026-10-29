"""Every real-money position carries a stop held by the exchange (#1080).

The real serve runs with ``allow_mainnet`` on the scripted markets, and the
real Binance spot adapter keeps the stops on the fake market's spot account
(``testnet.binance.vision``, answered in this process; nothing leaves it).
Entries and exits are still the simulated fills the ledger records; the spot
account is funded with the base assets so the stop-loss sells it holds are
covered.

What must hold (Code Owner decision 2026-10-19: the exchange stop is a
backstop at the strategy's own stop, not a new exit rule):

- a stop rests once the entry fills, at the strategy's stop rounded down to
  the exchange's step, for the quantity held;
- it moves when the strategy moves the stop, and is cancelled on exit;
- a restart reconciles the exchange with the ledger before any bar: a stop
  that sold during the downtime is recorded, never placed again; one that
  vanished is placed again; one placed but never recorded is adopted;
- a lost answer never leaves two stops, nor none;
- a shadow sends no request to the spot endpoints.

Failure modes: ``failure_modes.py`` area "exchange and FX".
"""

import contextlib
import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from unittest import mock

import pytest
from structlog.testing import capture_logs

from hermes.adapters.ledger.exchange_stop_rows import (
    SqliteExchangeStopJournal,
    exchange_stop_from_payload,
)
from hermes.application.execution.exchange_stops import STOP_SOURCE
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.exchange_stop_changed import ExchangeStopChanged, StopAction
from hermes.domain.trading.exchange_stop import EXCHANGE_STEP
from tests.integration.scenarios import markets
from tests.integration.scenarios.conftest import RunScenario
from tests.integration.scenarios.faults import ProcessDied
from tests.integration.scenarios.ledger import outline, signals, trades
from tests.support.fake_market import harness
from tests.support.fake_market.spot import SPOT_HOST, SPOT_PATHS, Lost, SpotAccount
from tests.support.serve_loop import events

pytestmark = pytest.mark.integration

type Row = tuple[str, str, Decimal, str]

_CHANDELIER_HELD = {
    "signals": [("BTC/USDT", "entry", ""), ("BTC/USDT", "exit", "chandelier")],
    "trades": [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
}
_FIRST_STOP_RESTS = datetime(2024, 10, 29, 5, tzinfo=UTC)
"""Chandelier market: the breakout bar has filled and its stop rests as order 1000."""
_SECOND_STOP_RESTS = datetime(2024, 10, 30, 1, tzinfo=UTC)
"""Chandelier market: the stop moved at 2024-10-30 00:00 and rests as order 1001."""
_THIRD_STOP_RESTS = datetime(2024, 10, 31, 1, tzinfo=UTC)
"""Chandelier market: the stop moved again at 2024-10-31 00:00 and rests as order 1002."""
_AFTER_THE_GAP = datetime(2024, 10, 31, 12, tzinfo=UTC)
"""Chandelier market: the 04:00-08:00 gap bar (low 115) has closed."""


def _funded() -> SpotAccount:
    """Return a spot account holding enough of every base asset to cover its stops."""
    return SpotAccount(
        free={
            "USDT": Decimal(10_000),
            "BTC": Decimal(1_000),
            "ETH": Decimal(1_000),
            "SOL": Decimal(1_000),
        }
    )


def _step(value: Decimal) -> Decimal:
    return value.quantize(EXCHANGE_STEP, rounding=ROUND_DOWN)


def _rows(db: Path) -> list[Row]:
    """Return each exchange-stop row as (action, order id, stop, reason), oldest first."""
    found = [
        exchange_stop_from_payload(json.loads(row["payload_json"]))
        for row in events(db, "ExchangeStopChanged")
    ]
    return [
        (e.action.value, e.order_id or "", e.stop_price or Decimal(0), e.reason or "")
        for e in found
    ]


def _book(spot: SpotAccount) -> list[tuple[int, str, Decimal]]:
    """Return every stop the account has seen as (order id, status, stop price)."""
    return [(o.order_id, o.status, o.stop_price or Decimal(0)) for o in spot.orders.values()]


def _cancel_outside_hermes(spot: SpotAccount) -> None:
    """Cancel every resting stop, as someone on the exchange's own screen would."""
    for order in spot.orders.values():
        if order.status == "NEW":
            spot.locked[order.pair.base.value] -= order.qty
            spot.free[order.pair.base.value] += order.qty
            order.status = "CANCELED"


def _alerts(db: Path) -> list[str]:
    """Return the severity of each alert the stop keeper recorded, oldest first."""
    payloads = [json.loads(row["payload_json"]) for row in events(db, "AnomalyDetected")]
    return [p["severity"] for p in payloads if p["source"] == STOP_SOURCE]


# SEV-1: S12 — an open real-money position never rests without its exchange stop (ADR-024 §7)
def test_the_stop_is_placed_at_entry_moved_with_the_strategy_and_cancelled_on_exit(
    run_scenario: RunScenario,
) -> None:
    """Regime-break exit: placed at the entry's stop, moved once, cancelled by the exit."""
    spot = _funded()
    run = run_scenario(markets.regime_break(), real_money=True, spot=spot)
    entry, exit_ = signals(run.db)
    buy = trades(run.db)[0]
    first, moved = _step(entry.stop_price), _step(exit_.stop_price)
    assert (run.exit_code, _rows(run.db), _book(spot), spot.orders[1000].qty) == (
        0,
        [
            ("placed", "BTCUSDT:1000", first, "protect_position"),
            ("cancelled", "BTCUSDT:1000", first, "stop_moved"),
            ("placed", "BTCUSDT:1001", moved, "protect_position"),
            ("cancelled", "BTCUSDT:1001", moved, "position_closed"),
        ],
        [(1000, "CANCELED", first), (1001, "CANCELED", moved)],
        _step(buy.qty),
    )


def test_the_stop_is_cancelled_before_the_exit_sells(run_scenario: RunScenario) -> None:
    """The exchange locks the stop's quantity, so the exit's sell comes after the cancel."""
    run = run_scenario(markets.regime_break(), real_money=True, spot=_funded())
    with contextlib.closing(sqlite3.connect(run.db)) as connection:
        order = [
            f"{kind}:{action or side}"
            for kind, action, side in connection.execute(
                "SELECT event_type, json_extract(payload_json, '$.action'), "
                "json_extract(payload_json, '$.side') FROM events "
                "WHERE event_type IN ('ExchangeStopChanged', 'TradeExecuted') ORDER BY id"
            )
        ]
    assert order[-2:] == ["ExchangeStopChanged:cancelled", "TradeExecuted:SELL"]


def test_the_strategys_exit_stays_the_trade_when_the_exchange_stop_also_sells(
    run_scenario: RunScenario,
) -> None:
    """Chandelier gap: the exchange stop fills, the ledger still records the signed exit."""
    spot = _funded()
    run = run_scenario(markets.chandelier_exit(), real_money=True, spot=spot)
    exit_stop = _step(signals(run.db)[1].stop_price)
    assert (outline(run.db), _rows(run.db)[-1], _book(spot)[-1], _alerts(run.db)) == (
        _CHANDELIER_HELD,
        ("filled", "BTCUSDT:1002", exit_stop, "sold_on_exchange"),
        (1002, "FILLED", exit_stop),
        ["WARNING"],
    )


# SEV-1: S12 — a restart reconciles the exchange's stops with the ledger before any bar (#858)
def test_a_stop_that_sold_during_the_downtime_is_recorded_and_never_placed_again(
    run_scenario: RunScenario,
) -> None:
    """Down from before the gap to after it: the restart finds the stop sold and places none."""
    spot = _funded()
    run_scenario(markets.chandelier_exit(), real_money=True, spot=spot, until=_THIRD_STOP_RESTS)
    restart = run_scenario(
        markets.chandelier_exit(), live_from=_AFTER_THE_GAP, real_money=True, spot=spot
    )
    placed, filled = _rows(restart.db)[-2:]
    assert (outline(restart.db), placed[:2], filled, len(spot.orders)) == (
        _CHANDELIER_HELD,
        ("placed", "BTCUSDT:1002"),
        ("filled", "BTCUSDT:1002", placed[2], "sold_on_exchange"),
        3,
    )


def test_a_stop_cancelled_outside_hermes_during_the_downtime_is_placed_again(
    run_scenario: RunScenario,
) -> None:
    """The restart finds the recorded stop gone, alerts, and rests it again at the same level."""
    spot = _funded()
    run_scenario(markets.chandelier_exit(), real_money=True, spot=spot, until=_SECOND_STOP_RESTS)
    _cancel_outside_hermes(spot)
    restart = run_scenario(
        markets.chandelier_exit(),
        live_from=_SECOND_STOP_RESTS.replace(hour=2),
        until=_SECOND_STOP_RESTS.replace(hour=3),
        real_money=True,
        spot=spot,
    )
    level = _rows(restart.db)[2][2]
    assert (_rows(restart.db)[2:], _book(spot)[1:], _alerts(restart.db)) == (
        [
            ("placed", "BTCUSDT:1001", level, "protect_position"),
            ("gone", "BTCUSDT:1001", level, "no_longer_on_exchange"),
            ("placed", "BTCUSDT:1002", level, "protect_position"),
        ],
        [(1001, "CANCELED", level), (1002, "NEW", level)],
        ["WARNING"],
    )


@pytest.mark.parametrize("how", [Lost.PLACED, Lost.DROPPED])
def test_a_lost_answer_to_the_first_stop_leaves_exactly_one_stop(
    run_scenario: RunScenario, how: Lost
) -> None:
    """503 after the stop reached the book, or before: one stop rests either way, recorded."""
    spot = _funded()
    spot.lose_next_order(how)
    run = run_scenario(
        markets.chandelier_exit(), real_money=True, spot=spot, until=_FIRST_STOP_RESTS
    )
    resting = [o for o in spot.orders.values() if o.status == "NEW"]
    assert (len(resting), [r[:2] for r in _rows(run.db)]) == (
        1,
        [("placed", f"BTCUSDT:{resting[0].order_id}")],
    )


def test_a_stop_placed_but_never_recorded_is_adopted_after_the_crash(
    run_scenario: RunScenario, tmp_path: Path
) -> None:
    """Killed between the placement and its row: the restart adopts the stop, places no second."""
    spot = _funded()
    scenario = markets.chandelier_exit()
    original = SqliteExchangeStopJournal.record_exchange_stops
    crashed_at: list[datetime] = []

    def die_once(self: SqliteExchangeStopJournal, evts: Sequence[DomainEvent]) -> None:
        placed = [e for e in evts if isinstance(e, ExchangeStopChanged)]
        if not crashed_at and placed and placed[0].action is StopAction.PLACED:
            crashed_at.append(placed[0].occurred_at)
            raise ProcessDied
        original(self, evts)

    with (
        capture_logs(),
        mock.patch.object(SqliteExchangeStopJournal, "record_exchange_stops", die_once),
        contextlib.suppress(ProcessDied),
    ):
        harness.serve(
            scenario.market,
            tmp_path / "ledger.sqlite3",
            live_from=scenario.live_from,
            until=_FIRST_STOP_RESTS,
            real_money=True,
            spot=spot,
        )
    restart = run_scenario(
        scenario, live_from=crashed_at[0], until=_FIRST_STOP_RESTS, real_money=True, spot=spot
    )
    assert ([r[:2] + r[3:] for r in _rows(restart.db)], _book(spot)[0][:2], len(spot.orders)) == (
        [("adopted", "BTCUSDT:1000", "found_on_exchange")],
        (1000, "NEW"),
        1,
    )


# SEV-1: S1 — a shadow or a dry run never reaches an exchange's order endpoints (I-08)
def test_a_shadow_with_a_venue_wired_sends_no_request_to_the_spot_endpoints(
    run_scenario: RunScenario,
) -> None:
    """Without ``allow_mainnet`` the venue is never called: no order, no row."""
    spot = _funded()
    run = run_scenario(markets.chandelier_exit(), spot=spot)
    spot_requests = [p for host, p, _ in run.requests if host == SPOT_HOST and p in SPOT_PATHS]
    assert (outline(run.db), spot_requests, spot.orders, _rows(run.db)) == (
        _CHANDELIER_HELD,
        [],
        {},
        [],
    )
