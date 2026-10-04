"""Telegram reply text for the read-only commands (ADR-015 D-02).

Every reply is built from the same layers the CLI prints: the ``hermes status``
report, the console read model, the ledger activity rows and the journal
lines. Replies are plain text (no Markdown), short sentences, Madrid time with
UTC where a time is actionable, amounts in EUR. Money only appears when
``pnl_visible`` allows it for the surface (a pulled reply does; a pushed
message never does).

Reference: openspec/changes/379-telegram-bot/change.md (D-2).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Final

from hermes.application.console import (
    ConsoleReadModel,
    PortfolioRange,
    Surface,
    ViewNotAvailableError,
)
from hermes.application.console.format import fmt_change, fmt_eur_price, fmt_level, signed_eur
from hermes.application.console.logs import LogLine
from hermes.application.console.views import (
    ActivityItem,
    ActivityKind,
    PortfolioSummary,
    TaxPending,
    WaterfallColumn,
    tax_amount,
)
from hermes.application.console.visibility import pnl_visible
from hermes.application.status import MADRID, StatusReport

__all__ = ["COMMANDS", "Replies", "ReplySources", "parse_command"]

COMMANDS: Final = ("status", "next", "signals", "money", "pos", "logs")
_HELP: Final = "Commands: /status /next /signals /money [week|month|year] /pos [COIN] /logs"
_NO_LIVE_MONEY: Final = "No live money yet: simulated fills start with #353."
_BLIND: Final = "Money is not shown here."
_TAX_PENDING: Final = "tax estimate: not available yet"
_EUR_PENDING: Final = "EUR pending"
_LOG_LINES: Final = 20


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """Split ``/money@hermes_bot week`` into ``("money", ["week"])``.

    Args:
        text: The message text.

    Returns:
        The lower-case command and its arguments, or ``None`` for plain text.
    """
    words = text.strip().split()
    if not words or not words[0].startswith("/"):
        return None
    name = words[0][1:].split("@", 1)[0].lower()
    return name, words[1:]


@dataclass(frozen=True, slots=True)
class ReplySources:
    """Where replies get their facts; each is called only when needed.

    Attributes:
        status: Builds the current ``hermes status`` report.
        read_model: The console read model, or ``None`` without live or demo data.
        activity: Ledger activity rows at or after the given instant.
        warnings: Recent WARNING-or-worse journal lines, oldest first.
    """

    status: Callable[[], StatusReport]
    read_model: Callable[[], ConsoleReadModel | None]
    activity: Callable[[datetime], Sequence[ActivityItem]]
    warnings: Callable[[], Sequence[LogLine]]


@dataclass
class Replies:
    """Turns commands into reply text; remembers the last ``/status``.

    Attributes:
        sources: Where the facts come from.
        tz: Display timezone.
        surface: Where the text goes; decides whether money shows.
    """

    sources: ReplySources
    tz: tzinfo = MADRID
    surface: Surface = Surface.TELEGRAM_PULLED
    _last_status: datetime | None = field(default=None)

    def reply(self, name: str, args: list[str], now: datetime) -> str:
        """Return the reply to ``/name args`` at ``now``; unknown names get help."""
        handlers: dict[str, Callable[[], str]] = {
            "status": lambda: self._status(now),
            "next": self._next,
            "signals": lambda: self._signals(now),
            "money": lambda: self._money(args),
            "pos": lambda: self._pos(args),
            "logs": self._logs,
        }
        handler = handlers.get(name)
        return _HELP if handler is None else handler()

    def _status(self, now: datetime) -> str:
        report = self.sources.status()
        verdict = report.verdict
        lines = [f"{verdict.headline}. {verdict.reason}", f"Your move: {verdict.next_step}."]
        nxt = min((s.next_close for s in report.streams if s.next_close), default=None)
        if nxt is not None:
            lines.append(f"Next check {self._clock(nxt)} ({nxt:%H:%M} UTC).")
        since = self._last_status or now - timedelta(hours=24)
        self._last_status = now
        lines.append(self._since_line(since))
        model = self.sources.read_model()
        if model is not None:
            test = model.shadow_test()
            day = f"day {test.day} of {test.length_days}" if test.day else "not started"
            note = f", {test.note}" if test.note and not test.day else ""
            lines.append(f"90-day test: {day}, {test.status.value}{note}.")
            if self._money_visible():
                try:
                    summary = model.portfolio(PortfolioRange.TODAY)
                except ViewNotAvailableError:
                    lines.append("Total: not available yet.")
                else:
                    if summary.eur_pending:
                        lines.append(f"Total: {_EUR_PENDING}.")
                    else:
                        total, net = fmt_level(summary.total_value), signed_eur(summary.net)
                        lines.append(f"Total {total}, today's net {net}.")
        else:
            lines.append("90-day test: not started, waits on #353.")
        return "\n".join(lines)

    def _since_line(self, since: datetime) -> str:
        items = self.sources.activity(since)
        opened = sum(1 for i in items if i.kind is ActivityKind.OPENED)
        closed = sum(1 for i in items if i.kind is ActivityKind.CLOSED)
        signals = sum(1 for i in items if i.kind is ActivityKind.SIGNAL)
        when = self._clock(since)
        if not (opened or closed or signals):
            return f"Since {when}: nothing new."
        return f"Since {when}: {signals} signals, {opened} opened, {closed} closed."

    def _next(self) -> str:
        report = self.sources.status()
        if not report.streams:
            return report.verdict.reason
        lines = ["Next bars:"]
        for s in report.streams:
            if s.next_close is None:
                lines.append(f"{s.pair} {s.interval.value}: no bar yet")
            else:
                when = self._clock(s.next_close)
                lines.append(f"{s.pair} {s.interval.value}: {when} ({s.next_close:%H:%M} UTC)")
        return "\n".join(lines)

    def _signals(self, now: datetime) -> str:
        items = [
            i
            for i in self.sources.activity(now - timedelta(days=7))
            if i.kind is ActivityKind.SIGNAL
        ]
        if not items:
            return "No signals in the last 7 days."
        lines = [f"{len(items)} signals in the last 7 days:"]
        lines += [
            f"{i.at.astimezone(self.tz):%a %H:%M} {i.coin or ''} {i.text or ''}".rstrip()
            for i in sorted(items, key=lambda i: i.at, reverse=True)
        ]
        return "\n".join(lines)

    def _money(self, args: list[str]) -> str:
        if not self._money_visible():
            return _BLIND
        model = self.sources.read_model()
        if model is None:
            return _NO_LIVE_MONEY
        try:
            range_ = PortfolioRange(args[0].lower()) if args else PortfolioRange.TODAY
        except ValueError:
            return "Usage: /money [week|month|year]"
        try:
            summary = model.portfolio(range_)
        except ViewNotAvailableError as exc:
            return f"{exc}."
        return _money_text(summary, range_)

    def _pos(self, args: list[str]) -> str:
        model = self.sources.read_model()
        if model is None:
            return "No open shadow positions. Fills start with #353."
        visible = self._money_visible()
        if args:
            detail = model.position(args[0].upper())
            if detail is None:
                return f"No open position in {args[0].upper()}."
            row = detail.row
            lines = [
                f"{row.coin} {row.side} · {row.timeframe} · opened {self._clock(row.opened_at)}",
                f"Entry {fmt_eur_price(row.entry_eur)} · stop {fmt_eur_price(row.stop_eur)}",
            ]
            if visible:
                tax = (
                    _TAX_PENDING
                    if isinstance(detail.tax, TaxPending)
                    else signed_eur(-tax_amount(detail.tax))
                )
                fees = signed_eur(-(detail.fees_paid + detail.fees_to_close))
                lines.append(
                    f"Gross {signed_eur(detail.gross)} · fees {fees} · tax {tax}"
                    f" · net {signed_eur(detail.net)}"
                )
            return "\n".join(lines)
        rows = model.positions()
        if not rows:
            return "No open shadow positions."
        lines = [f"{len(rows)} open (simulated)"]
        for row in rows:
            money = f" · net {signed_eur(row.net)}" if visible else ""
            lines.append(f"{row.coin} {row.side} · {row.timeframe}{money}")
        return "\n".join(lines)

    def _logs(self) -> str:
        lines = list(self.sources.warnings())[-_LOG_LINES:]
        if not lines:
            return "No warnings or errors in the journal."
        return "\n".join(
            f"{line.at.astimezone(self.tz):%H:%M} {line.level.upper()} {line.event or line.raw}"
            for line in lines
        )

    def _money_visible(self) -> bool:
        return pnl_visible(self.surface)

    def _clock(self, at: datetime) -> str:
        return at.astimezone(self.tz).strftime("%H:%M")


def _money_text(summary: PortfolioSummary, range_: PortfolioRange) -> str:
    if summary.eur_pending:
        return f"Total: {_EUR_PENDING} (no EUR rate in the ledger yet)."
    lines = [
        f"Total {fmt_level(summary.total_value)} ({fmt_change(summary.change, summary.change_pct)}"
        f" {range_.value})",
        _waterfall("Today", summary.today),
        _waterfall("This year", summary.year),
    ]
    return "\n".join(lines)


def _waterfall(label: str, column: WaterfallColumn) -> str:
    if column.eur_pending:
        return f"{label}: {_EUR_PENDING}"
    tax = (
        _TAX_PENDING if isinstance(column.tax, TaxPending) else signed_eur(-tax_amount(column.tax))
    )
    return (
        f"{label}: gross {signed_eur(column.gross)} · fees {signed_eur(-column.fees)}"
        f" · tax {tax} · net {signed_eur(column.net)}"
    )
