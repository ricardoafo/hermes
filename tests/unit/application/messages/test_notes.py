"""Tests for the channel-neutral notes every template is built from.

Invariants protected here:

- each event states what happened and to what on the first line, then the
  facts the operator acts on, in the formats of ``format.py``;
- a signal carries its stop, and the actual half of plan-versus-actual reads
  "withheld until certification";
- no note, in any shape, names a profit, a loss or a return before
  certification (#319), and the digest's outcome counts need CERTIFIED;
- an event with no builder raises instead of sending an empty push.

Reference: openspec/changes/144-notification-system/change.md (R-1, R-4).
"""

# ruff: noqa: RUF001 - the typographic minus sign is the character under test

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest

from hermes.application.messages.notes import ACTUAL_WITHHELD, build_note
from hermes.application.messages.reporting_phase import (
    OUTCOME_FIELD_NAMES,
    OUTCOME_WITHHELD_NOTICE,
    ReportingPhase,
)
from hermes.application.messages.shapes import Family, Shape, render_lines
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.signal_emitted import SignalEmitted
from tests.support.notification_samples import NOON_SATURDAY_UTC, samples

_BLIND = ReportingPhase.PRE_CERTIFICATION
_PNL_WORDS = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(OUTCOME_FIELD_NAMES)) + r"|profit|loss)\b",
    re.IGNORECASE,
)


def _note(name: str, shape: Shape, phase: ReportingPhase = _BLIND) -> tuple[str, ...]:
    _family, note = build_note(samples()[name], phase)
    return render_lines(note, shape, "…REF")


def _signal(name: str) -> SignalEmitted:
    event = samples()[name]
    assert isinstance(event, SignalEmitted)
    return event


def test_entry_compact_leads_with_kind_coin_and_price_then_the_stop() -> None:
    """A compact entry answers what and to what first, then gives the stop with its distance."""
    assert _note("signal-entry", Shape.COMPACT) == (
        "▲ Entry long BTC · 52,410.20 USDT",
        "stop 50,516.00 USDT (−3.6%) · Sat 14:00",
        "swing-donchian-4h · …REF",
    )


def test_entry_verbose_shows_the_plan_and_withholds_the_actual() -> None:
    """A verbose entry spells the plan out with UTC and ATR and withholds the actual."""
    assert _note("signal-entry", Shape.VERBOSE) == (
        "▲ Entry long BTC · 52,410.20 USDT · Sat 14:00 (12:00 UTC)",
        "Plan: stop 50,516.00 USDT (−3.6%, 1.8 ATR)",
        ACTUAL_WITHHELD,
        "swing-donchian-4h · …REF",
    )


def test_entry_minimal_is_one_line_with_the_stop() -> None:
    """A minimal entry is one line that still carries the stop."""
    assert _note("signal-entry", Shape.MINIMAL) == (
        "▲ Long BTC 52,410.20 USDT stop 50,516.00 USDT (Sat 14:00) · …REF",
    )


def test_exit_names_the_rule_and_the_reference_entry() -> None:
    """An exit shows the rule that fired, the entry reference and the stop level."""
    assert _note("signal-exit", Shape.COMPACT) == (
        "▼ Exit long BTC (trailing_stop)",
        "entry ref 52,410.20 USDT · stop 54,100.00 USDT · Sat 14:00",
        "swing-donchian-4h · …REF",
    )


def test_exit_verbose_withholds_the_actual() -> None:
    """An exit is plan-versus-actual too, so its actual half is withheld."""
    assert _note("signal-exit", Shape.VERBOSE)[2] == ACTUAL_WITHHELD


def test_entry_with_no_atr_omits_the_atr_multiple_only() -> None:
    """With a zero ATR the plan still shows the stop and its distance, without an ATR figure."""
    event = dataclasses.replace(_signal("signal-entry"), atr_context=Decimal(0))

    _family, note = build_note(event, _BLIND)

    assert note.verbose[1] == "Plan: stop 50,516.00 USDT (−3.6%)"


def test_entry_with_no_price_says_so_and_omits_the_distance() -> None:
    """A signal with no entry price shows 'price n/a' and no stop distance or ATR."""
    event = dataclasses.replace(_signal("signal-entry"), entry_price=None)

    _family, note = build_note(event, _BLIND)

    assert (note.compact[0], note.compact[1], note.verbose[1]) == (
        "▲ Entry long BTC · price n/a",
        "stop 50,516.00 USDT · Sat 14:00",
        "Plan: stop 50,516.00 USDT",
    )


def test_entry_with_a_zero_price_omits_the_distance_instead_of_dividing() -> None:
    """A zero entry price cannot give a percentage, so the distance is left out."""
    event = dataclasses.replace(_signal("signal-entry"), entry_price=Decimal(0))

    _family, note = build_note(event, _BLIND)

    assert note.compact[1] == "stop 50,516.00 USDT · Sat 14:00"


def test_exit_with_no_reason_has_no_empty_brackets() -> None:
    """An exit without a reason does not print empty parentheses."""
    event = dataclasses.replace(_signal("signal-exit"), exit_reason=None)

    _family, note = build_note(event, _BLIND)

    assert note.compact[0] == "▼ Exit long BTC"


def test_fill_shows_side_coin_price_quantity_and_fee() -> None:
    """A large fill names the side and coin, then the quantity, the fee and the time."""
    assert _note("position-large", Shape.COMPACT) == (
        "▲ Position large · BUY BTC · 52,410.20 USDT",
        "0.0040 BTC · fee 0.21 USDT · Sat 14:00",
        "swing-donchian-4h · …REF",
    )


def test_fill_verbose_adds_the_value() -> None:
    """The verbose fill adds the traded value and UTC time."""
    assert _note("position-large", Shape.VERBOSE)[:2] == (
        "▲ Position large · BUY BTC · 52,410.20 USDT · Sat 14:00 (12:00 UTC)",
        "0.0040 BTC · value 209.64 USDT · fee 0.21 USDT",
    )


def test_rejection_names_the_exchange_and_the_reason() -> None:
    """A rejected order leads with the stop sign and gives the exchange and why."""
    assert _note("trade-rejected", Shape.COMPACT) == (
        "■ Order rejected · BUY BTC",
        "binance: insufficient balance · Sat 14:00",
        "…REF",
    )


def test_pause_names_the_strategy_reason_and_next_step() -> None:
    """A pause shows the strategy, the reason and the hint to review it."""
    assert _note("strategy-paused-critical", Shape.COMPACT) == (
        "■ Strategy paused · swing-donchian-4h · Sat 14:00",
        "operator kill switch",
        "review the strategy · …REF",
    )


def test_pause_glyph_follows_severity() -> None:
    """A warning pause opens with the half circle instead of the stop sign."""
    assert _note("strategy-paused-warning", Shape.COMPACT)[0].startswith("◐ ")


def test_resume_mirrors_the_pause_with_the_duration_in_words() -> None:
    """The resume says the strategy is back and for how long it was paused, in words."""
    assert _note("strategy-resumed", Shape.COMPACT) == (
        "● Strategy resumed · swing-donchian-4h · Sat 14:00",
        "back after 15 min",
        "…REF",
    )


def test_anomaly_names_its_source_and_reason() -> None:
    """An anomaly shows the source, the time and the reason."""
    assert _note("anomaly-critical", Shape.COMPACT) == (
        "■ Anomaly · reconciler · Sat 14:00",
        "ledger and exchange balances disagree",
        "…REF",
    )


def test_info_anomaly_uses_the_filled_circle() -> None:
    """An informational anomaly opens with the filled circle."""
    assert _note("anomaly-info", Shape.COMPACT)[0].startswith("● ")


def test_low_balance_shows_what_is_left_and_the_threshold_with_a_hint() -> None:
    """A low balance gives the amount left, the threshold and the top-up hint."""
    assert _note("balance-low", Shape.COMPACT) == (
        "■ Balance low · USDT · Sat 14:00",
        "50.00 USDT left, threshold 100.00 USDT",
        "top up · …REF",
    )


def test_rebalance_heads_up_says_no_trade_yet_and_shows_drift_in_points() -> None:
    """The rebalance heads-up shows weights, drift in points and that no trade has run."""
    assert _note("rebalance-triggered", Shape.COMPACT) == (
        "◐ Rebalance heads-up · BTC · Sat 14:00",
        "35.0% vs target 25.0% · drift 10.0 pp (band 5.0 pp)",
        "no trade yet · …REF",
    )


def test_rebalance_minimal_is_the_one_line_pointer() -> None:
    """The minimal rebalance is a single line with the drift and the ref."""
    assert _note("rebalance-triggered", Shape.MINIMAL) == (
        "◐ Rebalance heads-up BTC: drift 10.0 pp (band 5.0 pp) · …REF",
    )


def test_blind_digest_gives_activity_and_the_withheld_notice() -> None:
    """Before certification the digest keeps the activity counts and says outcomes are withheld."""
    assert _note("daily-digest", Shape.COMPACT) == (
        "● Daily digest · 2026-10-03",
        "10/12 filled · 2 rejected · €12,500.50 traded",
        OUTCOME_WITHHELD_NOTICE,
        "…REF",
    )


def test_certified_digest_adds_the_winner_and_loser_counts() -> None:
    """Only the CERTIFIED phase adds the winners and losers counts and drops the notice."""
    lines = _note("daily-digest", Shape.COMPACT, ReportingPhase.CERTIFIED)

    assert lines[2] == "Top winners 1 · top losers 1 (CSV attached)"


def test_digest_verbose_lists_trades_and_notional() -> None:
    """The verbose digest spells out trades, fills, rejections and the traded amount."""
    assert _note("daily-digest", Shape.VERBOSE)[1:3] == (
        "Trades 12: filled 10, rejected 2",
        "Traded: €12,500.50",
    )


@pytest.mark.parametrize("shape", list(Shape))
@pytest.mark.parametrize("name", sorted(samples()))
def test_no_blind_note_contains_a_pnl_word(name: str, shape: Shape) -> None:
    """Before certification no shape of any event names profit, loss, return or an outcome field."""
    leaked = _PNL_WORDS.findall(" ".join(_note(name, shape)))

    assert leaked == []


def test_families_split_trade_health_and_reports() -> None:
    """Signals and fills are trade lifecycle, pauses are health, the digest is a report."""
    events = samples()
    families = {
        name: build_note(events[name], _BLIND)[0]
        for name in ("signal-entry", "strategy-paused-critical", "daily-digest")
    }

    assert families == {
        "signal-entry": Family.TRADE_LIFECYCLE,
        "strategy-paused-critical": Family.HEALTH_OPS,
        "daily-digest": Family.REPORTS,
    }


def test_event_without_a_builder_raises_naming_the_class() -> None:
    """An event type with no builder raises ValueError instead of rendering an empty push."""

    @dataclass(frozen=True, slots=True)
    class _CustomEvent(DomainEvent):
        __event_schema_version__: ClassVar[int] = 1

    with pytest.raises(ValueError, match="_CustomEvent"):
        build_note(_CustomEvent(occurred_at=NOON_SATURDAY_UTC, correlation_id="X"), _BLIND)


def test_digest_is_the_only_phase_aware_builder() -> None:
    """Every sample except the digest renders the same in both phases."""
    differing = sorted(
        name
        for name, event in samples().items()
        if build_note(event, _BLIND) != build_note(event, ReportingPhase.CERTIFIED)
    )

    assert differing == ["daily-digest"]
