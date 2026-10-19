"""Tests for the parity decision comparison and its alert.

Invariants protected here (openspec/archive/358-parity-check/change.md):

- two decision sets with the same keys and outcomes are in parity;
- a signal on one side only, or the same signal executed on one side and not
  on the other, is a mismatch that names the bar, the pair and both decisions;
- a differing decline reason alone is not a mismatch;
- the alert names bars and decisions and carries no price, equity or P&L.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from hermes.application.parity.decisions import (
    Decision,
    Outcome,
    compare,
    parity_alert,
)
from hermes.domain.events.anomaly_detected import AnomalySeverity
from hermes.domain.strategy.signal import SignalKind, SignalSide
from tests.support.fill_kernel import BTC_USDT

_DAY = date(2026, 10, 3)
_AT = datetime(2026, 10, 3, 8, tzinfo=UTC)
_NOW = datetime(2026, 10, 4, 0, 5, tzinfo=UTC)


def _decision(outcome: Outcome, *, reason: str | None = None, at: datetime = _AT) -> Decision:
    return Decision(
        pair=BTC_USDT,
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        signal_time=at,
        outcome=outcome,
        reason=reason,
    )


def test_identical_decisions_are_in_parity_and_counted() -> None:
    """Same key and same outcome on both sides: parity, one signal matched."""
    report = compare(_DAY, [_decision(Outcome.EXECUTED)], [_decision(Outcome.EXECUTED)])

    assert (report.in_parity, report.matched, report.mismatches) == (True, 1, ())


def test_a_day_without_signals_on_both_sides_is_in_parity() -> None:
    """Nothing fired live and nothing fires in the replay: parity with zero signals."""
    report = compare(_DAY, [], [])

    assert (report.in_parity, report.matched) == (True, 0)


def test_a_signal_serve_emitted_but_the_replay_did_not_is_a_mismatch() -> None:
    """Serve-only signal: the line names the bar close, the pair and both decisions."""
    report = compare(_DAY, [_decision(Outcome.EXECUTED)], [])

    assert [m.line() for m in report.mismatches] == [
        "MISMATCH 2026-10-03T08:00:00+00:00 BTC/USDT: serve entry long executed; replay no signal"
    ]
    assert report.matched == 0


def test_a_signal_the_replay_found_but_serve_did_not_is_a_mismatch() -> None:
    """Replay-only signal is the mirror image of the serve-only one."""
    report = compare(_DAY, [], [_decision(Outcome.NOT_EXECUTED, reason="no-book")])

    assert [m.line() for m in report.mismatches] == [
        "MISMATCH 2026-10-03T08:00:00+00:00 BTC/USDT: "
        "serve no signal; replay entry long not executed (no-book)"
    ]


def test_executed_on_one_side_and_declined_on_the_other_is_a_mismatch() -> None:
    """Same signal, different outcome: reported with the replay's decline rule."""
    report = compare(
        _DAY,
        [_decision(Outcome.EXECUTED)],
        [_decision(Outcome.NOT_EXECUTED, reason="no-position")],
    )

    assert [m.line() for m in report.mismatches] == [
        "MISMATCH 2026-10-03T08:00:00+00:00 BTC/USDT: "
        "serve entry long executed; replay entry long not executed (no-position)"
    ]


def test_a_decline_reason_alone_never_makes_matching_outcomes_differ() -> None:
    """Serve does not record reasons, so reason text must not decide parity."""
    report = compare(
        _DAY,
        [_decision(Outcome.NOT_EXECUTED)],
        [_decision(Outcome.NOT_EXECUTED, reason="fill_model")],
    )

    assert (report.in_parity, report.matched) == (True, 1)


def test_mismatches_are_listed_oldest_bar_first() -> None:
    """The report reads in time order whatever order the sides arrived in."""
    later = datetime(2026, 10, 3, 20, tzinfo=UTC)
    report = compare(
        _DAY,
        [_decision(Outcome.EXECUTED, at=later), _decision(Outcome.EXECUTED)],
        [],
    )

    assert [m.bar_close for m in report.mismatches] == [_AT, later]


def test_alert_is_none_when_in_parity() -> None:
    """No mismatch, no alert."""
    report = compare(_DAY, [], [])

    assert (report.in_parity, parity_alert(report, now=_NOW)) == (True, None)


def test_alert_names_the_bar_and_both_decisions_without_money() -> None:
    """The pushed text has the bar, pair and decisions, and no price, equity or P&L word."""
    report = compare(
        _DAY,
        [_decision(Outcome.EXECUTED)],
        [_decision(Outcome.NOT_EXECUTED, reason="no-position")],
    )

    alert = parity_alert(report, now=_NOW)

    assert alert is not None
    assert alert.severity is AnomalySeverity.WARNING
    assert alert.source == "parity"
    assert alert.reason == (
        "backtest/live parity mismatch on 2026-10-03: 2026-10-03T08:00:00+00:00 BTC/USDT: "
        "serve entry long executed, replay entry long not executed (no-position); "
        "run `hermes parity check --date 2026-10-03`"
    )
    for forbidden in ("equity", "P&L", "pnl", "price", "USDT 1", "EUR"):
        assert forbidden.lower() not in alert.reason.lower().replace("btc/usdt", "")


def test_alert_caps_the_listed_bars_and_counts_the_rest() -> None:
    """Five mismatches list three bars and say '2 more'."""
    served = [
        _decision(Outcome.EXECUTED, at=datetime(2026, 10, 3, hour, tzinfo=UTC))
        for hour in (4, 8, 12, 16, 20)
    ]

    alert = parity_alert(compare(_DAY, served, []), now=_NOW)

    assert alert is not None
    assert alert.reason.count("serve entry long executed") == 3
    assert "; 2 more;" in alert.reason
