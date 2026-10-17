"""Tests for the supervisor desk's daily line and weekly brief (#365, #496).

Invariants protected here:

- a brief reads only through the dispatcher: an ungranted tool is reported as
  unavailable, never read, and every read leaves an audit record;
- every item cites its source, and the rendered brief passes the output gate
  (ADR-012 D-08), so every number and reference exists in the input;
- no entry price reaches a brief for any price (ADR-012 D-06);
- strategy decisions show as sealed, without their titles;
- a brief whose free text fails the gate falls back to the digest of states
  and counts, which passes;
- items no tool serves yet say so rather than guessing;
- "What the engine did" states, per pair, the checks, signals, exit rules,
  fills and holding the engine recorded, and explains a quiet engine against
  the rules only.

The fake reader stands in for ``SqliteSupervisorReader``; the CLI test in
``tests/unit/entrypoints/test_supervisor.py`` covers the seam over a real
ledger file.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from hermes.application.status import UnitState
from hermes.application.supervisor.briefing import (
    ENGINE_RULES,
    NOT_ON_CONNECTION,
    SOURCES,
    BriefKind,
    compose,
    gather,
    publishable,
)
from hermes.application.supervisor.tools import (
    TOOLS,
    AuditRecord,
    SupervisorContext,
    ToolDispatcher,
)
from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.domain.events.risk_halt_recorded import RiskHaltRecorded
from hermes.domain.journal.entries import Decision, OutcomeKind, Review
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.ports.status import ShadowEntry
from hermes.ports.supervisor import BarCount, FillFact, SignalFact
from tests.support.status_ledger import BTC, ETH, INTERVALS, LAST_4H, NOW, PAIRS

_ALL = [t.scope for t in TOOLS.values()]


class _Reader:
    """In-memory SupervisorReadPort: every 4h stream current, 1d never seen."""

    def __init__(self, facts: tuple[SignalFact, ...], entries: tuple[ShadowEntry, ...]) -> None:
        self.facts = facts
        self.entries = entries

    def last_processed_close(self, pair: Pair, interval: KlineInterval) -> datetime | None:
        return LAST_4H - timedelta(milliseconds=1) if interval is KlineInterval.H4 else None

    def count_bars_since(self, interval: KlineInterval, since: datetime) -> int:
        return 0

    def count_signals_since(self, since: datetime) -> int:
        return sum(1 for f in self.facts if f.occurred_at >= since)

    def open_entries(self) -> tuple[ShadowEntry, ...]:
        return self.entries

    def halts_since(self, since: datetime) -> tuple[RiskHaltRecorded, ...]:
        return ()

    def last_clock_skew(self) -> AnomalyDetected | None:
        return None

    def signals_since(self, since: datetime) -> tuple[SignalFact, ...]:
        return tuple(f for f in self.facts if f.occurred_at >= since)

    def bars_since(self, since: datetime) -> tuple[BarCount, ...]:
        return _BARS

    def fills_since(self, since: datetime) -> tuple[FillFact, ...]:
        return tuple(f for f in _FILLS if f.occurred_at >= since)


class _Journal:
    def __init__(self, entries: list[Decision | Review]) -> None:
        self._entries = entries

    def append(self, entry: Decision | Review) -> None:
        self._entries.append(entry)

    def entries(self) -> list[Decision | Review]:
        return list(self._entries)


def _decision(entry_id: str, title: str, kind: OutcomeKind) -> Decision:
    return Decision(
        entry_id=entry_id,
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC),
        decision=title,
        options=("a", "b"),
        chosen="a",
        reasoning="because",
        expected_outcome="it works",
        review_on=date(2026, 10, 1),
        outcome_kind=kind,
    )


_ENTRY_TIME = LAST_4H - timedelta(days=2)
_SIGNALS = (
    SignalFact(ETH, "entry", _ENTRY_TIME),
    SignalFact(BTC, "exit", LAST_4H - timedelta(hours=8), exit_reason="chandelier"),
)
_BARS = (
    BarCount(BTC, KlineInterval.H4, 6),
    BarCount(ETH, KlineInterval.H4, 6),
    BarCount(ETH, KlineInterval.D1, 1),
)
_FILLS = (FillFact(BTC, "SELL", LAST_4H - timedelta(hours=8)),)
_DECISIONS: list[Decision | Review] = [
    _decision("DJ-1", "Keep the runner on Windows", OutcomeKind.PROCESS),
    _decision("DJ-2", "Widen the stop", OutcomeKind.STRATEGY),
]


def _dispatcher(
    grants: list[str],
    *,
    price: Decimal = Decimal("2618.45"),
    journal: list[Decision | Review] | None = None,
) -> tuple[ToolDispatcher, list[AuditRecord]]:
    records: list[AuditRecord] = []
    reader = _Reader(_SIGNALS, (ShadowEntry(ETH, _ENTRY_TIME, price),))

    @contextmanager
    def open_ctx() -> Iterator[SupervisorContext]:
        yield SupervisorContext(
            reader=reader,
            pairs=PAIRS,
            intervals=INTERVALS,
            settle=timedelta(seconds=60),
            grace=timedelta(minutes=15),
            unit=UnitState.RUNNING,
            now=NOW,
            journal=_Journal(list(_DECISIONS if journal is None else journal)),
            certification=lambda _sid: None,
            strategy_id="swing-donchian-4h",
        )

    dispatcher = ToolDispatcher(
        grants=grants, context=open_ctx, audit=records.append, clock=lambda: NOW
    )
    return dispatcher, records


def test_the_weekly_brief_cites_every_source_and_passes_the_gate() -> None:
    """Each tool-backed item cites its tool, and the rendered brief passes the gate."""
    dispatcher, _ = _dispatcher(_ALL)
    brief = compose(gather(dispatcher, kind=BriefKind.WEEKLY, now=NOW), kind=BriefKind.WEEKLY)

    cited = {s for item in brief.items for s in item.sources}

    assert set(SOURCES) <= cited
    assert brief.gate().reasons == ()


def test_the_weekly_brief_states_the_counts_code_computed() -> None:
    """Signals and positions are counted by code and stated exactly."""
    dispatcher, _ = _dispatcher(_ALL)
    text = compose(
        gather(dispatcher, kind=BriefKind.WEEKLY, now=NOW), kind=BriefKind.WEEKLY
    ).render()

    assert "- Signals: 2 since 2026-09-26 16:10 UTC: entries 1, exits 1." in text
    assert "- Open positions: ETH/USDT since 2026-10-01 16:00 UTC." in text


def test_strategy_decisions_are_sealed_without_their_titles() -> None:
    """A strategy call shows as sealed by count, never by title (#363 D-1)."""
    dispatcher, _ = _dispatcher(_ALL)
    text = compose(
        gather(dispatcher, kind=BriefKind.WEEKLY, now=NOW), kind=BriefKind.WEEKLY
    ).render()

    assert "DJ-1 Keep the runner on Windows (review from 2026-10-01)" in text
    assert "1 strategy call(s) sealed until certification" in text
    assert "Widen the stop" not in text


def test_items_no_tool_serves_say_so() -> None:
    """Fill drift and kill triggers say they are not on the data connection yet."""
    dispatcher, _ = _dispatcher(_ALL)
    brief = compose(gather(dispatcher, kind=BriefKind.WEEKLY, now=NOW), kind=BriefKind.WEEKLY)

    texts = {item.heading: item.text for item in brief.items}

    assert texts["Fill drift"] == texts["Kill triggers"] == NOT_ON_CONNECTION


def test_an_ungranted_tool_is_reported_unavailable_and_audited_as_denied() -> None:
    """Without a grant the brief says so; the refused read is still audited."""
    dispatcher, records = _dispatcher(["hermes:read:health"])
    brief = compose(gather(dispatcher, kind=BriefKind.WEEKLY, now=NOW), kind=BriefKind.WEEKLY)

    texts = {item.heading: item.text for item in brief.items}

    assert texts["Signals"] == "Unavailable (not granted)."
    assert [(r.tool, r.outcome.value) for r in records] == [
        ("health", "ok"),
        ("signals", "denied"),
        ("engine", "denied"),
        ("positions", "denied"),
        ("decisions", "denied"),
        ("certification", "denied"),
    ]
    assert brief.gate().passed


def test_the_daily_brief_is_the_day_line_then_the_engine_and_passes_the_gate() -> None:
    """The daily brief is the line over the last day plus what the engine did (#365)."""
    dispatcher, _ = _dispatcher(_ALL)
    brief = compose(gather(dispatcher, kind=BriefKind.DAILY, now=NOW), kind=BriefKind.DAILY)

    assert [item.heading for item in brief.items] == ["Today", "What the engine did"]
    assert brief.items[0].text.endswith(
        "Since 2026-10-02 16:10 UTC: 1 signal(s), 1 open position(s), 1 decision(s) waiting."
    )
    assert brief.gate().passed


@given(st.decimals(min_value=Decimal("1000.01"), max_value=Decimal("99999.99"), places=2))
def test_no_entry_price_reaches_a_brief(price: Decimal) -> None:
    """For any entry price, neither brief contains it (ADR-012 D-06)."""
    dispatcher, _ = _dispatcher(_ALL, price=price)

    for kind in BriefKind:
        text = compose(gather(dispatcher, kind=kind, now=NOW), kind=kind).render()
        assert str(price) not in text


def test_a_title_that_fails_the_gate_falls_back_to_the_digest() -> None:
    """Free text that breaks a gate rule is held back; the digest goes out."""
    journal: list[Decision | Review] = [
        _decision("DJ-9", "Post https://example.org", OutcomeKind.PROCESS)
    ]
    dispatcher, _ = _dispatcher(_ALL, journal=journal)
    inputs = gather(dispatcher, kind=BriefKind.WEEKLY, now=NOW)

    brief, verdict = publishable(inputs, kind=BriefKind.WEEKLY)

    assert "contains a link" in verdict.reasons
    assert "example" not in brief.render()
    assert "DJ-9 (review from 2026-10-01)" in brief.render()
    assert brief.gate().passed


def test_a_passing_brief_is_published_in_full() -> None:
    """When the full brief passes, it is the one returned."""
    dispatcher, _ = _dispatcher(_ALL)
    inputs = gather(dispatcher, kind=BriefKind.WEEKLY, now=NOW)

    brief, verdict = publishable(inputs, kind=BriefKind.WEEKLY)

    assert verdict.passed
    assert "Keep the runner on Windows" in brief.render()


def test_what_the_engine_did_states_each_pair_from_the_engine_tool() -> None:
    """Each pair's checks, signals, exit rule, fills and holding come from the tool's facts."""
    dispatcher, _ = _dispatcher(_ALL)
    brief = compose(gather(dispatcher, kind=BriefKind.WEEKLY, now=NOW), kind=BriefKind.WEEKLY)

    item = {i.heading: i for i in brief.items}["What the engine did"]

    assert item.sources == ("engine",)
    assert item.text == " ".join(
        (
            "BTC/USDT: 6 4h and 0 1d bars checked; exit signal(s) by rule: chandelier; "
            "fills: 0 buy, 1 sell; flat.",
            "ETH/USDT: 6 4h and 1 1d bars checked; 1 entry signal(s); no fills; "
            "holding since 2026-10-01 16:00 UTC.",
            "SOL/USDT: 0 4h and 0 1d bars checked; no signal; no fills; flat.",
            ENGINE_RULES,
        )
    )
    assert brief.gate().passed


def test_a_quiet_engine_is_explained_against_the_rules_only() -> None:
    """The explanation names the frozen rules and says what is not served yet; no number."""
    assert "breakout, momentum, regime" in ENGINE_RULES
    assert ENGINE_RULES.endswith("not on the data connection yet.")
    assert not any(ch.isdigit() for ch in ENGINE_RULES)


def test_what_the_engine_did_is_unavailable_without_its_grant() -> None:
    """Without the engine grant the section says so instead of guessing."""
    grants = [scope for scope in _ALL if scope != "hermes:read:engine"]
    dispatcher, _ = _dispatcher(grants)
    brief = compose(gather(dispatcher, kind=BriefKind.DAILY, now=NOW), kind=BriefKind.DAILY)

    assert brief.items[-1].text == "Unavailable (not granted)."
    assert brief.gate().passed
