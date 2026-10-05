"""Entries, the three exits and sizing, through the real serve (pre-registration §5).

Each test runs the production composition on a scripted market and reads the
ledger. Sizes, prices and fees are worked out in :mod:`.expected` from the
pre-registration text.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pytest

from hermes.domain.events.signal_emitted import SignalEmitted
from tests.integration.scenarios import expected, markets
from tests.integration.scenarios.conftest import RunScenario
from tests.integration.scenarios.ledger import outline, signals, trades
from tests.support.serve_loop import events

pytestmark = pytest.mark.integration

_PLACES = Decimal("1e-12")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_PLACES)


def test_a_breakout_opens_a_position_and_the_chandelier_stop_closes_it(
    run_scenario: RunScenario,
) -> None:
    """§5.1 entry then §5.2 chandelier exit: one BUY, one SELL, both signals notified."""
    run = run_scenario(markets.chandelier_exit())
    notified = [
        (str(e.pair), e.kind.value, e.exit_reason or "")
        for e in run.notified
        if isinstance(e, SignalEmitted)
    ]
    assert (run.exit_code, outline(run.db), notified) == (
        0,
        {
            "signals": [("BTC/USDT", "entry", ""), ("BTC/USDT", "exit", "chandelier")],
            "trades": [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
        },
        [("BTC/USDT", "entry", ""), ("BTC/USDT", "exit", "chandelier")],
    )


def test_the_entry_size_price_and_fee_follow_the_pre_registration(
    run_scenario: RunScenario,
) -> None:
    """The BUY is the §5.4 size, filled by walking the live book, charged the §6.1 fee."""
    run = run_scenario(markets.chandelier_exit())
    entry = signals(run.db)[0]
    buy = trades(run.db)[0]
    qty = expected.entry_qty(entry.entry_price, entry.stop_price)
    price = expected.walk(entry.entry_price, qty, buy=True)
    assert (_q(buy.qty), _q(buy.price), _q(buy.fee)) == (
        _q(qty),
        _q(price),
        _q(expected.fee(price, qty)),
    )


def test_each_live_fill_records_the_pre_registered_model_price_beside_it(
    run_scenario: RunScenario,
) -> None:
    """§6.2: the entry's model price is the bar close plus 5 bps; the exit's is the stop less 15."""
    run = run_scenario(markets.chandelier_exit())
    entry, exit_ = signals(run.db)
    recorded = [
        Decimal(json.loads(row["payload_json"])["model_price"])
        for row in events(run.db, "FillQualityRecorded")
    ]
    assert [_q(p) for p in recorded] == [
        _q(entry.entry_price * (1 + expected.ENTRY_SLIPPAGE)),
        _q(exit_.stop_price * (1 - expected.STOP_SLIPPAGE)),
    ]


def test_the_cap_sets_the_size_when_the_stop_is_close(run_scenario: RunScenario) -> None:
    """With a stop 1% under the entry the risk budget allows far more, so $250 is bought."""
    run = run_scenario(markets.chandelier_exit())
    entry = signals(run.db)[0]
    buy = trades(run.db)[0]
    assert (
        expected.RISK_BUDGET / (entry.entry_price - entry.stop_price) * entry.entry_price
        > expected.POSITION_CAP
    )
    assert _q(buy.qty * entry.entry_price) == _q(expected.POSITION_CAP)


def test_the_exit_sells_the_whole_position_into_the_live_book(run_scenario: RunScenario) -> None:
    """The SELL is the full held quantity, walked down the bids around the gap bar's close."""
    run = run_scenario(markets.chandelier_exit())
    buy, sell = trades(run.db)
    gap_close = Decimal(120)
    price = expected.walk(gap_close, buy.qty, buy=False)
    assert (sell.qty, _q(sell.price), _q(sell.fee)) == (
        buy.qty,
        _q(price),
        _q(expected.fee(price, buy.qty)),
    )


@pytest.mark.slow
@pytest.mark.timeout(240)  # ninety days of bars on three pairs: about 12 s, more on a loaded runner
def test_a_position_still_open_after_90_days_is_closed_by_the_time_stop(
    run_scenario: RunScenario,
) -> None:
    """§5.2 time stop: the exit fires on the first 4h close 90 days after the entry bar."""
    run = run_scenario(markets.time_stop())
    found = signals(run.db)
    assert (outline(run.db), found[1].at - found[0].at) == (
        {
            "signals": [("BTC/USDT", "entry", ""), ("BTC/USDT", "exit", "time_stop")],
            "trades": [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
        },
        timedelta(days=90),
    )


def test_a_daily_close_under_ema200_closes_the_position(run_scenario: RunScenario) -> None:
    """§5.2 regime break: the daily close at 148 under EMA(200) exits, with no stop hit."""
    run = run_scenario(markets.regime_break())
    assert outline(run.db) == {
        "signals": [("BTC/USDT", "entry", ""), ("BTC/USDT", "exit", "regime_break")],
        "trades": [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")],
    }


def test_the_risk_budget_sets_the_size_when_the_stop_is_far(run_scenario: RunScenario) -> None:
    """With a stop over 20% under the entry, $50 of risk buys less than the $250 cap (§5.4)."""
    run = run_scenario(markets.regime_break())
    entry = signals(run.db)[0]
    buy = trades(run.db)[0]
    notional = expected.entry_notional(entry.entry_price, entry.stop_price)
    assert notional < expected.POSITION_CAP
    assert _q(buy.qty) == _q(expected.entry_qty(entry.entry_price, entry.stop_price))


def test_a_breakout_under_ema200_opens_nothing(run_scenario: RunScenario) -> None:
    """§5.1 needs all three: breakout and ADX hold on the jump bar, the regime does not."""
    run = run_scenario(markets.regime_filter_blocks())
    checks = {
        (line["entry_breakout"], line["entry_momentum"], line["entry_regime"])
        for line in run.log_events("serve.bar.processed")
        if line["pair"] == "BTC/USDT" and "entry_breakout" in line
    }
    assert (outline(run.db), ("yes", "yes", "no") in checks) == (
        {"signals": [], "trades": []},
        True,
    )


def test_a_breakout_without_momentum_opens_nothing(run_scenario: RunScenario) -> None:
    """§5.1 momentum gate: breakout and regime hold on the jump bar, ADX(14) under 25 does not."""
    run = run_scenario(markets.momentum_gate_blocks())
    checks = {
        (line["entry_breakout"], line["entry_momentum"], line["entry_regime"])
        for line in run.log_events("serve.bar.processed")
        if line["pair"] == "BTC/USDT" and "entry_breakout" in line
    }
    assert (outline(run.db), ("yes", "no", "yes") in checks) == (
        {"signals": [], "trades": []},
        True,
    )
