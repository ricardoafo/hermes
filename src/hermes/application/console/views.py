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

from hermes.application.console.observation import (
    Distribution,
    DriftVerdict,
    drift_verdict,
    summarize,
)

__all__ = [
    "QUIET_ACTIVITY_KINDS",
    "ActivityDay",
    "ActivityItem",
    "ActivityKind",
    "Heartbeat",
    "HeartbeatState",
    "KillCriterion",
    "KillState",
    "KillTrigger",
    "ObservationMoney",
    "ObservationStats",
    "ObservationWindow",
    "PortfolioRange",
    "PortfolioSummary",
    "PositionDetail",
    "PositionRow",
    "ShadowTest",
    "ShadowTestStatus",
    "Tax",
    "TaxBand",
    "TaxEstimate",
    "TaxPending",
    "TimelineEvent",
    "WaterfallColumn",
    "tax_amount",
]

_ZERO = Decimal(0)


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
    """

    gross: Decimal
    fees: Decimal
    tax: Tax

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

    @property
    def entry_eur(self) -> Decimal:
        """Return the entry price in EUR."""
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
    """Progress verdict of the 90-day shadow test."""

    NOT_STARTED = "not started"
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


class KillCriterion(StrEnum):
    """The shadow-phase kill criteria of pre-registration v1 §10."""

    DRIFT_SUSTAINED = "drift_sustained"
    SHOCK_OVERLAY = "shock_overlay"
    SHADOW_SHARPE = "shadow_sharpe"

    @property
    def reveals_pnl(self) -> bool:
        """Return whether this criterion's progress count says something about P&L."""
        return self is KillCriterion.SHADOW_SHARPE


class KillState(StrEnum):
    """Whether a kill criterion is still watching or has stopped the shadow."""

    ARMED = "armed"
    FIRED = "fired"


@dataclass(frozen=True, slots=True)
class KillTrigger:
    """One §10 shadow-phase kill criterion as the engine last computed it.

    Attributes:
        criterion: Which criterion.
        state: Armed or fired.
        count: Progress towards the limit: days in breach for the drift and
            Sharpe criteria, fires in the last 30 days for the shock overlay.
    """

    criterion: KillCriterion
    state: KillState
    count: int = 0

    def __post_init__(self) -> None:
        """Reject a negative progress count."""
        if self.count < 0:
            msg = "kill trigger count must not be negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ObservationStats:
    """Execution and operations figures over one period of the window.

    Samples are raw, one per fill or decision, so every surface computes the
    same percentiles (see :mod:`hermes.application.console.observation`).

    Attributes:
        fills: Simulated fills.
        rejections: Orders the fill path rejected.
        drift_bps: Signed drift of each live-book fill from the §6.2 model
            price, in bps (positive = the fill cost more).
        slippage_bps: Signed slippage of each fill from the book mid, in bps.
        decision_latency_ms: Bar close to decision, per decision, in ms.
        data_gaps: Bars missing from the feed.
        late_bars: Bars that arrived after their scheduled check.
        uptime_pct: Share of the period the bar loop was up, percent.
        restarts: Process restarts.
    """

    fills: int
    rejections: int
    drift_bps: tuple[Decimal, ...]
    slippage_bps: tuple[Decimal, ...]
    decision_latency_ms: tuple[Decimal, ...]
    data_gaps: int
    late_bars: int
    uptime_pct: Decimal
    restarts: int

    @property
    def drift(self) -> Distribution | None:
        """Return p50, p95 and max of the absolute drift, as the gate reads it."""
        return summarize([abs(value) for value in self.drift_bps])

    @property
    def drift_verdict(self) -> DriftVerdict:
        """Return the drift verdict against the §7 gate."""
        return drift_verdict(self.drift_bps)

    @property
    def slippage(self) -> Distribution | None:
        """Return p50, p95 and max of the signed slippage."""
        return summarize(self.slippage_bps)

    @property
    def decision_latency(self) -> Distribution | None:
        """Return p50, p95 and max of the decision latency."""
        return summarize(self.decision_latency_ms)


@dataclass(frozen=True, slots=True)
class ObservationMoney:
    """Shadow results over the window; shown only where ``pnl_visible`` allows.

    Attributes:
        net: Net shadow P&L since day 1, EUR, after fees and estimated tax.
        sharpe: Annualised shadow Sharpe since day 1, or ``None`` while too
            short to compute.
    """

    net: Decimal
    sharpe: Decimal | None


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    """The 90-day shadow observation window (pre-registration v1 §7).

    Attributes:
        as_of: When the figures were read.
        test: Day N of the window and its verdict (the shadow test line).
        started_on: First day of the window, ``None`` before it starts.
        last_7_days: Figures over the last seven days.
        to_date: Figures since day 1.
        kill_triggers: The §10 shadow-phase criteria, empty before the start.
        money: Shadow results; P&L, so only operator surfaces render it.
    """

    as_of: datetime
    test: ShadowTest
    started_on: date | None
    last_7_days: ObservationStats
    to_date: ObservationStats
    kill_triggers: tuple[KillTrigger, ...]
    money: ObservationMoney | None = None

    @property
    def started(self) -> bool:
        """Return whether the window has a day 1."""
        return self.test.day is not None and self.test.status is not ShadowTestStatus.NOT_STARTED
