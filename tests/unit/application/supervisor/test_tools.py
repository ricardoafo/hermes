"""Tests for the outcome-blind supervisor tools and their dispatcher (#34, #470).

Invariants protected here:

- blindness (ADR-012 D-06): every projection field is on a reviewed allow
  list and none names an outcome; no price reaches any tool's output;
- read-only scopes (ADR-003 §08, ADR-012 D-08): every scope is
  ``hermes:read:<name>``, no tool name reads as a write, and no trade or
  write scope string exists anywhere in the source tree;
- deny-by-default: nothing is served without a grant, an ungranted or
  unknown tool is refused, and an unknown grant is rejected;
- the memorization guard refuses history before 2026-02-01, or before a
  stricter configured guard;
- strategy decisions stay sealed until certification (#363 D-1);
- every call is audited, and a read failure reports only its error class.

The fake reader stands in for ``SqliteSupervisorReader``; the seam is covered
by ``tests/unit/adapters/ledger/test_supervisor_reader.py`` and the CLI tests
in ``tests/unit/entrypoints/test_supervisor.py``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import types
import typing
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.status import UnitState
from hermes.application.supervisor.tools import (
    MEMORIZATION_GUARD,
    TOOLS,
    AuditRecord,
    CallOutcome,
    MemorizationGuardError,
    SupervisorContext,
    ToolDispatcher,
    UnknownToolError,
    engine,
    signals,
)
from hermes.domain.events.risk_halt_recorded import RiskHaltRecorded
from hermes.domain.journal.entries import Decision, OutcomeKind, Review
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.ports.status import ShadowEntry
from hermes.ports.supervisor import (
    BarCount,
    CertificationFact,
    FillFact,
    SignalFact,
    SupervisorReadPort,
)
from tests.support.status_ledger import BTC, ETH, INTERVALS, LAST_4H, NOW, PAIRS, SOL

_SRC = Path(__file__).resolve().parents[4] / "src" / "hermes"
_ALL_SCOPES = [t.scope for t in TOOLS.values()]

ALLOWED_FIELDS = {
    "HealthView": {
        "verdict",
        "headline",
        "reason",
        "next_step",
        "streams",
        "signals_24h",
        "signals_7d",
    },
    "StreamView": {"pair", "interval", "last_close", "next_close", "state"},
    "SignalsView": {"since", "signals"},
    "SignalView": {"pair", "kind", "occurred_at"},
    "PositionsView": {"positions"},
    "PositionView": {"pair", "entry_time"},
    "DecisionsView": {"reviewable", "sealed"},
    "DueDecisionView": {"entry_id", "decision", "review_on"},
    "SealedDecisionView": {"entry_id", "review_on"},
    "CertificationView": {"strategy_id", "state", "entered_at"},
    "EngineView": {"since", "pairs"},
    "PairActivityView": {
        "pair",
        "bars",
        "entries",
        "exit_reasons",
        "fills_buy",
        "fills_sell",
        "holding_since",
    },
    "BarCountView": {"interval", "count"},
}
"""The reviewed allow list. A new projection field must be added here on purpose."""

_OUTCOME_WORDS = re.compile(
    r"pnl|profit|loss|equity|price|value|sharpe|drawdown|win|return|balance|"
    r"size|qty|quantity|notional|cost|fee|gain|amount",
    re.IGNORECASE,
)


class _FakeReader:
    """In-memory SupervisorReadPort with fixed facts."""

    def __init__(
        self,
        facts: tuple[SignalFact, ...] = (),
        entries: tuple[ShadowEntry, ...] = (),
        bars: tuple[BarCount, ...] = (),
        fills: tuple[FillFact, ...] = (),
    ) -> None:
        self.facts = facts
        self.entries = entries
        self.bars = bars
        self.fills = fills

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

    def signals_since(self, since: datetime) -> tuple[SignalFact, ...]:
        return tuple(f for f in self.facts if f.occurred_at >= since)

    def bars_since(self, since: datetime) -> tuple[BarCount, ...]:
        return self.bars

    def fills_since(self, since: datetime) -> tuple[FillFact, ...]:
        return tuple(f for f in self.fills if f.occurred_at >= since)


class _FakeJournal:
    """In-memory DecisionJournalPort."""

    def __init__(self, entries: list[Decision | Review]) -> None:
        self._entries = entries

    def append(self, entry: Decision | Review) -> None:
        self._entries.append(entry)

    def entries(self) -> list[Decision | Review]:
        return list(self._entries)


def _ctx(
    reader: SupervisorReadPort | None = None,
    *,
    journal: _FakeJournal | None = None,
    state: str | None = None,
    guard: datetime = MEMORIZATION_GUARD,
) -> SupervisorContext:
    fact = None if state is None else CertificationFact(state=state, entered_at=NOW)
    return SupervisorContext(
        reader=reader or _FakeReader(),
        pairs=PAIRS,
        intervals=INTERVALS,
        settle=timedelta(seconds=60),
        grace=timedelta(minutes=15),
        unit=UnitState.RUNNING,
        now=NOW,
        journal=journal,
        certification=lambda _sid: fact,
        strategy_id="swing-donchian-4h",
        guard=guard,
    )


def _dispatcher(
    grants: list[str], ctx: SupervisorContext | None = None
) -> tuple[ToolDispatcher, list[AuditRecord]]:
    records: list[AuditRecord] = []

    @contextmanager
    def open_ctx() -> Iterator[SupervisorContext]:
        yield ctx or _ctx()

    return (
        ToolDispatcher(grants=grants, context=open_ctx, audit=records.append, clock=lambda: NOW),
        records,
    )


def _projection_types() -> dict[str, type]:
    found: dict[str, type] = {}
    pending = [t.output for t in TOOLS.values()]
    while pending:
        cls = pending.pop()
        if cls.__name__ in found:
            continue
        found[cls.__name__] = cls
        for hint in typing.get_type_hints(cls).values():
            for arg in (*typing.get_args(hint), hint):
                if isinstance(arg, type) and dataclasses.is_dataclass(arg):
                    pending.append(arg)
                if isinstance(arg, types.GenericAlias):
                    pending.extend(
                        a
                        for a in typing.get_args(arg)
                        if isinstance(a, type) and dataclasses.is_dataclass(a)
                    )
    return found


def test_every_projection_field_is_on_the_allow_list() -> None:
    """A new field on any tool output fails here until it is reviewed into the list."""
    actual = {
        name: sorted(f.name for f in dataclasses.fields(cls))
        for name, cls in _projection_types().items()
    }
    assert actual == {name: sorted(fields) for name, fields in ALLOWED_FIELDS.items()}


def test_no_projection_field_names_an_outcome() -> None:
    """No allow-listed field may be a P&L, price, value or performance figure."""
    offenders = [
        f"{cls}.{field}"
        for cls, fields in ALLOWED_FIELDS.items()
        for field in fields
        if _OUTCOME_WORDS.search(field)
    ]
    assert offenders == []


def test_every_scope_is_a_single_read_scope() -> None:
    """Each tool has its own hermes:read:<name> scope; nothing else exists."""
    assert all(re.fullmatch(r"hermes:read:[a-z_]+", s) for s in _ALL_SCOPES)
    assert len(set(_ALL_SCOPES)) == len(_ALL_SCOPES)


def test_no_tool_name_reads_as_a_write() -> None:
    """No order-placing or state-changing tool exists (ADR-003 §08)."""
    write_words = re.compile(r"place|cancel|modify|order|trade|pause|resume|set|write|delete")
    assert [n for n in TOOLS if write_words.search(n)] == []


def test_no_trade_or_write_scope_exists_in_the_source_tree() -> None:
    """``hermes:trade:*`` and ``hermes:write:*`` never appear in src (ADR-012 D-08)."""
    pattern = re.compile(r"hermes:(trade|write|admin):|hermes:read:\*")
    hits = [
        str(p.relative_to(_SRC))
        for p in _SRC.rglob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert hits == []


def test_nothing_is_served_without_a_grant() -> None:
    """Deny-by-default: a dispatcher with no grants lists no tools."""
    dispatcher, _ = _dispatcher([])
    assert dispatcher.available() == ()


def test_an_ungranted_tool_is_refused_and_audited() -> None:
    """Calling a tool whose scope was not granted raises and leaves a DENIED record."""
    dispatcher, records = _dispatcher(["hermes:read:health"])

    with pytest.raises(UnknownToolError):
        dispatcher.call("positions", {})
    assert [(r.tool, r.outcome) for r in records] == [("positions", CallOutcome.DENIED)]


def test_an_unknown_grant_is_rejected() -> None:
    """A wildcard or misspelt scope is an error, never a silent broad grant."""
    with pytest.raises(ValueError, match=r"hermes:read:\*"):
        _dispatcher(["hermes:read:*"])


def test_only_granted_tools_are_listed() -> None:
    """The listed tools are exactly those whose scopes were granted."""
    dispatcher, _ = _dispatcher(["hermes:read:signals", "hermes:read:health"])
    assert [t.name for t in dispatcher.available()] == ["health", "signals"]


def test_signals_before_the_guard_are_refused() -> None:
    """A since before 2026-02-01 is a memorization-guard refusal, audited as such."""
    dispatcher, records = _dispatcher(["hermes:read:signals"])

    result = dispatcher.call("signals", {"since": "2026-01-31T23:59:59+00:00"})

    assert result.payload is None
    assert "memorization guard" in (result.error or "")
    assert records[-1].outcome is CallOutcome.GUARD


def test_signals_at_the_guard_are_allowed() -> None:
    """The guard is inclusive: since exactly 2026-02-01T00:00Z is served."""
    view = signals(_ctx(), {"since": MEMORIZATION_GUARD.isoformat()})
    assert view.since == MEMORIZATION_GUARD


def test_a_stricter_configured_guard_is_enforced() -> None:
    """A guard set after the floor (model cutoff plus a month) refuses earlier history."""
    guard = datetime(2026, 7, 1, tzinfo=UTC)

    with pytest.raises(MemorizationGuardError):
        signals(_ctx(guard=guard), {"since": "2026-06-30T00:00:00+00:00"})


def test_a_guard_before_the_floor_is_rejected() -> None:
    """No configuration can lower the guard below 2026-02-01."""
    with pytest.raises(ValueError, match="floor"):
        _ctx(guard=datetime(2025, 12, 1, tzinfo=UTC))


@pytest.mark.parametrize(
    "args",
    [{}, {"since": "yesterday"}, {"since": "2026-03-01T00:00:00"}, {"since": 5}],
)
def test_bad_since_is_a_bad_arguments_refusal(args: dict[str, object]) -> None:
    """Missing, unparsable, naive or non-string since is refused as bad arguments."""
    dispatcher, records = _dispatcher(["hermes:read:signals"])

    result = dispatcher.call("signals", args)

    assert result.payload is None
    assert records[-1].outcome is CallOutcome.BAD_ARGUMENTS


def test_an_unknown_argument_is_refused() -> None:
    """Arguments outside the tool's schema are refused, not ignored."""
    dispatcher, _ = _dispatcher(["hermes:read:health"])

    result = dispatcher.call("health", {"include_pnl": True})

    assert result.error == "unknown argument(s): include_pnl"


def test_a_read_failure_reports_only_its_error_class() -> None:
    """An exception message (which could carry raw text) never reaches the client."""

    class _Broken(_FakeReader):
        def open_entries(self) -> tuple[ShadowEntry, ...]:
            raise RuntimeError("secret path /etc/hermes/env")

    dispatcher, records = _dispatcher(["hermes:read:positions"], _ctx(_Broken()))

    result = dispatcher.call("positions", {})

    assert result.error == "RuntimeError"
    assert records[-1].outcome is CallOutcome.ERROR


def test_a_successful_call_is_audited_with_hashes() -> None:
    """The audit record names the tool and scope and hashes the output it returned."""
    dispatcher, records = _dispatcher(["hermes:read:positions"])

    result = dispatcher.call("positions", {})

    record = records[-1]
    expected = json.dumps(result.payload, sort_keys=True, separators=(",", ":"))
    assert (record.tool, record.scope, record.outcome) == (
        "positions",
        "hermes:read:positions",
        CallOutcome.OK,
    )
    assert record.output_sha256 == hashlib.sha256(expected.encode()).hexdigest()


def test_positions_show_pair_and_entry_time_only() -> None:
    """An open position's entry price never reaches the projection."""
    reader = _FakeReader(entries=(ShadowEntry(ETH, LAST_4H, Decimal("2618.45")),))
    dispatcher, _ = _dispatcher(["hermes:read:positions"], _ctx(reader))

    result = dispatcher.call("positions", {})

    assert result.payload == {"positions": [{"pair": str(ETH), "entry_time": LAST_4H.isoformat()}]}


@given(
    price=st.decimals(min_value=Decimal("1000.0001"), max_value=Decimal("99999.9999"), places=4),
    hours=st.integers(min_value=0, max_value=24 * 200),
)
def test_no_price_reaches_any_output(price: Decimal, hours: int) -> None:
    """Whatever the entry price, it appears nowhere in the positions or signals JSON."""
    at = MEMORIZATION_GUARD + timedelta(hours=hours)
    reader = _FakeReader(
        facts=(SignalFact(BTC, "entry", at),),
        entries=(ShadowEntry(BTC, at, price),),
        fills=(FillFact(BTC, "BUY", at),),
    )
    grants = [
        "hermes:read:positions",
        "hermes:read:signals",
        "hermes:read:health",
        "hermes:read:engine",
    ]
    dispatcher, _ = _dispatcher(grants, _ctx(reader))
    since = {"since": MEMORIZATION_GUARD.isoformat()}

    outputs = [
        dispatcher.call("positions", {}).payload,
        dispatcher.call("signals", since).payload,
        dispatcher.call("health", {}).payload,
        dispatcher.call("engine", since).payload,
    ]

    assert str(price) not in json.dumps(outputs)


def _decision(entry_id: str, kind: OutcomeKind, title: str) -> Decision:
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


def test_strategy_decisions_are_sealed_without_their_title() -> None:
    """Before certification a strategy call shows only its id and date."""
    journal = _FakeJournal(
        [
            _decision("p1", OutcomeKind.PROCESS, "Slim the hook"),
            _decision("s1", OutcomeKind.STRATEGY, "Expect ETH entries to win"),
        ]
    )
    dispatcher, _ = _dispatcher(
        ["hermes:read:decisions"], _ctx(journal=journal, state="SHADOW_CERTIFIED")
    )

    payload = dispatcher.call("decisions", {}).payload

    assert payload == {
        "reviewable": [{"entry_id": "p1", "decision": "Slim the hook", "review_on": "2026-10-01"}],
        "sealed": [{"entry_id": "s1", "review_on": "2026-10-01"}],
    }


def test_strategy_decisions_unseal_after_oos_certification() -> None:
    """Once OOS-certified, strategy calls are reviewable like any other."""
    journal = _FakeJournal([_decision("s1", OutcomeKind.STRATEGY, "Expect ETH entries to win")])
    dispatcher, _ = _dispatcher(
        ["hermes:read:decisions"], _ctx(journal=journal, state="OOS_CERTIFIED")
    )

    payload = dispatcher.call("decisions", {}).payload

    assert payload is not None
    assert payload["sealed"] == []


def test_certification_without_a_record_is_untrusted() -> None:
    """No stored record reads as UNTRUSTED with no entry time."""
    dispatcher, _ = _dispatcher(["hermes:read:certification"])

    payload = dispatcher.call("certification", {}).payload

    assert payload == {"strategy_id": "swing-donchian-4h", "state": "UNTRUSTED", "entered_at": None}


def test_certification_reports_state_and_entry_time() -> None:
    """A stored record's state and last transition time are reported, nothing else."""
    dispatcher, _ = _dispatcher(["hermes:read:certification"], _ctx(state="SHADOW_CERTIFIED"))

    payload = dispatcher.call("certification", {}).payload

    assert payload == {
        "strategy_id": "swing-donchian-4h",
        "state": "SHADOW_CERTIFIED",
        "entered_at": NOW.isoformat(),
    }


def test_engine_reports_each_configured_pair_from_the_ledger_facts() -> None:
    """Per pair: bars per interval, entries, exit rules, fills by side and holding time."""
    since = LAST_4H - timedelta(days=1)
    reader = _FakeReader(
        facts=(
            SignalFact(BTC, "entry", since),
            SignalFact(BTC, "exit", LAST_4H, exit_reason="chandelier"),
            SignalFact(ETH, "entry", LAST_4H),
        ),
        entries=(ShadowEntry(ETH, LAST_4H, Decimal("2618.45")),),
        bars=(
            BarCount(BTC, KlineInterval.H4, 6),
            BarCount(BTC, KlineInterval.D1, 1),
            BarCount(ETH, KlineInterval.H4, 5),
        ),
        fills=(
            FillFact(BTC, "BUY", since),
            FillFact(BTC, "SELL", LAST_4H),
            FillFact(ETH, "BUY", LAST_4H),
        ),
    )

    view = engine(_ctx(reader), {"since": since.isoformat()})

    assert [
        (
            p.pair,
            [(b.interval, b.count) for b in p.bars],
            p.entries,
            p.exit_reasons,
            p.fills_buy,
            p.fills_sell,
            p.holding_since,
        )
        for p in view.pairs
    ] == [
        (str(BTC), [("4h", 6), ("1d", 1)], 1, ("chandelier",), 1, 1, None),
        (str(ETH), [("4h", 5), ("1d", 0)], 1, (), 1, 0, LAST_4H),
        (str(SOL), [("4h", 0), ("1d", 0)], 0, (), 0, 0, None),
    ]


def test_engine_before_the_guard_is_refused() -> None:
    """The engine tool holds the same memorization guard as the signals tool."""
    dispatcher, records = _dispatcher(["hermes:read:engine"])

    result = dispatcher.call("engine", {"since": "2026-01-31T23:59:59+00:00"})

    assert result.payload is None
    assert records[-1].outcome is CallOutcome.GUARD
