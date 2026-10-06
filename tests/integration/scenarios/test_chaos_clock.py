"""Chaos: how Hermes fails when the host clock jumps across a bar close (issue #846).

NTP can step the wall clock forward or back by hours on a host that drifted or
booted with a wrong RTC. Serve's local clock jumps across the breakout bar's
close and jumps back twelve hours later, while the fake exchange keeps true
time, as the real one would; monotonic time does not jump
(:class:`~tests.integration.scenarios.faults.SkewedClock`).

Whatever the local clock says, every bar is handled once, none is skipped, and
the run trades and notifies as it does with a steady clock.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from tests.integration.scenarios import markets
from tests.integration.scenarios.faults import entries_sent, recording_channels, skewed_clock
from tests.integration.scenarios.ledger import outline
from tests.support.fake_market import harness
from tests.support.serve_loop import events

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

_STEP = timedelta(hours=5)
"""Longer than one 4h bar, so a jump crosses a whole bar close."""

_JUMPS = {
    "ahead": (-timedelta(hours=1), _STEP, timedelta(hours=11)),
    "behind": (timedelta(hours=1), -_STEP, timedelta(hours=13)),
}
"""Per direction: when the clock jumps (from the breakout close), by how much, when it is fixed."""


@dataclass(frozen=True)
class _Outcome:
    exit_code: int
    btc_4h_handled: list[datetime]
    trades: list[tuple[str, ...]]
    entries_sent: int


@functools.cache
def _scenario() -> markets.Scenario:
    return markets.chandelier_exit()


def _btc_4h_handled(db: Path) -> list[datetime]:
    payloads = [json.loads(row["payload_json"]) for row in events(db, "BarProcessed")]
    return [
        datetime.fromisoformat(p["occurred_at"])
        for p in payloads
        if p["interval"] == "4h" and p["pair"]["base"] == "BTC"
    ]


def _serve(db: Path, jumps: list[tuple[datetime, timedelta]]) -> _Outcome:
    scenario = _scenario()
    with capture_logs(), recording_channels() as channels, skewed_clock(jumps):
        run = harness.serve(scenario.market, db, live_from=scenario.live_from)
    return _Outcome(
        exit_code=run.exit_code,
        btc_4h_handled=_btc_4h_handled(db),
        trades=outline(db)["trades"],
        entries_sent=entries_sent(channels),
    )


@pytest.fixture(scope="module")
def steady(tmp_path_factory: pytest.TempPathFactory) -> _Outcome:
    """Run the scenario once with a clock that never jumps."""
    return _serve(tmp_path_factory.mktemp("steady") / "ledger.sqlite3", [])


@pytest.fixture(scope="module")
def jumped(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], _Outcome]:
    """Return the run whose local clock jumps across the breakout close, then is set right."""
    done: dict[str, _Outcome] = {}

    def outcome(direction: str) -> _Outcome:
        if direction not in done:
            start, step, fixed = _JUMPS[direction]
            close = _scenario().breakout_at
            db = tmp_path_factory.mktemp(f"jump-{direction}") / "ledger.sqlite3"
            done[direction] = _serve(db, [(close + start, step), (close + fixed, -step)])
        return done[direction]

    return outcome


@pytest.mark.parametrize("direction", sorted(_JUMPS))
def test_a_clock_jump_across_a_bar_close_handles_every_bar_exactly_once(
    steady: _Outcome, jumped: Callable[[str], _Outcome], direction: str
) -> None:
    """The BTC 4h bars handled are those of the steady run: none twice, none skipped."""
    assert (jumped(direction).exit_code, jumped(direction).btc_4h_handled) == (
        0,
        steady.btc_4h_handled,
    )


@pytest.mark.parametrize(
    "direction",
    [
        pytest.param(
            "ahead",
            marks=pytest.mark.xfail(
                strict=True,
                raises=AssertionError,
                reason=(
                    "issue #859: the live-book window (5 min) is measured on the local wall "
                    "clock, so a host clock running ahead of the exchange makes every entry "
                    "fall back to the bar-close book, which RO-04 refuses as stale, while the "
                    "entry push still goes out"
                ),
            ),
        ),
        "behind",
    ],
)
def test_a_clock_jump_across_a_bar_close_trades_as_a_steady_clock(
    steady: _Outcome, jumped: Callable[[str], _Outcome], direction: str
) -> None:
    """The breakout still buys once and the gap still sells once."""
    assert jumped(direction).trades == steady.trades


@pytest.mark.parametrize("direction", sorted(_JUMPS))
def test_a_clock_jump_across_a_bar_close_sends_the_entry_push_once(
    jumped: Callable[[str], _Outcome], direction: str
) -> None:
    """The entry reaches Telegram once, not zero times as stale nor twice as new."""
    assert jumped(direction).entries_sent == 1


def test_a_steady_clock_buys_once_and_sells_once(steady: _Outcome) -> None:
    """The comparison run trades the scenario: one BUY at the breakout, one SELL at the gap."""
    assert steady.trades == [("BTC/USDT", "BUY"), ("BTC/USDT", "SELL")]
