"""Build the channel-neutral :class:`Note` for every notifiable event.

One builder per event writes the text of every shape from the event's own
fields, using only the formatters in :mod:`hermes.application.messages.format`.
The channel modules (``telegram`` and ``slack``) never format a fact
themselves; they pick a shape from :data:`~hermes.application.messages.shapes.SHAPES`
and wrap the lines for their wire format.

Events with no builder (``LotClosed``, ``BarProcessed``) are not routed to any
channel, so asking for their note raises ``ValueError`` instead of sending an
empty push.

Aggregate blind (#319, ADR-012 ruling 1): no builder reads a profit, loss or
return figure. The plan-versus-actual lines show the plan and say the actual is
withheld until certification. The only phase-aware builder is the daily digest,
which adds the winners and losers counts once the phase is ``CERTIFIED``.

Reference: openspec/changes/144-notification-system/change.md (R-1 to R-4);
    docs/decisions/2026-10-04-notification-templates-v2.md.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

from hermes.application.messages.format import (
    GLYPH_DOWN,
    GLYPH_HALF,
    GLYPH_OK,
    GLYPH_STOP,
    GLYPH_UP,
    SEPARATOR,
    fmt_distance_pct,
    fmt_duration,
    fmt_money,
    fmt_pp,
    fmt_qty,
    fmt_quote_money,
    fmt_weight,
    fmt_when,
    fmt_when_utc,
    severity_glyph,
)
from hermes.application.messages.reporting_phase import (
    OUTCOME_WITHHELD_NOTICE,
    ReportingPhase,
)
from hermes.application.messages.shapes import Family, Note
from hermes.domain.events.anomaly_detected import AnomalyDetected
from hermes.domain.events.balance_low import BalanceLow
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.daily_trade_digest import DailyTradeDigest
from hermes.domain.events.rebalance_triggered import RebalanceTriggered
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.strategy_paused import StrategyPaused
from hermes.domain.events.strategy_resumed import StrategyResumed
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.events.trade_rejected import TradeRejected
from hermes.domain.strategy.signal import SignalKind
from hermes.domain.value_objects.currency import Currency

__all__ = ["ACTUAL_WITHHELD", "build_note"]

ACTUAL_WITHHELD: Final = "Actual: withheld until certification"
"""The actual half of a plan-versus-actual message before certification."""

_PRICE_UNKNOWN: Final = "price n/a"
_HUNDRED: Final = Decimal(100)
_ZERO: Final = Decimal(0)


def _join(*parts: str) -> str:
    """Join the parts of one line."""
    return SEPARATOR.join(parts)


def _rate_for(currency: Currency, quote: Currency, eur_rate: Decimal | None) -> Decimal | None:
    """Return the rate only when ``currency`` is the quote the rate was looked up for."""
    return eur_rate if currency is quote else None


def _signal_note(event: SignalEmitted, eur_rate: Decimal | None) -> Note:
    """Build the note for a signal: the plan, never the outcome."""
    coin = event.pair.base.value
    quote = event.pair.quote
    entering = event.kind is SignalKind.ENTRY
    glyph = GLYPH_UP if entering else GLYPH_DOWN
    side = event.side.value
    stop = fmt_quote_money(event.stop_price, quote, eur_rate)
    entry = event.entry_price
    price = fmt_quote_money(entry, quote, eur_rate) if entry is not None else _PRICE_UNKNOWN
    when = fmt_when(event.occurred_at)
    when_utc = fmt_when_utc(event.occurred_at)
    footer = (str(event.strategy_id),)
    distance = ""
    atr_multiple = ""
    if entry is not None and entry > _ZERO:
        distance = fmt_distance_pct((event.stop_price - entry) / entry * _HUNDRED)
    if entry is not None and event.atr_context > _ZERO:
        atr_multiple = f"{abs(entry - event.stop_price) / event.atr_context:.1f} ATR"
    if entering:
        stop_compact = f"stop {stop} ({distance})" if distance else f"stop {stop}"
        stop_verbose = f"Plan: stop {stop}"
        if distance and atr_multiple:
            stop_verbose += f" ({distance}, {atr_multiple})"
        elif distance:
            stop_verbose += f" ({distance})"
        return Note(
            minimal=f"{glyph} {side.capitalize()} {coin} {price} stop {stop} ({when})",
            compact=(
                f"{glyph} Entry {side} {coin}{SEPARATOR}{price}",
                _join(stop_compact, when),
            ),
            verbose=(
                _join(f"{glyph} Entry {side} {coin}", price, when_utc),
                stop_verbose,
                ACTUAL_WITHHELD,
            ),
            footer=footer,
        )
    reason = f" ({event.exit_reason})" if event.exit_reason else ""
    entry_ref = f"entry ref {price}"
    return Note(
        minimal=f"{glyph} Exit {side} {coin}{reason} ({when})",
        compact=(
            f"{glyph} Exit {side} {coin}{reason}",
            _join(entry_ref, f"stop {stop}", when),
        ),
        verbose=(
            _join(f"{glyph} Exit {side} {coin}{reason}", when_utc),
            _join(f"Plan: {entry_ref}", f"stop {stop}"),
            ACTUAL_WITHHELD,
        ),
        footer=footer,
    )


def _trade_executed_note(event: TradeExecuted, eur_rate: Decimal | None) -> Note:
    """Build the note for a fill escalated as a large position."""
    coin = event.pair.base.value
    glyph = GLYPH_UP if event.side is TradeSide.BUY else GLYPH_DOWN
    quote = event.pair.quote
    price = fmt_quote_money(event.price.amount, quote, eur_rate)
    qty = fmt_qty(event.qty.amount, event.qty.asset)
    fee_currency = event.fee.currency
    fee_rate = _rate_for(fee_currency, quote, eur_rate)
    fee = f"fee {fmt_quote_money(event.fee.amount, fee_currency, fee_rate)}"
    value = fmt_quote_money(event.qty.amount * event.price.amount, quote, eur_rate)
    notional = f"value {value}"
    headline = f"{glyph} Position large{SEPARATOR}{event.side.value} {coin}{SEPARATOR}{price}"
    return Note(
        minimal=f"{glyph} {event.side.value} {qty} @ {price}",
        compact=(headline, _join(qty, fee, fmt_when(event.occurred_at))),
        verbose=(
            _join(
                f"{glyph} Position large",
                f"{event.side.value} {coin}",
                price,
                fmt_when_utc(event.occurred_at),
            ),
            _join(qty, notional, fee),
        ),
        footer=(str(event.strategy_id),),
    )


def _trade_rejected_note(event: TradeRejected) -> Note:
    """Build the note for a rejected order."""
    coin = event.pair.base.value
    what = f"{event.side.value} {coin}"
    why = f"{event.exchange}: {event.rejection_reason}"
    return Note(
        minimal=f"{GLYPH_STOP} {what} rejected by {why}",
        compact=(
            f"{GLYPH_STOP} Order rejected{SEPARATOR}{what}",
            _join(why, fmt_when(event.attempted_at)),
        ),
        verbose=(
            _join(f"{GLYPH_STOP} Order rejected", what, fmt_when_utc(event.attempted_at)),
            why,
            f"Pair: {event.pair}",
        ),
    )


def _strategy_paused_note(event: StrategyPaused) -> Note:
    """Build the note for a paused strategy."""
    glyph = severity_glyph(event.severity)
    name = event.strategy_id
    return Note(
        minimal=f"{glyph} Strategy paused {name}: {event.reason}",
        compact=(
            _join(f"{glyph} Strategy paused", name, fmt_when(event.paused_at)),
            event.reason,
        ),
        verbose=(
            _join(f"{glyph} Strategy paused", name, fmt_when_utc(event.paused_at)),
            f"Severity: {event.severity.value}",
            event.reason,
        ),
        footer=("review the strategy",),
    )


def _strategy_resumed_note(event: StrategyResumed) -> Note:
    """Build the note that mirrors a pause: back after N min."""
    name = event.strategy_id
    paused_for = fmt_duration(event.pause_duration_seconds)
    return Note(
        minimal=f"{GLYPH_OK} Strategy resumed {name} after {paused_for}",
        compact=(
            _join(f"{GLYPH_OK} Strategy resumed", name, fmt_when(event.resumed_at)),
            f"back after {paused_for}",
        ),
        verbose=(
            _join(f"{GLYPH_OK} Strategy resumed", name, fmt_when_utc(event.resumed_at)),
            f"Paused for {paused_for}",
        ),
    )


def _anomaly_note(event: AnomalyDetected) -> Note:
    """Build the note for a detected anomaly."""
    glyph = severity_glyph(event.severity)
    return Note(
        minimal=f"{glyph} Anomaly in {event.source}: {event.reason}",
        compact=(
            _join(f"{glyph} Anomaly", event.source, fmt_when(event.occurred_at)),
            event.reason,
        ),
        verbose=(
            _join(f"{glyph} Anomaly", event.source, fmt_when_utc(event.occurred_at)),
            f"Severity: {event.severity.value}",
            event.reason,
        ),
    )


def _balance_low_note(event: BalanceLow) -> Note:
    """Build the note for a balance under its floor."""
    asset = event.asset.value
    current = fmt_money(event.current.amount, event.asset)
    threshold = fmt_money(event.threshold.amount, event.asset)
    return Note(
        minimal=f"{GLYPH_STOP} Balance low {asset}: {current} left (threshold {threshold})",
        compact=(
            _join(f"{GLYPH_STOP} Balance low", asset, fmt_when(event.checked_at)),
            f"{current} left, threshold {threshold}",
        ),
        verbose=(
            _join(f"{GLYPH_STOP} Balance low", asset, fmt_when_utc(event.checked_at)),
            f"Current: {current}",
            f"Threshold: {threshold}",
        ),
        footer=("top up",),
    )


def _rebalance_note(event: RebalanceTriggered) -> Note:
    """Build the note for the rebalance heads-up (no trade has happened yet)."""
    coin = event.pair.base.value
    current = fmt_weight(event.current_weight.value)
    target = fmt_weight(event.target_weight.value)
    drift = fmt_pp(abs(event.current_weight.value - event.target_weight.value))
    band = fmt_pp(event.band.value)
    gap = f"drift {drift} (band {band})"
    return Note(
        minimal=f"{GLYPH_HALF} Rebalance heads-up {coin}: {gap}",
        compact=(
            _join(f"{GLYPH_HALF} Rebalance heads-up", coin, fmt_when(event.occurred_at)),
            _join(f"{current} vs target {target}", gap),
        ),
        verbose=(
            _join(f"{GLYPH_HALF} Rebalance heads-up", coin, fmt_when_utc(event.occurred_at)),
            _join(f"Weight {current}", f"target {target}", gap),
        ),
        footer=("no trade yet",),
    )


def _digest_note(event: DailyTradeDigest, phase: ReportingPhase) -> Note:
    """Build the daily digest note; outcome counts need a certified phase."""
    day = event.day.isoformat()
    filled = f"{event.filled}/{event.total_trades} filled"
    notional = fmt_money(event.total_notional.amount, event.total_notional.currency)
    traded = f"{notional} traded"
    activity = _join(filled, f"{event.rejected} rejected", traded)
    if phase.shows_outcomes:
        tail = (
            f"Top winners {len(event.top_winners)}"
            f"{SEPARATOR}top losers {len(event.top_losers)} (CSV attached)",
        )
    else:
        tail = (OUTCOME_WITHHELD_NOTICE,)
    return Note(
        minimal=_join(f"{GLYPH_OK} Daily digest {day}", filled, traded),
        compact=(f"{GLYPH_OK} Daily digest{SEPARATOR}{day}", activity, *tail),
        verbose=(
            _join(f"{GLYPH_OK} Daily digest", day, fmt_when_utc(event.occurred_at)),
            f"Trades {event.total_trades}: filled {event.filled}, rejected {event.rejected}",
            f"Traded: {notional}",
            *tail,
        ),
    )


def build_note(
    event: DomainEvent, phase: ReportingPhase, eur_rate: Decimal | None = None
) -> tuple[Family, Note]:
    """Return the family and the note of ``event``.

    Args:
        event: A concrete domain event.
        phase: Reporting phase. Only the daily digest reads it.
        eur_rate: Units of the event's quote currency per one euro, or
            ``None``. With a rate, signal and fill prices read in EUR; without
            one they keep the quote currency, so the fallback is visible.

    Returns:
        The family that picks the shape, and the text of every shape.

    Raises:
        ValueError: ``event`` has no builder, so it is not notified.
    """
    match event:
        case SignalEmitted():
            built = (Family.TRADE_LIFECYCLE, _signal_note(event, eur_rate))
        case TradeExecuted():
            built = (Family.TRADE_LIFECYCLE, _trade_executed_note(event, eur_rate))
        case TradeRejected():
            built = (Family.TRADE_LIFECYCLE, _trade_rejected_note(event))
        case StrategyPaused():
            built = (Family.HEALTH_OPS, _strategy_paused_note(event))
        case StrategyResumed():
            built = (Family.HEALTH_OPS, _strategy_resumed_note(event))
        case AnomalyDetected():
            built = (Family.HEALTH_OPS, _anomaly_note(event))
        case BalanceLow():
            built = (Family.HEALTH_OPS, _balance_low_note(event))
        case RebalanceTriggered():
            built = (Family.REPORTS, _rebalance_note(event))
        case DailyTradeDigest():
            built = (Family.REPORTS, _digest_note(event, phase))
        case _:
            raise ValueError(
                f"No notification note for {type(event).__qualname__} - "
                f"add a builder in src/hermes/application/messages/notes.py."
            )
    return built
