"""Supervisor desk briefings: the daily line and the weekly brief (#365, ADR-012 D-05).

A brief is built in two steps, both deterministic:

1. :func:`gather` asks the read-only supervisor tools through a
   :class:`~hermes.application.supervisor.tools.ToolDispatcher`, so the desk
   holds the same deny-by-default grants and leaves the same audit lines as
   any other caller. It adds the counts the brief states, computed by code,
   so every number in the text exists in the input (the D-08 gate).
2. :func:`compose` turns that input into items that each cite the tool they
   came from as ``[tool]``, or a journal entry as ``[decision:ID]``.

No model is called: this is the zero-token all-clear of ADR-012 D-09, and the
digest the desk falls back to when a text fails the gate. The brief inherits
the tools' blindness (D-06): it states times, states, pairs and counts, never
a price, value or P&L.

Reference: openspec/archive/365-supervisor-desk-briefings/change.md; issues #365, #496.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from hermes.application.supervisor.gate import GateVerdict, check
from hermes.application.supervisor.tools import ToolDispatcher, UnknownToolError

__all__ = [
    "NOT_ON_CONNECTION",
    "SOURCES",
    "Brief",
    "BriefItem",
    "BriefKind",
    "compose",
    "gather",
    "publishable",
]

SOURCES: Final = ("health", "signals", "engine", "positions", "decisions", "certification")
"""The tools a brief reads, in the order it reads them."""

NOT_ON_CONNECTION: Final = "Not on the data connection yet."
"""What a brief says about an item no tool serves yet, rather than guessing."""

_WINDOWED: Final = frozenset({"signals", "engine"})
"""The tools a brief asks for its window, ``since`` to now."""

ENGINE_RULES: Final = (
    "No signal means no check met all three frozen entry rules (breakout, momentum, "
    "regime). Which rules held on each check, and fills the engine declined: "
    f"{NOT_ON_CONNECTION[0].lower()}{NOT_ON_CONNECTION[1:]}"
)
"""How the brief explains a quiet engine: against the rules, never with a market value."""


class BriefKind(StrEnum):
    """Which brief: the daily line or the weekly brief."""

    DAILY = "daily"
    WEEKLY = "weekly"


_WINDOW: Final[Mapping[BriefKind, timedelta]] = {
    BriefKind.DAILY: timedelta(days=1),
    BriefKind.WEEKLY: timedelta(days=7),
}


@dataclass(frozen=True, slots=True)
class BriefItem:
    """One line of a brief and the evidence behind it.

    Attributes:
        heading: Short label, e.g. ``Health``.
        text: The line, in plain words.
        sources: The references it cites, e.g. ``("health",)``.
    """

    heading: str
    text: str
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Brief:
    """A composed brief and the input it was written from.

    Attributes:
        kind: Daily or weekly.
        items: The lines, in order.
        inputs: The gathered tool payloads and counts.
    """

    kind: BriefKind
    items: tuple[BriefItem, ...]
    inputs: Mapping[str, object]

    def references(self) -> frozenset[str]:
        """Return every reference a text built from :attr:`inputs` may cite."""
        refs = {name for name in SOURCES if name in self.inputs}
        decisions = _section(self.inputs, "decisions")
        for key in ("reviewable", "sealed"):
            refs.update(f"decision:{d.get('entry_id')}" for d in _rows(decisions, key))
        return frozenset(refs)

    def render(self) -> str:
        """Return the brief as plain text: a title line, then one line per item."""
        title = f"Hermes {self.kind.value} brief, {_when(self.inputs.get('now'))}"
        lines = [title]
        for item in self.items:
            cites = " ".join(f"[{s}]" for s in item.sources)
            lines.append(f"- {item.heading}: {item.text} {cites}".rstrip())
        return "\n".join(lines) + "\n"

    def gate(self) -> GateVerdict:
        """Check the rendered brief against the D-08 output gate."""
        return check(self.render(), inputs=self.inputs, references=self.references())


def gather(dispatcher: ToolDispatcher, *, kind: BriefKind, now: datetime) -> dict[str, object]:
    """Read every source tool once and add the counts a brief states.

    A tool that is not granted, or that refuses the call, is recorded as
    ``{"unavailable": reason}`` so the brief says so instead of guessing.

    Args:
        dispatcher: The caller's granted, audited tools.
        kind: Which brief; sets the signal window.
        now: The instant the brief describes, UTC-aware.

    Returns:
        JSON-ready input for :func:`compose` and the gate.
    """
    since = now - _WINDOW[kind]
    bundle: dict[str, object] = {"now": now.isoformat(), "since": since.isoformat()}
    for name in SOURCES:
        args: dict[str, object] = {"since": since.isoformat()} if name in _WINDOWED else {}
        try:
            result = dispatcher.call(name, args)
        except UnknownToolError:
            bundle[name] = {"unavailable": "not granted"}
            continue
        bundle[name] = (
            result.payload
            if result.payload is not None
            else {"unavailable": result.error or "refused"}
        )
    bundle["counts"] = _counts(bundle)
    return bundle


def compose(inputs: Mapping[str, object], *, kind: BriefKind, titles: bool = True) -> Brief:
    """Build a brief from gathered input.

    Args:
        inputs: The output of :func:`gather`.
        kind: Daily or weekly.
        titles: Include the Code Owner's free text (health reasons, decision
            titles). ``False`` builds the digest of states and counts only,
            the fallback when the full brief fails the gate.

    Returns:
        The brief.
    """
    if kind is BriefKind.DAILY:
        return Brief(kind, (_daily(inputs, titles=titles), _engine(inputs)), inputs)
    items = (
        _health(inputs, titles=titles),
        _signals(inputs),
        _engine(inputs),
        _positions(inputs),
        _decisions(inputs, titles=titles),
        _certification(inputs),
        BriefItem("Fill drift", NOT_ON_CONNECTION, ()),
        BriefItem("Kill triggers", NOT_ON_CONNECTION, ()),
        BriefItem("Model spend", "None: this brief is built by code, without a model.", ()),
    )
    return Brief(kind, items, inputs)


def publishable(inputs: Mapping[str, object], *, kind: BriefKind) -> tuple[Brief, GateVerdict]:
    """Return the full brief if it passes the gate, else the digest (D-08).

    Args:
        inputs: The output of :func:`gather`.
        kind: Daily or weekly.

    Returns:
        The brief to post and the full brief's gate verdict.
    """
    full = compose(inputs, kind=kind)
    verdict = full.gate()
    if verdict.passed:
        return full, verdict
    return compose(inputs, kind=kind, titles=False), verdict


def _daily(inputs: Mapping[str, object], *, titles: bool) -> BriefItem:
    counts = _section(inputs, "counts") or {}
    health = _section(inputs, "health")
    if health is None:
        state = "unavailable."
    elif titles:
        state = f"{health.get('verdict')}, {health.get('headline')}."
    else:
        state = f"{health.get('verdict')}."
    text = (
        f"Health {state} Since {_when(inputs.get('since'))}: "
        f"{counts.get('signals', 0)} signal(s), "
        f"{counts.get('open_positions', 0)} open position(s), "
        f"{counts.get('decisions_waiting', 0)} decision(s) waiting."
    )
    return BriefItem("Today", text, _cited(inputs, ("health", "signals", "positions", "decisions")))


def _health(inputs: Mapping[str, object], *, titles: bool) -> BriefItem:
    health = _section(inputs, "health")
    if health is None:
        return BriefItem("Health", _unavailable(inputs, "health"), _cited(inputs, ("health",)))
    parts = [f"{health.get('verdict')}."]
    if titles:
        parts = [f"{health.get('verdict')}, {health.get('headline')}."]
        if health.get("verdict") != "ok":
            parts.append(f"{health.get('reason')} Next: {health.get('next_step')}.")
    behind = [
        f"{s.get('pair')} {s.get('interval')} {s.get('state')}"
        for s in _rows(health, "streams")
        if s.get("state") != "on time"
    ]
    if behind:
        parts.append(f"Behind: {'; '.join(behind)}.")
    return BriefItem("Health", " ".join(parts), ("health",))


def _signals(inputs: Mapping[str, object]) -> BriefItem:
    view = _section(inputs, "signals")
    if view is None:
        return BriefItem("Signals", _unavailable(inputs, "signals"), _cited(inputs, ("signals",)))
    counts = _section(inputs, "counts") or {}
    text = (
        f"{counts.get('signals', 0)} since {_when(inputs.get('since'))}: "
        f"entries {counts.get('entries', 0)}, exits {counts.get('exits', 0)}."
    )
    rows = _rows(view, "signals")
    if rows:
        text += " " + "; ".join(
            f"{r.get('pair')} {r.get('kind')} {_when(r.get('occurred_at'))}" for r in rows
        )
        text += "."
    return BriefItem("Signals", text, ("signals",))


def _engine(inputs: Mapping[str, object]) -> BriefItem:
    heading = "What the engine did"
    view = _section(inputs, "engine")
    if view is None:
        return BriefItem(heading, _unavailable(inputs, "engine"), _cited(inputs, ("engine",)))
    lines = [_pair_activity(row) for row in _rows(view, "pairs")]
    lines.append(ENGINE_RULES)
    return BriefItem(heading, " ".join(lines), ("engine",))


def _pair_activity(row: Mapping[str, object]) -> str:
    bars = " and ".join(f"{b.get('count')} {b.get('interval')}" for b in _rows(row, "bars"))
    parts = [f"{bars} bars checked" if bars else "no bars checked"]
    entries = row.get("entries") or 0
    reasons = row.get("exit_reasons")
    exits = [str(r) for r in reasons] if isinstance(reasons, list) else []
    if not entries and not exits:
        parts.append("no signal")
    else:
        if entries:
            parts.append(f"{entries} entry signal(s)")
        if exits:
            parts.append(f"exit signal(s) by rule: {', '.join(exits)}")
    buys, sells = row.get("fills_buy") or 0, row.get("fills_sell") or 0
    parts.append(f"fills: {buys} buy, {sells} sell" if buys or sells else "no fills")
    since = row.get("holding_since")
    parts.append(f"holding since {_when(since)}" if since else "flat")
    return f"{row.get('pair')}: {'; '.join(parts)}."


def _positions(inputs: Mapping[str, object]) -> BriefItem:
    view = _section(inputs, "positions")
    if view is None:
        name = "positions"
        return BriefItem("Open positions", _unavailable(inputs, name), _cited(inputs, (name,)))
    rows = _rows(view, "positions")
    if not rows:
        return BriefItem("Open positions", "None.", ("positions",))
    listed = "; ".join(f"{r.get('pair')} since {_when(r.get('entry_time'))}" for r in rows)
    return BriefItem("Open positions", f"{listed}.", ("positions",))


def _decisions(inputs: Mapping[str, object], *, titles: bool) -> BriefItem:
    view = _section(inputs, "decisions")
    if view is None:
        return BriefItem(
            "Decisions waiting", _unavailable(inputs, "decisions"), _cited(inputs, ("decisions",))
        )
    reviewable = _rows(view, "reviewable")
    sealed = _rows(view, "sealed")
    if not reviewable and not sealed:
        return BriefItem("Decisions waiting", "None due.", ("decisions",))
    parts: list[str] = []
    for d in reviewable:
        title = f" {d.get('decision')}" if titles else ""
        parts.append(f"{d.get('entry_id')}{title} (review from {d.get('review_on')})")
    if sealed:
        parts.append(f"{len(sealed)} strategy call(s) sealed until certification")
    sources = (
        "decisions",
        *(f"decision:{d.get('entry_id')}" for d in reviewable),
        *(f"decision:{d.get('entry_id')}" for d in sealed),
    )
    return BriefItem("Decisions waiting", "; ".join(parts) + ".", sources)


def _certification(inputs: Mapping[str, object]) -> BriefItem:
    view = _section(inputs, "certification")
    if view is None:
        name = "certification"
        return BriefItem("Certification", _unavailable(inputs, name), _cited(inputs, (name,)))
    entered = view.get("entered_at")
    since = f" since {_when(entered)}" if entered else ""
    return BriefItem("Certification", f"{view.get('state')}{since}.", ("certification",))


def _counts(bundle: Mapping[str, object]) -> dict[str, int]:
    signals = _rows(_section(bundle, "signals"), "signals")
    decisions = _section(bundle, "decisions")
    return {
        "signals": len(signals),
        "entries": sum(1 for s in signals if s.get("kind") == "entry"),
        "exits": sum(1 for s in signals if s.get("kind") == "exit"),
        "open_positions": len(_rows(_section(bundle, "positions"), "positions")),
        "decisions_waiting": len(_rows(decisions, "reviewable")),
        "decisions_sealed": len(_rows(decisions, "sealed")),
    }


def _section(inputs: Mapping[str, object], name: str) -> Mapping[str, object] | None:
    """Return a tool's payload, or ``None`` when it is missing or unavailable."""
    value = inputs.get(name)
    if not isinstance(value, Mapping) or "unavailable" in value:
        return None
    return value


def _rows(view: Mapping[str, object] | None, key: str) -> Sequence[Mapping[str, object]]:
    value = view.get(key) if view is not None else None
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, Mapping))


def _unavailable(inputs: Mapping[str, object], name: str) -> str:
    value = inputs.get(name)
    reason = value.get("unavailable") if isinstance(value, Mapping) else None
    return f"Unavailable ({reason or 'not read'})."


def _cited(inputs: Mapping[str, object], names: Sequence[str]) -> tuple[str, ...]:
    return tuple(n for n in names if n in inputs)


def _when(raw: object) -> str:
    """Return an ISO time as ``YYYY-MM-DD HH:MM UTC``, or ``unknown``."""
    if not isinstance(raw, str):
        return "unknown"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.strftime("%Y-%m-%d %H:%M UTC")
