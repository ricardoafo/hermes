"""Every read the console makes of its sources, gathered in one place (issue #1001).

The console paints from a :class:`ConsoleReads`: one value per card, or the
error that card shows instead. :func:`read_console` makes each read once, for
the screens a :class:`ReadRequest` names, and touches no widget, so the
console can run it in a worker thread while keys and animations carry on.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from hermes.application.console import (
    ConsoleReadModel,
    Heartbeat,
    PortfolioRange,
    PortfolioSummary,
    PositionDetail,
    PositionRow,
    Surface,
    ViewNotAvailableError,
    WatchedPair,
)
from hermes.application.console.logs import LogLine
from hermes.application.console.render import RenderContext
from hermes.application.console.shadow_screen import (
    ShadowRecordError,
    ShadowScreen,
    ShadowSources,
    build_shadow_screen,
)
from hermes.application.console.system import (
    MachineStats,
    ReleaseHistory,
    ServiceStatus,
    SystemSources,
)
from hermes.application.console.views import ActivityDay, ConsoleBrief, RuleCheck, TaxPending
from hermes.application.tax.screen import TaxScreen, TaxScreenSource
from hermes.domain.errors import LedgerError
from hermes.entrypoints.tui_system import BAR_CHECKS
from hermes.entrypoints.tui_tax import TaxPackStatus

__all__ = [
    "CARD_ERRORS",
    "SHADOW_ERRORS",
    "ConsoleReads",
    "Failed",
    "ReadRequest",
    "ReadSources",
    "ShadowFailed",
    "SystemReads",
    "read_console",
    "read_shadow",
    "read_system",
]

type Failed = ViewNotAvailableError | LedgerError
type ShadowFailed = ViewNotAvailableError | LedgerError | ShadowRecordError

CARD_ERRORS: Final = (ViewNotAvailableError, LedgerError)
SHADOW_ERRORS: Final = (ViewNotAvailableError, LedgerError, ShadowRecordError)
_DOWN_HINT: Final = "press L for why"


@dataclass(frozen=True, slots=True)
class ReadSources:
    """Everything the console reads from; each source is read-only.

    Attributes:
        model: The read model every card draws from.
        shadow: The Shadow test screen's sources.
        journal: Returns the last N journal lines, or ``None`` when unreadable.
        release: Returns the running release's name.
        tax: The Tax screen's source.
        tax_pack: Returns the year's tax pack state.
        system: The System screen's sources.
    """

    model: ConsoleReadModel
    shadow: ShadowSources
    journal: Callable[[int], Sequence[LogLine] | None] | None = None
    release: Callable[[], str] | None = None
    tax: TaxScreenSource | None = None
    tax_pack: Callable[[int], TaxPackStatus | None] | None = None
    system: SystemSources = field(default_factory=SystemSources)


@dataclass(frozen=True, slots=True)
class ReadRequest:
    """What the shown screens need read, decided on the UI thread.

    Attributes:
        width: The cards' inner width, for the render context.
        range_: The portfolio range the change line and curve show.
        include_quiet: Include the one-line 4h checks in Activity.
        log_tail_rows: Journal lines for the ultra-wide log tail; ``None`` when not shown.
        logs_lines: Journal lines for the Logs screen or panel; ``None`` when neither shows.
        briefs: The Briefs screen shows.
        signals: The Signals screen shows.
        tax: The Tax screen shows.
        system: The System screen shows.
    """

    width: int
    range_: PortfolioRange = PortfolioRange.TODAY
    include_quiet: bool = False
    log_tail_rows: int | None = None
    logs_lines: int | None = None
    briefs: bool = False
    signals: bool = False
    tax: bool = False
    system: bool = False


@dataclass(frozen=True, slots=True)
class SystemReads:
    """One read of the System screen's sources; a missing source stays ``None``.

    Attributes:
        services: systemd state of the hermes units.
        releases: The releases on disk.
        machine: cpu, memory, disk, ledger size, last snapshot.
        bar_seconds: The last bar checks' durations; ``None`` when unreadable.
    """

    services: tuple[ServiceStatus, ...] | None = None
    releases: ReleaseHistory | None = None
    machine: MachineStats | None = None
    bar_seconds: tuple[Decimal, ...] | None = None


@dataclass(frozen=True, slots=True)
class ConsoleReads:
    """One read of every source the shown screens need.

    A field holding an error is the card's reason to show nothing; a field
    left ``None`` was not asked for, or has no source here.

    Attributes:
        ctx: The render context: the clock, the staleness and the time zone.
        release: The running release's name; ``None`` without a source or when it fails.
        heartbeat: The engine's last bar check.
        portfolio: The portfolio over the requested range.
        positions: The open positions.
        details: Each open position's detail, by coin.
        watch: The pairs hermes checks and when it next looks.
        activity: The activity days.
        shadow: The Shadow test screen.
        log_tail: The log tail's journal lines.
        logs: The Logs screen's journal lines.
        logs_asked: The request asked for the Logs screen's lines, so
            ``logs`` being ``None`` means the journal is not readable here.
        briefs: The Briefs screen's briefs.
        rule_checks: The Signals screen's rule checks.
        tax: The Tax screen, or why there is none.
        tax_pack: The tax pack state for the Tax screen's year.
        system: The System screen's reads.
    """

    ctx: RenderContext
    release: str | None
    heartbeat: Heartbeat | Failed
    portfolio: PortfolioSummary | Failed
    positions: tuple[PositionRow, ...] | Failed
    details: Mapping[str, PositionDetail] | Failed
    watch: tuple[WatchedPair, ...] | Failed
    activity: tuple[ActivityDay, ...] | Failed
    shadow: ShadowScreen | ShadowFailed
    log_tail: Sequence[LogLine] | None = None
    logs: Sequence[LogLine] | None = None
    logs_asked: bool = False
    briefs: tuple[ConsoleBrief, ...] | Failed | None = None
    rule_checks: tuple[RuleCheck, ...] | Failed | None = None
    tax: TaxScreen | TaxPending | None = None
    tax_pack: TaxPackStatus | None = None
    system: SystemReads | None = None


def read_console(sources: ReadSources, request: ReadRequest) -> ConsoleReads:
    """Read every source the request names, once each.

    Args:
        sources: Where to read from.
        request: What the shown screens need.

    Returns:
        The reads; a failed read keeps its error in place of its value.
    """
    model = sources.model
    ctx = _context(model, request.width)
    positions = _attempt(model.positions)
    tax, tax_pack = _read_tax(sources, request, ctx, positions)
    return ConsoleReads(
        ctx=ctx,
        release=_read_release(sources.release),
        heartbeat=_attempt(model.heartbeat),
        portfolio=_attempt(lambda: model.portfolio(request.range_)),
        positions=positions,
        details=_read_details(model, positions),
        watch=_attempt(model.market_watch),
        activity=_attempt(lambda: model.activity(include_quiet=request.include_quiet)),
        shadow=_read_shadow_screen(sources.shadow, ctx),
        log_tail=_read_journal(sources, request.log_tail_rows),
        logs=_read_journal(sources, request.logs_lines),
        logs_asked=request.logs_lines is not None,
        briefs=_attempt(model.briefs) if request.briefs else None,
        rule_checks=_attempt(model.rule_checks) if request.signals else None,
        tax=tax,
        tax_pack=tax_pack,
        system=read_system(sources.system) if request.system else None,
    )


def read_shadow(sources: ShadowSources, ctx: RenderContext) -> ShadowScreen:
    """Return the Shadow test screen read now from its sources.

    Args:
        sources: The Shadow test screen's sources.
        ctx: Gives the time and the time zone it is read at.

    Raises:
        LedgerError: The ledger could not be read.
        ShadowRecordError: A day-0 record could not be read.
    """
    strategy = sources.strategy_id
    runs = sources.runs
    record = None if runs is None or strategy is None else runs.latest_run(strategy)
    notebook = None if sources.notebook is None else sources.notebook.notebook_entries()
    return build_shadow_screen(
        record=record,
        evidence=sources.evidence,
        notebook=notebook,
        as_of=ctx.now,
        tz=ctx.tz,
    )


def read_system(sources: SystemSources) -> SystemReads:
    """Read the System screen's sources; a missing source stays ``None``.

    Args:
        sources: The System screen's sources.

    Returns:
        The reads; unreadable bar timing is ``None``.
    """
    bar_seconds = None
    if sources.bar_timing is not None:
        try:
            bar_seconds = sources.bar_timing.bar_check_seconds(BAR_CHECKS)
        except LedgerError:
            bar_seconds = None
    return SystemReads(
        services=None if sources.services is None else sources.services.services(),
        releases=None if sources.releases is None else sources.releases.releases(),
        machine=None if sources.machine is None else sources.machine.machine(),
        bar_seconds=bar_seconds,
    )


def _attempt[T](read: Callable[[], T]) -> T | Failed:
    try:
        return read()
    except CARD_ERRORS as exc:
        return exc


def _context(model: ConsoleReadModel, width: int) -> RenderContext:
    try:
        ctx = RenderContext.for_model(
            model, surface=Surface.OPERATOR_TUI, width=width, down_hint=_DOWN_HINT
        )
    except CARD_ERRORS:
        return RenderContext(surface=Surface.OPERATOR_TUI, now=datetime.now(UTC), width=width)
    return replace(ctx, demo_scenario=None)


def _read_release(release: Callable[[], str] | None) -> str | None:
    if release is None:
        return None
    try:
        return release()
    except Exception:
        return None


def _read_details(
    model: ConsoleReadModel, positions: tuple[PositionRow, ...] | Failed
) -> Mapping[str, PositionDetail] | Failed:
    if isinstance(positions, CARD_ERRORS):
        return {}
    try:
        details = {row.coin: model.position(row.coin) for row in positions}
    except CARD_ERRORS as exc:
        return exc
    return {coin: detail for coin, detail in details.items() if detail is not None}


def _read_shadow_screen(sources: ShadowSources, ctx: RenderContext) -> ShadowScreen | ShadowFailed:
    try:
        return read_shadow(sources, ctx)
    except SHADOW_ERRORS as exc:
        return exc


def _read_journal(sources: ReadSources, lines: int | None) -> Sequence[LogLine] | None:
    # A demo scenario has no journal of its own: its log tail and Logs screen say so.
    if lines is None or sources.journal is None or sources.model.demo_scenario is not None:
        return None
    return sources.journal(lines)


def _read_tax(
    sources: ReadSources,
    request: ReadRequest,
    ctx: RenderContext,
    positions: tuple[PositionRow, ...] | Failed,
) -> tuple[TaxScreen | TaxPending | None, TaxPackStatus | None]:
    if not request.tax:
        return None, None
    screen: TaxScreen | TaxPending
    if sources.tax is None:
        screen = TaxPending(label="tax estimate not available yet")
    else:
        prices = (
            {}
            if isinstance(positions, CARD_ERRORS)
            else {row.coin: row.current_eur for row in positions}
        )
        try:
            screen = sources.tax.tax_screen(as_of=ctx.now, prices=prices)
        except LedgerError:
            screen = TaxPending(label="could not read the ledger")
    year = screen.year if isinstance(screen, TaxScreen) else ctx.now.astimezone(ctx.tz).year
    pack = None if sources.tax_pack is None else sources.tax_pack(year)
    return screen, pack
