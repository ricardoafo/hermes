"""SQLite :class:`LedgerPort` implementation — one transaction per write.

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

Reference: ADR-003 §00 (I-06), §03 (persistence schema),
code-conventions.md §6 (Decimal ledger values), §9 (UTC timestamps).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from hermes.adapters.ledger.migration_runner import apply_migrations
from hermes.domain.errors import LedgerError
from hermes.domain.events.base import DomainEvent
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.infrastructure.time import now_utc

__all__ = ["SqliteLedger"]


class SqliteLedger:
    """SQLite implementation of :class:`hermes.ports.ledger.LedgerPort`.

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
                "       cost_basis_per_unit, strategy_id, opened_at "
                "FROM lots "
                "WHERE asset = ? AND CAST(remaining_qty AS REAL) > 0 "
                "ORDER BY opened_at ASC, lot_id ASC",
                (asset.value,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"list_open_lots failed: {exc}") from exc

        return tuple(self._row_to_lot(row) for row in rows)

    # ------------------------------------------------------------------
    # Private helpers — SQL and (de)serialisation
    # ------------------------------------------------------------------

    def _insert_lot(self, lot: Lot) -> None:
        """Insert a new lot row. Does NOT commit — caller controls the txn."""
        self._conn.execute(
            "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, "
            "                  cost_basis_per_unit, strategy_id, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                lot.lot_id.value,
                lot.asset.value,
                str(lot.original_qty.amount),
                str(lot.remaining_qty.amount),
                str(lot.cost_basis_per_unit.amount),
                lot.strategy_id.value,
                _iso_utc(lot.opened_at),
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
        ) = row
        asset = Currency(asset_str)
        return Lot(
            lot_id=LotId(value=lot_id_str),
            asset=asset,
            original_qty=Quantity(amount=Decimal(original), asset=asset),
            remaining_qty=Quantity(amount=Decimal(remaining), asset=asset),
            cost_basis_per_unit=Money(amount=Decimal(cost_basis), currency=Currency.EUR),
            strategy_id=StrategyId(strategy_id_str),
            opened_at=datetime.fromisoformat(opened_at_str),
        )


# ---------------------------------------------------------------------------
# Module-level helpers — serialisation of domain values to JSON-safe primitives
# ---------------------------------------------------------------------------


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
