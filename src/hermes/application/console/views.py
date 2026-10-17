"""View objects the operator console renders.

Every console surface (CLI, TUI, pulled Telegram replies) reads these plain,
frozen values through :class:`~hermes.application.console.read_model.ConsoleReadModel`
and never touches the ledger itself. Money is ``Decimal`` EUR; timestamps are
timezone-aware. Net figures are derived properties so ``net = gross - fees - tax``
holds by construction rather than by convention.

Reference: openspec/changes/console-read-model/change.md (D-1, D-7);
ADR-003 §07.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from hermes.application.supervisor.briefing import Brief
    from hermes.application.supervisor.gate import GateVerdict

__all__ = [
    "QUIET_ACTIVITY_KINDS",
    "RULE_COUNT",
    "ActivityDay",
    "ActivityItem",
    "ActivityKind",
    "BriefSection",
    "ConsoleBrief",
    "Heartbeat",
    "HeartbeatState",
    "PortfolioRange",
    "PortfolioSummary",
    "PositionDetail",
    "PositionRow",
    "RuleCheck",
    "ShadowTest",
    "ShadowTestStatus",
    "Tax",
    "TaxBand",
    "TaxEstimate",
    "TaxPending",
    "TimelineEvent",
    "WatchedPair",
    "WaterfallColumn",
    "tax_amount",
]

_ZERO = Decimal(0)

RULE_COUNT = 3
"""The frozen entry rules: breakout, momentum and regime (pre-registration §5.1)."""


class HeartbeatState(StrEnum):
    """Health of the bar loop as the console last saw it."""

    OK = "ok"
    LATE = "late"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Whether hermes is checking bars on schedule.

    Attributes:
        state: OK, LATE (the scheduled check has not happened yet) or DOWN.
        as_of: When this view was read; the clock every relative time uses.
        last_check_at: When the last bar check completed.
        next_check_at: When the next bar check is (or was) due.
        down_since: When hermes stopped; set only when ``state`` is DOWN.
    """

    state: HeartbeatState
    as_of: datetime
    last_check_at: datetime
    next_check_at: datetime
    down_since: datetime | None = None

    def __post_init__(self) -> None:
        """Reject a DOWN heartbeat without a start time, and the reverse."""
        if (self.state is HeartbeatState.DOWN) != (self.down_since is not None):
            msg = "down_since must be set exactly when state is DOWN"
            raise ValueError(msg)

    @property
    def stale_since(self) -> datetime | None:
        """Return when the money figures froze, or ``None`` while they are live."""
        return self.down_since


@dataclass(frozen=True, slots=True)
class TaxEstimate:
    """An estimated tax figure in EUR.

    Attributes:
        amount: Tax owed; a negative amount is a credit (a loss offsets tax).
    """

    amount: Decimal


@dataclass(frozen=True, slots=True)
class TaxPending:
    """Placeholder until the tax module lands; net is then before tax."""

    label: str = "tax estimate: not available yet"


type Tax = TaxEstimate | TaxPending


def tax_amount(tax: Tax) -> Decimal:
    """Return the tax that reduces net: the estimate, or zero while pending.

    Args:
        tax: The estimate or the pending placeholder.

    Returns:
        The owed amount (negative for a credit), ``0`` when pending.
    """
    return tax.amount if isinstance(tax, TaxEstimate) else _ZERO


@dataclass(frozen=True, slots=True)
class TaxBand:
    """Where this year's gains sit on the savings-income scale.

    Attributes:
        rate: Marginal rate of the current band, as a percentage (``19``).
        next_rate: Rate of the next band, or ``None`` in the top band.
        room_to_next: EUR of further gains before the next band starts.
        method: Lot-matching method behind the estimate.
    """

    rate: Decimal
    next_rate: Decimal | None
    room_to_next: Decimal | None
    method: str = "FIFO"


class PortfolioRange(StrEnum):
    """Period the hero change line and the sparkline cover."""

    TODAY = "today"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


@dataclass(frozen=True, slots=True)
class WaterfallColumn:
    """Closed-trade result for one period, from gross down to net.

    Attributes:
        gross: Realised P&L before costs.
        fees: Fees paid, as a positive cost.
        tax: Estimated tax on the period, or pending.
        eur_pending: A closed trade of the period is not priced in EUR yet;
            ``gross``, ``fees`` and ``net`` are then not meaningful.
    """

    gross: Decimal
    fees: Decimal
    tax: Tax
    eur_pending: bool = False

    @property
    def net(self) -> Decimal:
        """Return ``gross - fees - tax``; tax counts as zero while pending."""
        return self.gross - self.fees - tax_amount(self.tax)


@dataclass(frozen=True, slots=True)
class PortfolioSummary:
    """Portfolio value and results for one range.

    Attributes:
        range: The period ``change``, ``change_pct``, ``net`` and ``sparkline`` cover.
        as_of: When the values were computed.
        total_value: Cash plus open positions at current prices, EUR.
        change: Change of ``total_value`` over the range, EUR.
        change_pct: The same change as a percentage of the starting value.
        net: Net P&L over the range after fees and estimated tax.
        sparkline: Total value samples over the range, oldest first.
        cash: Uninvested EUR.
        invested: EUR in open positions at current prices.
        today: Closed-trade waterfall for today.
        year: Closed-trade waterfall for this tax year.
        open_pnl: Unrealised net P&L of open positions, before tax.
        open_tax_if_sold: Tax if every open position were sold now.
        tax_band: Footnote data; ``None`` while tax is pending.
        starting_capital: EUR the account started with, if known.
        started_on: Day the account started, if known.
        starting_capital_quote: The start capital in its quote currency (the
            USDT capital seed), when the source records it; ``starting_capital``
            is then its EUR equivalent at the seed-date rate, or ``None`` while
            that rate is pending.
        eur_pending: No EUR rate is known yet, so no money figure of this
            summary is meaningful; surfaces show ``total_value_quote`` instead.
        total_value_quote: Cash plus open positions in the quote currency (USDT),
            when the source knows it.
        open_pnl_pending: An open position is not priced in EUR yet, so
            ``open_pnl`` is not meaningful.
        quote_currency: The currency ``starting_capital_quote`` and
            ``total_value_quote`` are counted in (the capital seed's asset).
        eur_rate_date: Date of the ECB rate the EUR figures were converted
            at, when the source records it, so a stale rate shows (#692).
        starting_capital_rate_date: Date of the ECB rate behind
            ``starting_capital``, when the source records it.
        sparkline_at: When each ``sparkline`` sample took effect, oldest
            first; empty when the source does not know (the demo fixtures).
        range_from: When the range starts, when the source knows it; a first
            sample after it means the history is shorter than the range.
    """

    range: PortfolioRange
    as_of: datetime
    total_value: Decimal
    change: Decimal
    change_pct: Decimal
    net: Decimal
    sparkline: tuple[Decimal, ...]
    cash: Decimal
    invested: Decimal
    today: WaterfallColumn
    year: WaterfallColumn
    open_pnl: Decimal
    open_tax_if_sold: Tax
    tax_band: TaxBand | None
    starting_capital: Decimal | None = None
    started_on: date | None = None
    starting_capital_quote: Decimal | None = None
    eur_pending: bool = False
    total_value_quote: Decimal | None = None
    open_pnl_pending: bool = False
    quote_currency: str = "USDT"
    eur_rate_date: date | None = None
    starting_capital_rate_date: date | None = None
    sparkline_at: tuple[datetime, ...] = ()
    range_from: datetime | None = None


@dataclass(frozen=True, slots=True)
class PositionRow:
    """One open shadow position, as the positions list shows it.

    Attributes:
        coin: Base asset (``"ETH"``).
        timeframe: Bar timeframe of the strategy (``"4h"``).
        side: ``"long"`` or ``"short"``.
        size: Quantity of ``coin`` held.
        opened_at: Fill time of the entry.
        entry_price: Average entry price in the quote currency.
        current_price: Latest price in the quote currency.
        change_pct: Price change since entry, percent.
        net: Net P&L in EUR if closed now (fees in, tax per the detail).
        stop_price: Current stop level.
        high_since_entry: Highest price since entry.
        stop_distance_atr: Distance from current price to stop, in ATR.
        stop_distance_pct: Distance from current price to stop, percent.
        eur_per_usdt: EUR for one unit of the quote currency (USDT) at read time;
            the console shows every price in EUR through it.
        entry_price_eur: Entry price in EUR at the fill-date rates, when the
            source knows it; otherwise the entry is converted at ``eur_per_usdt``.
        eur_pending: The fills are not priced in EUR yet. No EUR figure of this
            row (net, EUR prices, ``eur_per_usdt``) is meaningful; surfaces show
            prices in USDT and say the EUR values are pending.
    """

    coin: str
    timeframe: str
    side: str
    size: Decimal
    opened_at: datetime
    entry_price: Decimal
    current_price: Decimal
    change_pct: Decimal
    net: Decimal
    stop_price: Decimal
    high_since_entry: Decimal
    stop_distance_atr: Decimal
    stop_distance_pct: Decimal
    eur_per_usdt: Decimal = Decimal(1)
    entry_price_eur: Decimal | None = None
    eur_pending: bool = False

    @property
    def entry_eur(self) -> Decimal:
        """Return the entry price in EUR: the fill-date value when known."""
        if self.entry_price_eur is not None:
            return self.entry_price_eur
        return self.entry_price * self.eur_per_usdt

    @property
    def current_eur(self) -> Decimal:
        """Return the current price in EUR."""
        return self.current_price * self.eur_per_usdt

    @property
    def stop_eur(self) -> Decimal:
        """Return the stop level in EUR."""
        return self.stop_price * self.eur_per_usdt

    @property
    def high_eur(self) -> Decimal:
        """Return the highest price since entry in EUR."""
        return self.high_since_entry * self.eur_per_usdt


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """One step in a position's life: signal, fill, stop move or exit."""

    at: datetime
    label: str


@dataclass(frozen=True, slots=True)
class PositionDetail:
    """Everything the detail view of one position shows.

    Attributes:
        row: The list row for the same position.
        strategy: Strategy that opened it.
        entry_values: Indicator values at entry, as ordered (name, value) pairs.
        stop_moves: How many times the stop has moved.
        gross: Unrealised P&L before costs, EUR.
        fees_paid: Entry fees paid, EUR.
        fees_to_close: Estimated fees to close, EUR.
        tax: Estimated tax if sold now, or pending.
        price_path: Closing prices since entry, oldest first.
        timeline: Signal, fill, stop moves and exit, oldest first.
    """

    row: PositionRow
    strategy: str
    entry_values: tuple[tuple[str, Decimal], ...]
    stop_moves: int
    gross: Decimal
    fees_paid: Decimal
    fees_to_close: Decimal
    tax: Tax
    price_path: tuple[Decimal, ...]
    timeline: tuple[TimelineEvent, ...]

    @property
    def net(self) -> Decimal:
        """Return ``gross - fees_paid - fees_to_close - tax`` (tax zero while pending)."""
        return self.gross - self.fees_paid - self.fees_to_close - tax_amount(self.tax)

    @property
    def now_worth(self) -> Decimal:
        """Return what the position is worth at the current price, EUR."""
        return self.row.size * self.row.current_eur

    @property
    def cost(self) -> Decimal:
        """Return ``size x entry + buy fee`` in EUR."""
        return self.row.size * self.row.entry_eur + self.fees_paid


@dataclass(frozen=True, slots=True)
class WatchedPair:
    """One stream hermes checks, as the market watch shows it (#650).

    It holds only what hermes records about its own checks: no price, no
    entry level and no distance to one. Live prices sit inside the sealed
    shadow-test window, so the console does not show how close a pair is to
    an entry.

    Attributes:
        coin: Base asset (``"BTC"``).
        quote: Quote asset (``"USDT"``).
        timeframe: Bar timeframe of the stream (``"4h"``).
        last_check_at: When the last bar check of this stream completed.
        signal: Kind of the signal that check produced (``"entry"`` or
            ``"exit"``), or ``None`` when it produced none.
        next_check_at: When the next bar check of this stream is due.
    """

    coin: str
    quote: str
    timeframe: str
    last_check_at: datetime
    signal: str | None
    next_check_at: datetime


@dataclass(frozen=True, slots=True)
class RuleCheck:
    """Which frozen entry rules held for one coin on one bar check (#714).

    Yes or no only, never a price, an indicator value or how close a rule
    came to holding (ADR-012 D-06): the Signals screen shows rules, not
    distances.

    Attributes:
        at: The checked candle's boundary (its close), UTC-aware.
        coin: Base asset (``"BTC"``).
        interval: The checked timeframe (``"4h"``).
        breakout: The Donchian breakout held (4h, pre-registration §5.1).
        momentum: The ADX momentum gate held (4h, §5.1).
        regime: The EMA trend filter held (1D, §5.1).
    """

    at: datetime
    coin: str
    interval: str
    breakout: bool
    momentum: bool
    regime: bool

    @property
    def met(self) -> int:
        """Return how many of the three entry rules held."""
        return self.breakout + self.momentum + self.regime

    @property
    def signal(self) -> bool:
        """Return whether all three held, which is an entry signal (§5.1)."""
        return self.met == RULE_COUNT


class ActivityKind(StrEnum):
    """Kinds of activity rows. BAR and SIGNAL appear only on request."""

    OPENED = "opened"
    CLOSED = "closed"
    STOP_RAISED = "stop_raised"
    RESTART = "restart"
    WARNING = "warning"
    ERROR = "error"
    BAR = "bar"
    SIGNAL = "signal"


QUIET_ACTIVITY_KINDS: frozenset[ActivityKind] = frozenset({ActivityKind.BAR, ActivityKind.SIGNAL})


@dataclass(frozen=True, slots=True)
class ActivityItem:
    """One activity row; repeated stop raises arrive already folded.

    Attributes:
        at: When it happened (the latest occurrence for a folded row).
        kind: What happened.
        coin: Asset concerned, if any.
        text: Free text for restarts, warnings and errors.
        price: Fill price for opened and closed rows.
        net: Net P&L for a closed row, EUR.
        count: Occurrences folded into this row.
        stop_from: First stop level of a folded stop raise.
        stop_to: Last stop level of a folded stop raise.
        eur_per_usdt: EUR for one unit of the quote currency at read time.
    """

    at: datetime
    kind: ActivityKind
    coin: str | None = None
    text: str | None = None
    price: Decimal | None = None
    net: Decimal | None = None
    count: int = 1
    stop_from: Decimal | None = None
    stop_to: Decimal | None = None
    eur_per_usdt: Decimal = Decimal(1)

    @property
    def price_eur(self) -> Decimal | None:
        """Return the fill price in EUR, or ``None`` without a price."""
        return None if self.price is None else self.price * self.eur_per_usdt


@dataclass(frozen=True, slots=True)
class ActivityDay:
    """Activity rows of one local calendar day, newest first."""

    day: date
    items: tuple[ActivityItem, ...]


class ShadowTestStatus(StrEnum):
    """Progress verdict of the 90-day shadow test.

    ``RUNNING`` is a test under way whose verdict nobody can give yet (the
    ledger knows the day count, not whether the run is on track).
    """

    NOT_STARTED = "not started"
    RUNNING = "running"
    ON_TRACK = "on track"
    OFF_TRACK = "off track"
    FINISHED = "finished"


@dataclass(frozen=True, slots=True)
class ShadowTest:
    """The 90-day shadow test line.

    Attributes:
        status: Verdict shown after the day count.
        day: Current day of the test, ``None`` before it starts.
        length_days: Planned length of the test.
        note: Short reason, e.g. what a not-started test waits on.
    """

    status: ShadowTestStatus
    day: int | None
    length_days: int = 90
    note: str | None = None


@dataclass(frozen=True, slots=True)
class BriefSection:
    """One item of a supervisor desk brief (#720).

    Attributes:
        heading: Short label, e.g. ``What the engine did``.
        text: The item in plain words.
        sources: The tools or journal entries it cites, e.g. ``("engine",)``.
    """

    heading: str
    text: str
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConsoleBrief:
    """A brief the supervisor desk published, as the Briefs screen shows it (#720).

    The console never writes a brief of its own: this is what
    :func:`~hermes.application.supervisor.briefing.publishable` returned, the
    full brief when it passed the output gate, else the digest. The desk reads
    only the P&L-blind supervisor tools and the gate refuses outcome words and
    any number the tools did not return, so no price, value, equity or P&L can
    be in it (ADR-012 D-06, D-08).

    Attributes:
        kind: ``daily`` or ``weekly``.
        as_of: The instant the brief describes.
        sections: The brief's items, in the desk's order.
        digest: The full brief failed the gate, so this is the digest.
    """

    kind: str
    as_of: datetime
    sections: tuple[BriefSection, ...]
    digest: bool = False

    @classmethod
    def published(cls, brief: Brief, verdict: GateVerdict, *, as_of: datetime) -> Self:
        """Return the view of a brief the desk published, unchanged.

        Args:
            brief: The brief to show, full or digest.
            verdict: The full brief's gate verdict; a failed one marks a digest.
            as_of: The instant the brief describes, timezone-aware.

        Returns:
            The view.
        """
        sections = tuple(BriefSection(i.heading, i.text, i.sources) for i in brief.items)
        return cls(brief.kind.value, as_of, sections, digest=not verdict.passed)
