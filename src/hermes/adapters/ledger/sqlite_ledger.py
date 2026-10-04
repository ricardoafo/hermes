"""SQLite ledger adapter (``LedgerPort`` + ``TradeLedgerPort``) — one transaction per write.

Concrete adapter for the FIFO tax-lot ledger. Uses stdlib ``sqlite3``
(no ORM — ADR-003 rules out heavyweight tooling for the single-writer
workload). Every mutating method groups its row insert and the accompanying
event append into ONE ``BEGIN ... COMMIT`` boundary so invariant I-06 is
satisfied by construction.

Serialization of :class:`DomainEvent` values is done via ``dataclasses.asdict``
plus a small canonicaliser that stringifies :class:`Decimal`, :class:`datetime`,
and value-object wrappers into JSON-safe primitives. The stored payload plus
the ``event_schema_version`` column let a future upcaster chain
(code-conventions §10) reconstruct the event.

Trade rows (:meth:`SqliteLedger.record_trade`) carry EUR values or a pending
status. Lots and consumptions linked to trades are a projection: every trade
write replays the asset's trades through
:func:`~hermes.domain.lots.eur_fifo.replay_fifo_eur` and rewrites their EUR
values, fees included, inside the same transaction (change
``trade-eur-values`` D-6).

Reference: ADR-003 §00 (I-06), §03 (persistence schema),
code-conventions.md §6 (Decimal ledger values), §9 (UTC timestamps).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from returns.result import Failure

from hermes.adapters.ledger.migration_runner import apply_migrations
from hermes.domain.errors import LedgerError
from hermes.domain.events.base import DomainEvent
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.events.trade_executed import TradeExecuted, TradeSide
from hermes.domain.fx.eur_pricing import EurStatus
from hermes.domain.lots.eur_fifo import EurTrade, acquisition_value, replay_fifo_eur
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.market_data.bar import Bar
from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.strategy.signal import SignalKind, SignalSide
from hermes.domain.trading.order import OrderSide
from hermes.domain.trading.trade_record import TradeEur, TradeRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.price import Price
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure.time import now_utc
from hermes.ports.ledger import PendingTrade

__all__ = ["SqliteLedger"]


class SqliteLedger:
    """SQLite implementation of :class:`~hermes.ports.ledger.LedgerPort` and
    :class:`~hermes.ports.ledger.TradeLedgerPort`.

    The adapter owns its :class:`sqlite3.Connection`. Callers pass a
    pre-opened connection at construction time so tests can inject
    ``sqlite3.connect(":memory:")`` and production can inject a WAL-mode
    file connection.

    Threading: not thread-safe (matches ADR-003 §03 single-writer model).
    The application uses one process, one writer at a time.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Bind to a caller-owned SQLite connection and apply pending migrations.

        Args:
            connection: An open :class:`sqlite3.Connection`. The caller
                is responsible for closing it. The adapter runs
                :func:`apply_migrations` immediately so the ledger schema
                is present before the first write.
        """
        self._conn = connection
        # isolation_level=None would put us in autocommit and break the
        # I-06 grouping. The default (BEGIN implicit on first DML) is what
        # every write method relies on — an explicit `commit()` at the end
        # of the batch marks the single transaction boundary.
        apply_migrations(self._conn)

    # ------------------------------------------------------------------
    # LedgerPort — write methods (each is ONE transaction)
    # ------------------------------------------------------------------

    def open_lot(self, lot: Lot, event: DomainEvent) -> None:
        """Insert ``lot`` and append ``event`` inside a single transaction.

        The two INSERTs share ONE explicit ``BEGIN…COMMIT`` boundary. On
        error the transaction is rolled back so neither row lands in the
        ledger (invariant I-06).

        Args:
            lot: Freshly-opened :class:`Lot` (``remaining == original``).
            event: The BUY-side :class:`TradeExecuted` that opened it.

        Raises:
            LedgerError: On any SQLite failure.
        """
        try:
            self._insert_lot(lot)
            self._insert_event(event)
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise LedgerError(msg=f"open_lot failed: {exc}") from exc

    def consume_lot(
        self,
        consumptions: tuple[LotConsumption, ...],
        updated_lots: tuple[Lot, ...],
        event: DomainEvent,
    ) -> None:
        """Persist consumptions + updated remainders + event atomically.

        Args:
            consumptions: Ordered per :func:`select_lots_fifo` output.
            updated_lots: Same order — lot with reduced ``remaining_qty``.
            event: The SELL-side :class:`TradeExecuted` driving the draw.

        Raises:
            LedgerError: On any SQLite failure or when the arg lengths
                disagree (a programmer bug that must not silently pass).
        """
        if len(consumptions) != len(updated_lots):
            raise LedgerError(
                msg=(
                    f"consume_lot: consumptions ({len(consumptions)}) and "
                    f"updated_lots ({len(updated_lots)}) must have equal length"
                )
            )
        try:
            for consumption, updated in zip(consumptions, updated_lots, strict=True):
                self._insert_consumption(consumption)
                self._update_lot_remaining(updated)
            self._insert_event(event)
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise LedgerError(msg=f"consume_lot failed: {exc}") from exc

    def append_event(self, event: DomainEvent) -> None:
        """Append a standalone :class:`DomainEvent` to the ``events`` table.

        Args:
            event: Any concrete :class:`DomainEvent` subclass. Serialized
                via :func:`_event_to_json` and stored with its schema
                version so the upcaster chain can rehydrate it.

        Raises:
            LedgerError: On any SQLite failure.
        """
        try:
            self._insert_event(event)
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise LedgerError(msg=f"append_event failed: {exc}") from exc

    def record_live_bar(self, bar: Bar) -> Bar | None:
        """Store one closed live bar unless its key is already stored (#421).

        Data only: no strategy, signal or event reads this table.

        Args:
            bar: The closed bar the serve loop is handling.

        Returns:
            ``None`` when the bar was stored or an identical bar was already
            there; the stored bar when it differs (the stored one is kept).

        Raises:
            LedgerError: On any SQLite failure.
        """
        key = (
            bar.pair.base.value,
            bar.pair.quote.value,
            bar.interval.value,
            _iso_utc(bar.open_time),
        )
        try:
            inserted = self._conn.execute(
                "INSERT OR IGNORE INTO live_bars (pair_base, pair_quote, interval, open_time, "
                "close_time, open, high, low, close, volume, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    *key,
                    _iso_utc(bar.close_time),
                    str(bar.open),
                    str(bar.high),
                    str(bar.low),
                    str(bar.close),
                    str(bar.volume),
                    _iso_utc(datetime.now(UTC)),
                ),
            ).rowcount
            self._conn.commit()
            if inserted:
                return None
            row = self._conn.execute(
                "SELECT open_time, close_time, open, high, low, close, volume FROM live_bars "
                "WHERE pair_base = ? AND pair_quote = ? AND interval = ? AND open_time = ?",
                key,
            ).fetchone()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise LedgerError(msg=f"record_live_bar failed: {exc}") from exc
        stored = _row_to_bar(bar.pair, bar.interval, row)
        return None if stored == bar else stored

    def live_bars(
        self, pair: Pair, interval: KlineInterval, start: datetime, end: datetime
    ) -> tuple[Bar, ...]:
        """Return stored live bars with ``start <= open_time < end``, oldest first.

        Raises:
            LedgerError: On any SQLite failure.
        """
        try:
            rows = self._conn.execute(
                "SELECT open_time, close_time, open, high, low, close, volume FROM live_bars "
                "WHERE pair_base = ? AND pair_quote = ? AND interval = ? "
                "  AND open_time >= ? AND open_time < ? ORDER BY open_time",
                (pair.base.value, pair.quote.value, interval.value, _iso_utc(start), _iso_utc(end)),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"live_bars failed: {exc}") from exc
        return tuple(_row_to_bar(pair, interval, row) for row in rows)

    # ------------------------------------------------------------------
    # TradeLedgerPort — trade rows with EUR values
    # ------------------------------------------------------------------

    def record_trade(self, trade: TradeRecord, event: DomainEvent) -> int:
        """Store a trade, its lot effects and its event in one transaction.

        A BUY inserts the lot it opens, linked to the trade. The asset is then
        re-projected so lot remainders, consumptions and every EUR value
        follow from the trades (I-06 holds: one ``BEGIN…COMMIT``).

        Args:
            trade: The trade. ``trade.eur is None`` stores it pending.
            event: The ``TradeExecuted`` event for the trade.

        Returns:
            The new trade row id.

        Raises:
            LedgerError: On any SQLite failure or a SELL beyond the open
                quantity; the transaction is rolled back.
        """
        try:
            trade_id = self._insert_trade(trade)
            if trade.lot_id is not None:
                self._insert_trade_lot(trade, trade_id)
            self._reproject(trade.pair.base)
            self._insert_event(event)
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise LedgerError(msg=f"record_trade failed: {exc}") from exc
        except LedgerError:
            self._conn.rollback()
            raise
        return trade_id

    def list_pending_trades(self) -> tuple[PendingTrade, ...]:
        """Return every trade with ``eur_status = 'pending'``, oldest first.

        Returns:
            Pending trades sorted by fill time, then row id.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            rows = self._conn.execute(
                "SELECT id, pair_base, pair_quote, executed_at, price_amount, "
                "       fee_amount, fee_currency "
                "FROM trades WHERE eur_status = ?",
                (EurStatus.PENDING.value,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"list_pending_trades failed: {exc}") from exc
        pending = [
            PendingTrade(
                trade_id=int(row[0]),
                pair=Pair(base=Currency(row[1]), quote=Currency(row[2])),
                executed_at=datetime.fromisoformat(row[3]),
                price=Decimal(row[4]),
                fee=Money(amount=Decimal(row[5]), currency=Currency(row[6])),
            )
            for row in rows
        ]
        return tuple(sorted(pending, key=lambda p: (p.executed_at, p.trade_id)))

    def apply_trade_eur(self, trade_id: int, eur: TradeEur) -> None:
        """Store a trade's EUR values, mark it ``ok`` and re-project its asset.

        Args:
            trade_id: Row id of the trade.
            eur: The EUR values.

        Raises:
            LedgerError: On any SQLite failure or an unknown ``trade_id``;
                the transaction is rolled back.
        """
        try:
            row = self._conn.execute(
                "SELECT pair_base FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            if row is None:
                raise LedgerError(msg=f"apply_trade_eur: no trade with id {trade_id}")
            self._conn.execute(
                "UPDATE trades SET eur_rate = ?, eur_rate_date = ?, eur_source = ?, "
                "       price_eur = ?, fee_eur = ?, eur_status = ? "
                "WHERE id = ?",
                (*_eur_columns(eur), EurStatus.OK.value, trade_id),
            )
            self._reproject(Currency(row[0]))
            self._conn.commit()
        except sqlite3.Error as exc:
            self._conn.rollback()
            raise LedgerError(msg=f"apply_trade_eur failed: {exc}") from exc
        except LedgerError:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------
    # LedgerPort — read methods
    # ------------------------------------------------------------------

    def list_open_lots(self, asset: Currency) -> tuple[Lot, ...]:
        """Return every open lot for ``asset``, FIFO-ordered.

        Args:
            asset: Base currency filter.

        Returns:
            Tuple of :class:`Lot` sorted by ``opened_at`` asc, then
            ``lot_id`` asc — the shape :func:`select_lots_fifo` expects.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            rows = self._conn.execute(
                "SELECT lot_id, asset, original_qty, remaining_qty, "
                "       cost_basis_per_unit, strategy_id, opened_at, "
                "       cost_basis_native_per_unit, native_currency "
                "FROM lots "
                "WHERE asset = ? AND CAST(remaining_qty AS REAL) > 0 "
                "ORDER BY opened_at ASC, lot_id ASC",
                (asset.value,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"list_open_lots failed: {exc}") from exc

        return tuple(self._row_to_lot(row) for row in rows)

    def has_signal_event(self, event: SignalEmitted) -> bool:
        """Return whether a ``SignalEmitted`` row matches ``event``.

        Matches on strategy, pair, kind and ``occurred_at``.

        Args:
            event: The signal event to look up.

        Returns:
            ``True`` when a matching row exists.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            row = self._conn.execute(
                "SELECT 1 FROM events "
                "WHERE event_type = 'SignalEmitted' AND occurred_at = ? "
                "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
                "  AND json_extract(payload_json, '$.pair.base') = ? "
                "  AND json_extract(payload_json, '$.pair.quote') = ? "
                "  AND json_extract(payload_json, '$.kind') = ? "
                "LIMIT 1",
                (
                    _iso_utc(event.occurred_at),
                    event.strategy_id.value,
                    event.pair.base.value,
                    event.pair.quote.value,
                    event.kind.value,
                ),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"has_signal_event failed: {exc}") from exc
        return row is not None

    def last_processed_close(self, pair: Pair, interval: KlineInterval) -> datetime | None:
        """Return the newest ``BarProcessed`` close time for one stream.

        Args:
            pair: Trading pair.
            interval: Candle-duration bucket.

        Returns:
            The newest ``occurred_at``, or ``None`` when the stream has no row.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            row = self._conn.execute(
                "SELECT occurred_at FROM events "
                "WHERE event_type = 'BarProcessed' "
                "  AND json_extract(payload_json, '$.pair.base') = ? "
                "  AND json_extract(payload_json, '$.pair.quote') = ? "
                "  AND json_extract(payload_json, '$.interval') = ? "
                "ORDER BY occurred_at DESC LIMIT 1",
                (pair.base.value, pair.quote.value, interval.value),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"last_processed_close failed: {exc}") from exc
        return None if row is None else datetime.fromisoformat(row[0])

    def latest_signal_event(
        self, strategy_id: StrategyId, pair: Pair, *, at_or_before: datetime
    ) -> SignalEmitted | None:
        """Return the newest ``SignalEmitted`` of a strategy and pair up to a bound.

        Args:
            strategy_id: Strategy bucket that emitted the signal.
            pair: Trading pair.
            at_or_before: Inclusive upper bound on ``occurred_at``.

        Returns:
            The rehydrated event, or ``None`` when there is none.

        Raises:
            LedgerError: On any SQLite read failure or an unreadable payload.
        """
        try:
            row = self._conn.execute(
                "SELECT payload_json FROM events "
                "WHERE event_type = 'SignalEmitted' AND occurred_at <= ? "
                "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
                "  AND json_extract(payload_json, '$.pair.base') = ? "
                "  AND json_extract(payload_json, '$.pair.quote') = ? "
                "ORDER BY occurred_at DESC, id DESC LIMIT 1",
                (
                    _iso_utc(at_or_before),
                    strategy_id.value,
                    pair.base.value,
                    pair.quote.value,
                ),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"latest_signal_event failed: {exc}") from exc
        if row is None:
            return None
        try:
            return _signal_from_payload(json.loads(row[0]))
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"latest_signal_event: unreadable payload: {exc}") from exc

    def trade_events(self, strategy_id: StrategyId) -> tuple[TradeExecuted, ...]:
        """Return every ``TradeExecuted`` of a strategy, oldest first.

        Args:
            strategy_id: Strategy bucket the fills belong to.

        Returns:
            The rehydrated events ordered by ``occurred_at`` then row id.

        Raises:
            LedgerError: On any SQLite read failure, a row whose schema version
                is not 2, or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT event_schema_version, payload_json FROM events "
                "WHERE event_type = 'TradeExecuted' "
                "  AND json_extract(payload_json, '$.strategy_id.value') = ? "
                "ORDER BY occurred_at ASC, id ASC",
                (strategy_id.value,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"trade_events failed: {exc}") from exc
        return _trades_from_rows(rows, op="trade_events")

    def trades_between(self, start: datetime, end: datetime) -> tuple[TradeExecuted, ...]:
        """Return every ``TradeExecuted`` with ``start <= occurred_at <= end``, oldest first.

        All strategies are included: the recharacterization guard measures the
        taxpayer's activity, not one bucket's (ADR-002 §6, #135).

        Args:
            start: Window start (UTC, inclusive).
            end: Window end (UTC, inclusive).

        Returns:
            The rehydrated events ordered by ``occurred_at`` then row id.

        Raises:
            LedgerError: On any SQLite read failure, a row whose schema version
                is not 2, or an unreadable payload.
        """
        try:
            rows = self._conn.execute(
                "SELECT event_schema_version, payload_json FROM events "
                "WHERE event_type = 'TradeExecuted' AND occurred_at >= ? AND occurred_at <= ? "
                "ORDER BY occurred_at ASC, id ASC",
                (_iso_utc(start), _iso_utc(end)),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"trades_between failed: {exc}") from exc
        return _trades_from_rows(rows, op="trades_between")

    # ------------------------------------------------------------------
    # Private helpers — SQL and (de)serialisation
    # ------------------------------------------------------------------

    def _insert_trade(self, trade: TradeRecord) -> int:
        """Insert a trade row with its EUR columns or a pending status."""
        eur = (None, None, None, None, None) if trade.eur is None else _eur_columns(trade.eur)
        cursor = self._conn.execute(
            "INSERT INTO trades (correlation_id, pair_base, pair_quote, side, qty_amount, "
            "                    qty_asset, price_amount, fee_amount, fee_currency, "
            "                    executed_at, eur_rate, eur_rate_date, eur_source, "
            "                    price_eur, fee_eur, eur_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trade.correlation_id,
                trade.pair.base.value,
                trade.pair.quote.value,
                trade.side.value,
                _dec(trade.qty),
                trade.pair.base.value,
                _dec(trade.price),
                _dec(trade.fee.amount),
                trade.fee.currency.value,
                _iso_utc(trade.executed_at.astimezone(UTC)),
                *eur,
                trade.eur_status.value,
            ),
        )
        if cursor.lastrowid is None:
            raise LedgerError(msg="record_trade: INSERT INTO trades returned no row id")
        return cursor.lastrowid

    def _insert_trade_lot(self, trade: TradeRecord, trade_id: int) -> None:
        """Insert the lot a BUY opens; the projection fills its EUR basis."""
        if trade.lot_id is None:
            raise LedgerError(msg=f"record_trade: trade {trade_id} opens no lot")
        native_basis = (
            acquisition_value(price=trade.price, qty=trade.qty, fee=trade.fee.amount) / trade.qty
        )
        self._conn.execute(
            "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, "
            "                  cost_basis_per_unit, strategy_id, opened_at, "
            "                  cost_basis_native_per_unit, native_currency, trade_id) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
            (
                trade.lot_id.value,
                trade.pair.base.value,
                _dec(trade.qty),
                _dec(trade.qty),
                trade.strategy_id.value,
                _iso_utc(trade.executed_at.astimezone(UTC)),
                _dec(native_basis),
                trade.pair.quote.value,
                trade_id,
            ),
        )

    def _reproject(self, asset: Currency) -> None:
        """Rewrite the asset's trade-linked lots and consumptions from its trades.

        Raises:
            LedgerError: When the replay fails (a SELL beyond the open quantity).
            sqlite3.Error: On any SQL failure; the caller rolls back.
        """
        rows = self._conn.execute(
            "SELECT id, side, qty_amount, executed_at, price_eur, fee_eur FROM trades "
            "WHERE pair_base = ? "
            "  AND (side = 'SELL' OR id IN (SELECT trade_id FROM lots WHERE trade_id IS NOT NULL))",
            (asset.value,),
        ).fetchall()
        trades = sorted(
            (
                EurTrade(
                    ref=str(row[0]),
                    side=OrderSide(row[1]),
                    qty=Decimal(row[2]),
                    executed_at=datetime.fromisoformat(row[3]),
                    price_eur=_optional_decimal(row[4]),
                    fee_eur=_optional_decimal(row[5]),
                )
                for row in rows
            ),
            key=lambda t: (t.executed_at, int(t.ref)),
        )
        projected = replay_fifo_eur(asset, trades)
        if isinstance(projected, Failure):
            raise LedgerError(msg=f"FIFO replay of {asset} failed: {projected.failure()}")
        projection = projected.unwrap()

        lot_by_trade: dict[str, str] = {}
        for lot_value in projection.lots:
            (lot_id,) = self._conn.execute(
                "SELECT lot_id FROM lots WHERE trade_id = ?", (int(lot_value.buy_ref),)
            ).fetchone()
            lot_by_trade[lot_value.buy_ref] = lot_id
            self._conn.execute(
                "UPDATE lots SET remaining_qty = ?, cost_basis_per_unit = ? WHERE trade_id = ?",
                (
                    _dec(lot_value.remaining_qty),
                    _optional_dec(lot_value.cost_basis_per_unit_eur),
                    int(lot_value.buy_ref),
                ),
            )
        self._conn.execute(
            "DELETE FROM lot_consumptions WHERE trade_id IN "
            "(SELECT id FROM trades WHERE pair_base = ?)",
            (asset.value,),
        )
        for piece in projection.consumptions:
            self._conn.execute(
                "INSERT INTO lot_consumptions (lot_id, consumed_qty, consumed_asset, "
                "                              realized_gain_eur, consumed_at, proceeds_eur, "
                "                              cost_eur, fee_eur, trade_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    lot_by_trade[piece.buy_ref],
                    _dec(piece.qty),
                    asset.value,
                    _optional_dec(piece.gain_eur),
                    _iso_utc(piece.consumed_at),
                    _optional_dec(piece.proceeds_eur),
                    _optional_dec(piece.cost_eur),
                    _optional_dec(piece.fee_eur),
                    int(piece.sell_ref),
                ),
            )

    def _insert_lot(self, lot: Lot) -> None:
        """Insert a new lot row. Does NOT commit — caller controls the txn."""
        native = lot.cost_basis_native_per_unit
        self._conn.execute(
            "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, "
            "                  cost_basis_per_unit, strategy_id, opened_at, "
            "                  cost_basis_native_per_unit, native_currency) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                lot.lot_id.value,
                lot.asset.value,
                str(lot.original_qty.amount),
                str(lot.remaining_qty.amount),
                None if lot.cost_basis_per_unit is None else str(lot.cost_basis_per_unit.amount),
                lot.strategy_id.value,
                _iso_utc(lot.opened_at),
                None if native is None else str(native.amount),
                None if native is None else native.currency.value,
            ),
        )

    def _update_lot_remaining(self, lot: Lot) -> None:
        """Overwrite the ``remaining_qty`` for an existing lot row."""
        self._conn.execute(
            "UPDATE lots SET remaining_qty = ? WHERE lot_id = ?",
            (str(lot.remaining_qty.amount), lot.lot_id.value),
        )

    def _insert_consumption(self, consumption: LotConsumption) -> None:
        """Insert a lot_consumptions row for one SELL-to-lot draw."""
        self._conn.execute(
            "INSERT INTO lot_consumptions (lot_id, consumed_qty, consumed_asset, "
            "                              realized_gain_eur, consumed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                consumption.lot_id.value,
                str(consumption.consumed_qty.amount),
                consumption.consumed_qty.asset.value,
                str(consumption.realized_gain.amount),
                _iso_utc(consumption.consumed_at),
            ),
        )

    def _insert_event(self, event: DomainEvent) -> None:
        """Insert the event row alongside a fresh recorded_at timestamp."""
        payload_json = _event_to_json(event)
        self._conn.execute(
            "INSERT INTO events (event_type, event_schema_version, correlation_id, "
            "                    occurred_at, payload_json, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                type(event).__qualname__,
                event.__event_schema_version__,
                event.correlation_id,
                _iso_utc(event.occurred_at),
                payload_json,
                _iso_utc(now_utc()),
            ),
        )

    def _row_to_lot(self, row: tuple[Any, ...]) -> Lot:
        """Rehydrate a Lot from its SQLite row tuple."""
        (
            lot_id_str,
            asset_str,
            original,
            remaining,
            cost_basis,
            strategy_id_str,
            opened_at_str,
            native_basis,
            native_currency,
        ) = row
        asset = Currency(asset_str)
        return Lot(
            lot_id=LotId(value=lot_id_str),
            asset=asset,
            original_qty=Quantity(amount=Decimal(original), asset=asset),
            remaining_qty=Quantity(amount=Decimal(remaining), asset=asset),
            cost_basis_per_unit=(
                None
                if cost_basis is None
                else Money(amount=Decimal(cost_basis), currency=Currency.EUR)
            ),
            strategy_id=StrategyId(strategy_id_str),
            opened_at=datetime.fromisoformat(opened_at_str),
            cost_basis_native_per_unit=(
                None
                if native_basis is None or native_currency is None
                else Money(amount=Decimal(native_basis), currency=Currency(native_currency))
            ),
        )


# ---------------------------------------------------------------------------
# Module-level helpers — serialisation of domain values to JSON-safe primitives
# ---------------------------------------------------------------------------


def _row_to_bar(pair: Pair, interval: KlineInterval, row: tuple[Any, ...]) -> Bar:
    open_time, close_time, open_, high, low, close, volume = row
    return Bar(
        pair=pair,
        interval=interval,
        open_time=datetime.fromisoformat(open_time),
        close_time=datetime.fromisoformat(close_time),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def _iso_utc(dt: datetime) -> str:
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
        raise LedgerError(msg=f"_iso_utc: refusing to serialize naive datetime {dt!r}")
    return dt.isoformat()


def _event_to_json(event: DomainEvent) -> str:
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
    return json.dumps(payload, default=_json_default, sort_keys=True)


def _json_default(value: Any) -> Any:
    """Fallback for ``json.dumps`` — handle Decimal, datetime, Enum."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return _iso_utc(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__} to JSON: {value!r}")


def _dec(value: Decimal) -> str:
    """Format a Decimal as plain positional text (no exponent)."""
    return format(value, "f")


def _optional_dec(value: Decimal | None) -> str | None:
    """Format an optional Decimal, keeping ``None`` as SQL NULL."""
    return None if value is None else _dec(value)


def _eur_columns(eur: TradeEur) -> tuple[str, str, str, str, str]:
    """Return ``eur_rate, eur_rate_date, eur_source, price_eur, fee_eur`` as stored."""
    return (
        _dec(eur.pricing.rate),
        eur.pricing.rate_date.isoformat(),
        eur.pricing.source,
        _dec(eur.price_eur),
        _dec(eur.fee_eur),
    )


def _optional_decimal(raw: object) -> Decimal | None:
    """Parse a stored decimal string, keeping ``None`` as ``None``."""
    return None if raw is None else Decimal(str(raw))


def _signal_from_payload(payload: dict[str, Any]) -> SignalEmitted:
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
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        correlation_id=payload["correlation_id"],
        event_seq=int(payload["event_seq"]),
        strategy_id=StrategyId(payload["strategy_id"]["value"]),
        pair=Pair(
            base=Currency(payload["pair"]["base"]),
            quote=Currency(payload["pair"]["quote"]),
        ),
        kind=SignalKind(payload["kind"]),
        side=SignalSide(payload["side"]),
        entry_price=_optional_decimal(payload["entry_price"]),
        stop_price=Decimal(str(payload["stop_price"])),
        atr_context=Decimal(str(payload["atr_context"])),
        exit_reason=payload["exit_reason"],
        f_star_estimate=_optional_decimal(payload["f_star_estimate"]),
    )


def _trades_from_rows(rows: list[Any], *, op: str) -> tuple[TradeExecuted, ...]:
    """Rehydrate ``(event_schema_version, payload_json)`` rows into events."""
    trades: list[TradeExecuted] = []
    for version, payload_json in rows:
        if version != TradeExecuted.__event_schema_version__:
            raise LedgerError(msg=f"{op}: unsupported TradeExecuted schema version {version}")
        try:
            trades.append(_trade_from_payload(json.loads(payload_json)))
        except (KeyError, ValueError, TypeError) as exc:
            raise LedgerError(msg=f"{op}: unreadable payload: {exc}") from exc
    return tuple(trades)


def _trade_from_payload(payload: dict[str, Any]) -> TradeExecuted:
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
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        correlation_id=payload["correlation_id"],
        event_seq=int(payload["event_seq"]),
        pair=pair,
        side=TradeSide(payload["side"]),
        qty=Quantity(amount=Decimal(str(payload["qty"]["amount"])), asset=pair.base),
        price=Price(amount=Decimal(str(payload["price"]["amount"])), pair=pair),
        fee=Money(
            amount=Decimal(str(payload["fee"]["amount"])),
            currency=Currency(payload["fee"]["currency"]),
        ),
        strategy_id=StrategyId(payload["strategy_id"]["value"]),
    )
