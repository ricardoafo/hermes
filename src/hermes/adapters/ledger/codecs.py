"""Row and payload codecs shared by the ledger writer and its read-only readers."""

import dataclasses
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from hermes.adapters.ledger._cells import decimal_cell, optional_decimal_cell, time_cell
from hermes.domain.errors import LedgerError
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.fill_quality_recorded import (
    BookSource,
    FillQualityRecorded,
)
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.signal_notified import SignalNotified
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.lots.eur_fifo import EurTrade
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind, SignalSide
from hermes.domain.trading.trade_record import TradeEur
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.ledger import EurTradeRow


def live_bar_from_row(pair: Pair, interval: KlineInterval, row: tuple[Any, ...]) -> Bar:
    """Rebuild a :class:`Bar` from a ``live_bars`` row.

    Raises:
        LedgerError: A cell is not a readable time or number.
    """
    open_time, close_time, open_, high, low, close, volume = row
    try:
        return Bar(
            pair=pair,
            interval=interval,
            open_time=time_cell(open_time),
            close_time=time_cell(close_time),
            open=decimal_cell(open_),
            high=decimal_cell(high),
            low=decimal_cell(low),
            close=decimal_cell(close),
            volume=decimal_cell(volume),
        )
    except (ValueError, TypeError) as exc:
        raise LedgerError(msg=f"unreadable live bar: {exc}") from exc


def iso_utc(dt: datetime) -> str:
    """Format a tz-aware :class:`datetime` as an ISO-8601 UTC string.

    Args:
        dt: Must be timezone-aware — the adapter refuses naive input to
            catch bugs at the boundary rather than silently persisting
            wrong-zone timestamps.

    Returns:
        ISO-8601 string (e.g. ``"2026-08-23T14:22:03+00:00"``).

    Raises:
        LedgerError: When ``dt`` is naive.
    """
    if dt.tzinfo is None:
        raise LedgerError(msg=f"iso_utc: refusing to serialize naive datetime {dt!r}")
    return dt.isoformat()


def event_to_json(event: DomainEvent) -> str:
    """Serialise a :class:`DomainEvent` to a JSON string.

    Handles :class:`Decimal`, :class:`datetime`, :class:`Enum` (StrEnum
    currencies, TradeSide), and nested frozen dataclasses (Money, Pair,
    Price, Quantity, LotId, …) by walking ``dataclasses.asdict`` and
    stringifying leaves the JSON encoder cannot handle natively.

    Args:
        event: Any concrete :class:`DomainEvent`.

    Returns:
        A JSON string preserving the event's field structure.
    """
    payload = dataclasses.asdict(event)
    return json.dumps(payload, default=json_default, sort_keys=True)


def json_default(value: Any) -> Any:
    """Fallback for ``json.dumps`` — handle Decimal, datetime, Enum."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__} to JSON: {value!r}")


def decimal_text(value: Decimal) -> str:
    """Format a Decimal as plain positional text (no exponent)."""
    return format(value, "f")


def optional_decimal_text(value: Decimal | None) -> str | None:
    """Format an optional Decimal, keeping ``None`` as SQL NULL."""
    return None if value is None else decimal_text(value)


def eur_columns(eur: TradeEur) -> tuple[str, str, str, str, str]:
    """Return ``eur_rate, eur_rate_date, eur_source, price_eur, fee_eur`` as stored."""
    return (
        decimal_text(eur.pricing.rate),
        eur.pricing.rate_date.isoformat(),
        eur.pricing.source,
        decimal_text(eur.price_eur),
        decimal_text(eur.fee_eur),
    )


def eur_row(asset: Currency, trade: EurTrade, *, quote_leg: bool) -> EurTradeRow:
    """Return one replay step of ``asset`` as the tax source port's row."""
    return EurTradeRow(
        ref=trade.ref,
        asset=asset,
        side=trade.side,
        qty=trade.qty,
        executed_at=trade.executed_at,
        price_eur=trade.price_eur,
        fee_eur=trade.fee_eur,
        quote_leg=quote_leg,
    )


def optional_decimal(raw: object) -> Decimal | None:
    """Parse a stored decimal string, keeping ``None`` as ``None``."""
    return optional_decimal_cell(raw)


def signal_from_payload(payload: dict[str, Any]) -> SignalEmitted:
    """Rehydrate a :class:`SignalEmitted` from its stored JSON payload.

    Args:
        payload: The decoded ``payload_json`` of a ``SignalEmitted`` row.

    Returns:
        The event with every stored field restored.

    Raises:
        KeyError: A field is missing from the payload.
        ValueError: A field holds a value of the wrong shape.
    """
    return SignalEmitted(
        occurred_at=time_cell(payload["occurred_at"]),
        correlation_id=payload["correlation_id"],
        event_seq=int(payload["event_seq"]),
        strategy_id=StrategyId(payload["strategy_id"]["value"]),
        pair=Pair(
            base=Currency(payload["pair"]["base"]),
            quote=Currency(payload["pair"]["quote"]),
        ),
        kind=SignalKind(payload["kind"]),
        side=SignalSide(payload["side"]),
        entry_price=optional_decimal(payload["entry_price"]),
        stop_price=decimal_cell(payload["stop_price"]),
        atr_context=decimal_cell(payload["atr_context"]),
        exit_reason=payload["exit_reason"],
        f_star_estimate=optional_decimal(payload["f_star_estimate"]),
    )


def notified_from_payload(payload: dict[str, Any]) -> SignalNotified:
    """Rehydrate a :class:`SignalNotified` from its stored JSON payload.

    Raises:
        KeyError: A field is missing from the payload.
        ValueError: A field holds a value of the wrong shape.
    """
    return SignalNotified(
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        correlation_id=payload["correlation_id"],
        event_seq=int(payload["event_seq"]),
        delivered=tuple(str(name) for name in payload["delivered"]),
        failed=tuple(str(name) for name in payload["failed"]),
    )


def trades_from_rows(rows: list[Any], *, op: str) -> tuple[TradeExecuted, ...]:
    """Rehydrate ``(event_schema_version, payload_json)`` rows into events."""
    trades: list[TradeExecuted] = []
    for version, payload_json in rows:
        if version != TradeExecuted.__event_schema_version__:
            raise LedgerError(msg=f"{op}: unsupported TradeExecuted schema version {version}")
        try:
            trades.append(trade_from_payload(json.loads(payload_json)))
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"{op}: unreadable payload: {exc}") from exc
    return tuple(trades)


def trade_from_payload(payload: dict[str, Any]) -> TradeExecuted:
    """Rehydrate a :class:`TradeExecuted` from its stored JSON payload.

    Args:
        payload: The decoded ``payload_json`` of a ``TradeExecuted`` row.

    Returns:
        The event with every stored field restored.

    Raises:
        KeyError: A field is missing from the payload.
        ValueError: A field holds a value of the wrong shape.
    """
    pair = Pair(
        base=Currency(payload["pair"]["base"]),
        quote=Currency(payload["pair"]["quote"]),
    )
    return TradeExecuted(
        occurred_at=time_cell(payload["occurred_at"]),
        correlation_id=payload["correlation_id"],
        event_seq=int(payload["event_seq"]),
        pair=pair,
        side=TradeSide(payload["side"]),
        qty=Quantity(amount=decimal_cell(payload["qty"]["amount"]), asset=pair.base),
        price=Price(amount=decimal_cell(payload["price"]["amount"]), pair=pair),
        fee=Money(
            amount=decimal_cell(payload["fee"]["amount"]),
            currency=Currency(payload["fee"]["currency"]),
        ),
        strategy_id=StrategyId(payload["strategy_id"]["value"]),
    )


def fill_quality_from_payload(payload: dict[str, Any]) -> FillQualityRecorded:
    """Rehydrate a :class:`FillQualityRecorded` from its stored JSON payload.

    Args:
        payload: The decoded ``payload_json`` of a ``FillQualityRecorded`` row.

    Returns:
        The event with every stored field restored.

    Raises:
        KeyError: A field is missing from the payload.
        ValueError: A field holds a value of the wrong shape.
    """
    return FillQualityRecorded(
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        correlation_id=payload["correlation_id"],
        event_seq=int(payload["event_seq"]),
        strategy_id=StrategyId(payload["strategy_id"]["value"]),
        pair=Pair(
            base=Currency(payload["pair"]["base"]),
            quote=Currency(payload["pair"]["quote"]),
        ),
        side=TradeSide(payload["side"]),
        qty=Decimal(str(payload["qty"])),
        book_source=BookSource(payload["book_source"]),
        book_time=datetime.fromisoformat(payload["book_time"]),
        fill_price=Decimal(str(payload["fill_price"])),
        model_price=optional_decimal(payload["model_price"]),
        drift_bps=optional_decimal(payload["drift_bps"]),
        book_mid=optional_decimal(payload["book_mid"]),
        slippage=optional_decimal(payload["slippage"]),
        arrival_price=optional_decimal(payload["arrival_price"]),
        delta_ms=None if payload["delta_ms"] is None else int(payload["delta_ms"]),
    )
