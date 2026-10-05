"""Read-only, outcome-blind supervisor tools (ADR-003 §08, ADR-012 D-06/D-07).

The supervisor (the desk, routines and the MCP transport) reads the system
only through these tools. Each tool:

- has one read scope (``hermes:read:<name>``); no write or trade scope exists,
  so no tool can place, cancel or change anything;
- returns a typed projection built by code from an allowlist of fields:
  times, enums, pairs, counts and the Code Owner's own decision titles. No
  P&L, equity, price, position value, Sharpe, drawdown or win rate reaches
  the supervisor before certification (ADR-012 D-06);
- refuses history before the memorization guard, 2026-02-01 or later
  (ADR-003 §08): a model recalls pre-cutoff market data from training, so
  its "analysis" of that period would be recall.

Access is deny-by-default: :class:`ToolDispatcher` serves only the tools whose
scope the caller was granted, and every call, refused or not, is audited.

Reference: openspec/changes/34-supervisor-read-tools/change.md; issues #34, #470.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Final

from hermes.application.journal.service import due
from hermes.application.status import UnitState, build_report
from hermes.domain.journal.entries import Decision
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.pair import Pair
from hermes.ports.decision_journal import DecisionJournalPort
from hermes.ports.supervisor import CertificationLookup, SupervisorReadPort

__all__ = [
    "MEMORIZATION_GUARD",
    "TOOLS",
    "UNSEALED_STATES",
    "AuditRecord",
    "BarCountView",
    "CallOutcome",
    "CertificationView",
    "DecisionsView",
    "DueDecisionView",
    "EngineView",
    "HealthView",
    "MemorizationGuardError",
    "PairActivityView",
    "PositionView",
    "PositionsView",
    "SealedDecisionView",
    "SignalView",
    "SignalsView",
    "StreamView",
    "SupervisorContext",
    "SupervisorTool",
    "ToolArgumentError",
    "ToolDispatcher",
    "ToolResult",
    "UnknownToolError",
    "to_json",
]

MEMORIZATION_GUARD: Final = datetime(2026, 2, 1, tzinfo=UTC)
"""ADR-003 §08: no tool returns data from before this instant."""

UNSEALED_STATES: Final = frozenset({"OOS_CERTIFIED", "LIVE_APPROVED", "CERTIFIED"})
"""Certification states after which strategy decisions may be reviewed (#363 D-1)."""


class MemorizationGuardError(ValueError):
    """A request reached back before the memorization guard."""


class ToolArgumentError(ValueError):
    """A tool was called with a missing, unknown or malformed argument."""


class UnknownToolError(LookupError):
    """The tool does not exist, or its scope was not granted (deny-by-default)."""


@dataclass(frozen=True, slots=True)
class SupervisorContext:
    """What the tools read from, built fresh for every call.

    Attributes:
        reader: Read-only ledger access.
        pairs: Configured pairs.
        intervals: Configured intervals.
        settle: Settle delay after a bar closes.
        grace: Lateness tolerance.
        unit: systemd state of ``hermes.service``.
        now: The instant the call describes, UTC-aware.
        journal: The decision journal, or ``None`` where there is none.
        certification: Looks up a strategy's certification state, or ``None``.
        strategy_id: The strategy whose certification seals decisions.
        guard: Memorization guard; never earlier than :data:`MEMORIZATION_GUARD`.
    """

    reader: SupervisorReadPort
    pairs: tuple[Pair, ...]
    intervals: tuple[KlineInterval, ...]
    settle: timedelta
    grace: timedelta
    unit: UnitState
    now: datetime
    journal: DecisionJournalPort | None = None
    certification: CertificationLookup | None = None
    strategy_id: str = ""
    guard: datetime = MEMORIZATION_GUARD

    def __post_init__(self) -> None:
        """Refuse a guard earlier than the ADR-003 §08 floor.

        Raises:
            ValueError: ``guard`` is before :data:`MEMORIZATION_GUARD`.
        """
        if self.guard < MEMORIZATION_GUARD:
            raise ValueError(
                f"guard {self.guard.isoformat()} is before the floor "
                f"{MEMORIZATION_GUARD.date().isoformat()}"
            )


@dataclass(frozen=True, slots=True)
class StreamView:
    """One stream's progress: times and state only."""

    pair: str
    interval: str
    last_close: datetime | None
    next_close: datetime | None
    state: str


@dataclass(frozen=True, slots=True)
class HealthView:
    """The verdict and every stream, as ``hermes status`` computes them."""

    verdict: str
    headline: str
    reason: str
    next_step: str
    streams: tuple[StreamView, ...]
    signals_24h: int
    signals_7d: int


@dataclass(frozen=True, slots=True)
class SignalView:
    """A recorded signal: which pair, entry or exit, and when."""

    pair: str
    kind: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SignalsView:
    """Signals at or after ``since``, oldest first."""

    since: datetime
    signals: tuple[SignalView, ...]


@dataclass(frozen=True, slots=True)
class PositionView:
    """An open shadow position: which pair and since when. No price or value."""

    pair: str
    entry_time: datetime


@dataclass(frozen=True, slots=True)
class PositionsView:
    """Every open shadow position, oldest first."""

    positions: tuple[PositionView, ...]


@dataclass(frozen=True, slots=True)
class DueDecisionView:
    """A decision ready to review: its id, the Code Owner's title and the date."""

    entry_id: str
    decision: str
    review_on: date


@dataclass(frozen=True, slots=True)
class SealedDecisionView:
    """A strategy decision held until certification: id and date, no text.

    The title of a strategy call can describe the outcome it expected, so it
    stays out until the strategy is certified.
    """

    entry_id: str
    review_on: date


@dataclass(frozen=True, slots=True)
class DecisionsView:
    """Decisions due for review, and those sealed until certification."""

    reviewable: tuple[DueDecisionView, ...]
    sealed: tuple[SealedDecisionView, ...]


@dataclass(frozen=True, slots=True)
class CertificationView:
    """The strategy's lifecycle state and when it entered it. No metrics."""

    strategy_id: str
    state: str
    entered_at: datetime | None


@dataclass(frozen=True, slots=True)
class BarCountView:
    """How many bars of one interval the loop handled for a pair."""

    interval: str
    count: int


@dataclass(frozen=True, slots=True)
class PairActivityView:
    """What the engine did on one pair: checks, signals, fills and whether it holds.

    Counts, rule names and times only; never a price, size or value (ADR-012 D-06).
    """

    pair: str
    bars: tuple[BarCountView, ...]
    entries: int
    exit_reasons: tuple[str, ...]
    fills_buy: int
    fills_sell: int
    holding_since: datetime | None


@dataclass(frozen=True, slots=True)
class EngineView:
    """What the engine did on every configured pair since ``since``."""

    since: datetime
    pairs: tuple[PairActivityView, ...]


def health(ctx: SupervisorContext, _args: Mapping[str, object]) -> HealthView:
    """Return the system's verdict and stream progress."""
    report = build_report(
        ctx.reader,
        pairs=ctx.pairs,
        intervals=ctx.intervals,
        now=ctx.now,
        settle=ctx.settle,
        grace=ctx.grace,
        unit=ctx.unit,
    )
    return HealthView(
        verdict=report.verdict.level.value,
        headline=report.verdict.headline,
        reason=report.verdict.reason,
        next_step=report.verdict.next_step,
        streams=tuple(
            StreamView(
                pair=str(s.pair),
                interval=s.interval.value,
                last_close=s.last_close,
                next_close=s.next_close,
                state=s.state.value,
            )
            for s in report.streams
        ),
        signals_24h=report.signals_24h,
        signals_7d=report.signals_7d,
    )


def signals(ctx: SupervisorContext, args: Mapping[str, object]) -> SignalsView:
    """Return the signals at or after ``args["since"]``, oldest first.

    Raises:
        ToolArgumentError: ``since`` is missing or not a UTC-aware ISO time.
        MemorizationGuardError: ``since`` is before the guard.
    """
    since = _guarded_since(ctx, args)
    return SignalsView(
        since=since,
        signals=tuple(
            SignalView(pair=str(f.pair), kind=f.kind, occurred_at=f.occurred_at)
            for f in ctx.reader.signals_since(since)
        ),
    )


def engine(ctx: SupervisorContext, args: Mapping[str, object]) -> EngineView:
    """Return what the engine did on each configured pair since ``args["since"]``.

    Raises:
        ToolArgumentError: ``since`` is missing or not a UTC-aware ISO time.
        MemorizationGuardError: ``since`` is before the guard.
    """
    since = _guarded_since(ctx, args)
    bars = ctx.reader.bars_since(since)
    signals_ = ctx.reader.signals_since(since)
    fills = ctx.reader.fills_since(since)
    held = {e.pair: e.entry_time for e in ctx.reader.open_entries()}
    return EngineView(
        since=since,
        pairs=tuple(
            PairActivityView(
                pair=str(pair),
                bars=tuple(
                    BarCountView(
                        interval=interval.value,
                        count=sum(
                            b.count for b in bars if (b.pair, b.interval) == (pair, interval)
                        ),
                    )
                    for interval in ctx.intervals
                ),
                entries=sum(1 for f in signals_ if f.pair == pair and f.kind == "entry"),
                exit_reasons=tuple(
                    f.exit_reason or "unknown"
                    for f in signals_
                    if f.pair == pair and f.kind == "exit"
                ),
                fills_buy=sum(1 for f in fills if f.pair == pair and f.side == "BUY"),
                fills_sell=sum(1 for f in fills if f.pair == pair and f.side == "SELL"),
                holding_since=held.get(pair),
            )
            for pair in ctx.pairs
        ),
    )


def positions(ctx: SupervisorContext, _args: Mapping[str, object]) -> PositionsView:
    """Return the open shadow positions, pair and entry time only."""
    return PositionsView(
        positions=tuple(
            PositionView(pair=str(e.pair), entry_time=e.entry_time)
            for e in ctx.reader.open_entries()
        )
    )


def decisions(ctx: SupervisorContext, _args: Mapping[str, object]) -> DecisionsView:
    """Return the decisions due for review; strategy calls stay sealed."""
    if ctx.journal is None:
        return DecisionsView(reviewable=(), sealed=())
    report = due(ctx.journal, today=ctx.now.date(), certified=_certified(ctx))
    return DecisionsView(
        reviewable=tuple(_due_view(d) for d in report.reviewable),
        sealed=tuple(SealedDecisionView(d.entry_id, d.review_on) for d in report.sealed),
    )


def certification(ctx: SupervisorContext, _args: Mapping[str, object]) -> CertificationView:
    """Return the strategy's certification state, without any metric."""
    fact = ctx.certification(ctx.strategy_id) if ctx.certification else None
    if fact is None:
        return CertificationView(strategy_id=ctx.strategy_id, state="UNTRUSTED", entered_at=None)
    return CertificationView(
        strategy_id=ctx.strategy_id, state=fact.state, entered_at=fact.entered_at
    )


def _certified(ctx: SupervisorContext) -> bool:
    fact = ctx.certification(ctx.strategy_id) if ctx.certification else None
    return fact is not None and fact.state in UNSEALED_STATES


def _due_view(decision: Decision) -> DueDecisionView:
    return DueDecisionView(decision.entry_id, decision.decision, decision.review_on)


def _guarded_since(ctx: SupervisorContext, args: Mapping[str, object]) -> datetime:
    since = _since(args)
    if since < ctx.guard:
        raise MemorizationGuardError(
            f"since {since.isoformat()} is before the memorization guard "
            f"{ctx.guard.date().isoformat()} (ADR-003 §08)"
        )
    return since


def _since(args: Mapping[str, object]) -> datetime:
    raw = args.get("since")
    if not isinstance(raw, str):
        raise ToolArgumentError("since is required: an ISO 8601 time with a UTC offset")
    try:
        since = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ToolArgumentError(f"since {raw!r} is not an ISO 8601 time") from exc
    if since.tzinfo is None:
        raise ToolArgumentError(f"since {raw!r} has no UTC offset")
    return since


_NO_ARGS: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_SINCE_ARGS: Final[Mapping[str, object]] = {
    "type": "object",
    "properties": {
        "since": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 with offset, on or after 2026-02-01.",
        }
    },
    "required": ["since"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class SupervisorTool:
    """A named, scoped read tool.

    Attributes:
        name: Tool name, as MCP clients call it.
        scope: The one read scope that unlocks it.
        summary: One line for clients.
        input_schema: JSON Schema of the arguments.
        output: The projection type the tool returns.
        run: Builds the projection from a context and validated arguments.
    """

    name: str
    scope: str
    summary: str
    input_schema: Mapping[str, object]
    output: type
    run: Callable[[SupervisorContext, Mapping[str, object]], object]


TOOLS: Final[Mapping[str, SupervisorTool]] = {
    tool.name: tool
    for tool in (
        SupervisorTool(
            "health",
            "hermes:read:health",
            "Verdict, each stream's last and next bar, and signal counts.",
            _NO_ARGS,
            HealthView,
            health,
        ),
        SupervisorTool(
            "signals",
            "hermes:read:signals",
            "Signals since a time: pair, entry or exit, and when. No prices.",
            _SINCE_ARGS,
            SignalsView,
            signals,
        ),
        SupervisorTool(
            "engine",
            "hermes:read:engine",
            "Per pair since a time: bars checked, signals, exit rules, fills, holding. No prices.",
            _SINCE_ARGS,
            EngineView,
            engine,
        ),
        SupervisorTool(
            "positions",
            "hermes:read:positions",
            "Open shadow positions: pair and entry time. No price or value.",
            _NO_ARGS,
            PositionsView,
            positions,
        ),
        SupervisorTool(
            "decisions",
            "hermes:read:decisions",
            "Decision-journal entries due for review; strategy calls stay sealed.",
            _NO_ARGS,
            DecisionsView,
            decisions,
        ),
        SupervisorTool(
            "certification",
            "hermes:read:certification",
            "The strategy's certification state and when it entered it.",
            _NO_ARGS,
            CertificationView,
            certification,
        ),
    )
}
"""Every supervisor tool. Every scope is ``hermes:read:<name>``; there is no write tool."""


class CallOutcome(StrEnum):
    """How an audited call ended."""

    OK = "ok"
    DENIED = "denied"
    BAD_ARGUMENTS = "bad_arguments"
    GUARD = "memorization_guard"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One audited tool call: what was asked and how it ended, by hash.

    Attributes:
        at: When the call was made.
        tool: Requested tool name.
        scope: The tool's scope, or ``""`` for an unknown tool.
        outcome: How the call ended.
        args_sha256: Hash of the canonical JSON arguments.
        output_sha256: Hash of the returned JSON, or ``""`` without output.
    """

    at: datetime
    tool: str
    scope: str
    outcome: CallOutcome
    args_sha256: str
    output_sha256: str


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A tool's projection, or the reason it was refused.

    Attributes:
        payload: The projection as JSON-ready data; ``None`` on an error.
        error: Plain-words reason; ``None`` on success.
    """

    payload: dict[str, object] | None
    error: str | None


class ToolDispatcher:
    """Serve the granted tools only, and audit every call."""

    def __init__(
        self,
        *,
        grants: Sequence[str],
        context: Callable[[], AbstractContextManager[SupervisorContext]],
        audit: Callable[[AuditRecord], None],
        clock: Callable[[], datetime],
        tools: Mapping[str, SupervisorTool] = TOOLS,
    ) -> None:
        """Build a dispatcher.

        Args:
            grants: Scopes the caller holds. Each must name an existing read
                scope; there is no wildcard.
            context: Opens a fresh context for one call.
            audit: Receives one record per call.
            clock: Wall clock for audit timestamps.
            tools: The registry; tests pass their own.

        Raises:
            ValueError: A grant is not an existing tool's scope.
        """
        known = {t.scope for t in tools.values()}
        unknown = sorted(set(grants) - known)
        if unknown:
            raise ValueError(f"unknown scope(s): {', '.join(unknown)}")
        self._granted = {n: t for n, t in tools.items() if t.scope in set(grants)}
        self._context = context
        self._audit = audit
        self._clock = clock

    def available(self) -> tuple[SupervisorTool, ...]:
        """Return the granted tools, by name."""
        return tuple(self._granted[name] for name in sorted(self._granted))

    def call(self, name: str, args: Mapping[str, object]) -> ToolResult:
        """Run tool ``name`` with ``args`` and audit the call.

        Raises:
            UnknownToolError: The tool does not exist or was not granted.
        """
        tool = self._granted.get(name)
        args_hash = _sha256(dict(args))
        if tool is None:
            self._record(name, "", CallOutcome.DENIED, args_hash, "")
            raise UnknownToolError(f"unknown tool: {name}")
        try:
            _check_names(tool, args)
            with self._context() as ctx:
                payload = to_json(tool.run(ctx, args))
        except ToolArgumentError as exc:
            return self._refuse(tool, CallOutcome.BAD_ARGUMENTS, args_hash, str(exc))
        except MemorizationGuardError as exc:
            return self._refuse(tool, CallOutcome.GUARD, args_hash, str(exc))
        except Exception as exc:  # any read failure is reported to the client, never raised
            return self._refuse(tool, CallOutcome.ERROR, args_hash, type(exc).__name__)
        self._record(name, tool.scope, CallOutcome.OK, args_hash, _sha256(payload))
        return ToolResult(payload=payload, error=None)

    def _refuse(
        self, tool: SupervisorTool, outcome: CallOutcome, args_hash: str, reason: str
    ) -> ToolResult:
        self._record(tool.name, tool.scope, outcome, args_hash, "")
        return ToolResult(payload=None, error=reason)

    def _record(
        self, name: str, scope: str, outcome: CallOutcome, args_hash: str, output_hash: str
    ) -> None:
        self._audit(AuditRecord(self._clock(), name, scope, outcome, args_hash, output_hash))


def _check_names(tool: SupervisorTool, args: Mapping[str, object]) -> None:
    properties = tool.input_schema.get("properties", {})
    allowed = set(properties) if isinstance(properties, Mapping) else set()
    extra = sorted(set(args) - allowed)
    if extra:
        raise ToolArgumentError(f"unknown argument(s): {', '.join(extra)}")


def to_json(value: object) -> dict[str, object]:
    """Return a projection as a JSON-ready dict.

    Args:
        value: A projection dataclass instance.

    Raises:
        TypeError: ``value`` is not a dataclass instance.
    """
    if not dataclasses.is_dataclass(value) or isinstance(value, type):
        raise TypeError(f"not a projection: {type(value).__name__}")
    return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}


def _plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return to_json(value)
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _sha256(data: object) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
