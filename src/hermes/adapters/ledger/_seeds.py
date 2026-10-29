"""Capital seeds: the opening quote-currency lots and their EUR pricing."""

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

from hermes.adapters.ledger._projection import _LotProjection
from hermes.adapters.ledger.codecs import (
    decimal_text,
    iso_utc,
    optional_decimal,
    optional_decimal_text,
)
from hermes.domain.errors import LedgerError
from hermes.domain.events.base import DomainEvent
from hermes.domain.fx.eur_pricing import EurPricing, EurStatus
from hermes.domain.lots.quote_leg import (
    CapitalSeed,
)
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.ports.ledger import PendingSeed

_EVIDENCE_EVENTS = ("BarProcessed", "SignalEmitted", "TradeExecuted")


class _SeedStore(_LotProjection):
    """Capital seeds: the opening quote-currency lots and their EUR pricing."""

    def record_capital_seed(self, seed: CapitalSeed, event: DomainEvent) -> int:
        """Store a strategy's start capital as one quote-asset lot, with its event.

        The seed is stored EUR-pending. Its asset is re-projected in the same
        transaction: SELL trades already quoted in it gain their quote lots,
        held by the first seed's strategy.

        Args:
            seed: The seed, without an EUR rate.
            event: The ``ShadowCapitalSeeded`` event.

        Returns:
            The new seed row id.

        Raises:
            LedgerError: On any SQLite failure, a second seed for the strategy,
                a priced seed or a quote leg that cannot be replayed; the
                transaction is rolled back.
        """
        if seed.eur_rate is not None:
            raise LedgerError(msg="record_capital_seed: store the seed pending, then price it")
        asset = seed.amount.currency
        lot_id = LotId.new().value
        with self._write("record_capital_seed"):
            self._insert_quote_lot_row(
                lot_id=lot_id,
                asset=asset,
                qty=seed.amount.amount,
                strategy_id=seed.strategy_id.value,
                opened_at=seed.seeded_at,
                trade_id=None,
            )
            seed_id = self._conn.execute(
                "INSERT INTO capital_seeds (strategy_id, asset, amount, seeded_at, lot_id, "
                "                           eur_status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    seed.strategy_id.value,
                    asset.value,
                    decimal_text(seed.amount.amount),
                    iso_utc(seed.seeded_at.astimezone(UTC)),
                    lot_id,
                    EurStatus.PENDING.value,
                ),
            ).lastrowid
            if seed_id is None:
                raise LedgerError(msg="record_capital_seed: INSERT returned no row id")
            self._reproject_quote(asset)
            self._insert_event(event)
        return seed_id

    def capital_seed(self, strategy_id: StrategyId) -> CapitalSeed | None:
        """Return the strategy's seed, or ``None`` when it has none.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            row = self._conn.execute(
                "SELECT asset, amount, seeded_at, eur_rate FROM capital_seeds "
                "WHERE strategy_id = ?",
                (strategy_id.value,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"capital_seed failed: {exc}") from exc
        if row is None:
            return None
        return CapitalSeed(
            strategy_id=strategy_id,
            amount=Money(amount=Decimal(row[1]), currency=Currency(row[0])),
            seeded_at=datetime.fromisoformat(row[2]),
            eur_rate=optional_decimal(row[3]),
        )

    def seeded_strategies(self) -> tuple[StrategyId, ...]:
        """Return every strategy that has a capital seed in this ledger, in seed order.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            rows = self._conn.execute(
                "SELECT strategy_id FROM capital_seeds ORDER BY seeded_at, strategy_id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"seeded_strategies failed: {exc}") from exc
        return tuple(StrategyId(row[0]) for row in rows)

    def earliest_activity(self) -> datetime | None:
        """Return the earliest bar, signal or trade event time, or ``None`` without any.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            rows = self._conn.execute(
                "SELECT occurred_at FROM events WHERE event_type IN (?, ?, ?)",
                _EVIDENCE_EVENTS,
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"earliest_activity failed: {exc}") from exc
        return min((datetime.fromisoformat(row[0]) for row in rows), default=None)

    def list_pending_seeds(self) -> tuple[PendingSeed, ...]:
        """Return every seed whose EUR value is pending, oldest first.

        Raises:
            LedgerError: On any SQLite read failure.
        """
        try:
            rows = self._conn.execute(
                "SELECT id, asset, seeded_at FROM capital_seeds WHERE eur_status = ?",
                (EurStatus.PENDING.value,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise LedgerError(msg=f"list_pending_seeds failed: {exc}") from exc
        pending = [
            PendingSeed(
                seed_id=int(row[0]),
                asset=Currency(row[1]),
                seeded_at=datetime.fromisoformat(row[2]),
            )
            for row in rows
        ]
        return tuple(sorted(pending, key=lambda p: (p.seeded_at, p.seed_id)))

    def apply_seed_eur(self, seed_id: int, pricing: EurPricing) -> None:
        """Price a pending seed, mark it ``ok`` and re-project its asset.

        Args:
            seed_id: Row id of the seed.
            pricing: The EUR rate of the seed's asset on its seed date.

        Raises:
            LedgerError: On any SQLite failure, an unknown ``seed_id`` or a
                pricing for another currency; the transaction is rolled back.
        """
        with self._write("apply_seed_eur"):
            row = self._conn.execute(
                "SELECT asset, amount, strategy_id, seeded_at FROM capital_seeds WHERE id = ?",
                (seed_id,),
            ).fetchone()
            if row is None:
                raise LedgerError(msg=f"apply_seed_eur: no seed with id {seed_id}")
            asset = Currency(row[0])
            if pricing.quote != asset:
                raise LedgerError(
                    msg=f"apply_seed_eur: seed {seed_id} is held in {asset}, "
                    f"the pricing is for {pricing.quote}"
                )
            priced = CapitalSeed(
                strategy_id=StrategyId(row[2]),
                amount=Money(amount=Decimal(row[1]), currency=asset),
                seeded_at=datetime.fromisoformat(row[3]),
                eur_rate=pricing.rate,
            )
            self._conn.execute(
                "UPDATE capital_seeds SET eur_rate = ?, eur_rate_date = ?, eur_source = ?, "
                "       value_eur = ?, eur_status = ? "
                "WHERE id = ?",
                (
                    decimal_text(pricing.rate),
                    pricing.rate_date.isoformat(),
                    pricing.source,
                    optional_decimal_text(priced.value_eur),
                    EurStatus.OK.value,
                    seed_id,
                ),
            )
            self._reproject_quote(asset)
