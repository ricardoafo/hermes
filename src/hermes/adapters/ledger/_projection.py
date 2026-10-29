"""Lot projection: each trade write replays the asset's FIFO and rewrites its EUR values."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from returns.result import Failure

from hermes.adapters.ledger._ledger_core import _LedgerCore
from hermes.adapters.ledger.codecs import (
    decimal_text,
    eur_columns,
    iso_utc,
    optional_decimal,
    optional_decimal_text,
)
from hermes.domain.errors import LedgerError
from hermes.domain.lots.eur_fifo import EurTrade, acquisition_value, replay_fifo_eur
from hermes.domain.lots.lot import Lot
from hermes.domain.lots.lot_consumption import LotConsumption
from hermes.domain.lots.quote_leg import (
    CapitalSeed,
    LegTrade,
    base_leg_trade,
    net_base_qty,
    quote_leg_qty,
    quote_leg_trades,
)
from hermes.domain.trading.order import OrderSide
from hermes.domain.trading.trade_record import TradeRecord
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.lot_id import LotId
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId

_SEED_REF_PREFIX = "seed:"

_LEG_TRADES_SQL = {
    "base": (
        "SELECT id, pair_base, pair_quote, side, qty_amount, price_amount, fee_amount, "
        "       fee_currency, executed_at, eur_rate, price_eur, fee_eur "
        "FROM trades WHERE pair_base = ? AND (side = 'SELL' OR id IN "
        "(SELECT trade_id FROM lots WHERE trade_id IS NOT NULL AND asset = pair_base))"
    ),
    "quote": (
        "SELECT id, pair_base, pair_quote, side, qty_amount, price_amount, fee_amount, "
        "       fee_currency, executed_at, eur_rate, price_eur, fee_eur "
        "FROM trades WHERE pair_quote = ?"
    ),
    "id": (
        "SELECT id, pair_base, pair_quote, side, qty_amount, price_amount, fee_amount, "
        "       fee_currency, executed_at, eur_rate, price_eur, fee_eur "
        "FROM trades WHERE id = ?"
    ),
}

_DELETE_CONSUMPTIONS_SQL = {
    "base": (
        "DELETE FROM lot_consumptions WHERE consumed_asset = ? AND trade_id IN "
        "(SELECT id FROM trades WHERE pair_base = ?)"
    ),
    "quote": (
        "DELETE FROM lot_consumptions WHERE consumed_asset = ? AND trade_id IN "
        "(SELECT id FROM trades WHERE pair_quote = ?)"
    ),
}


class _LotProjection(_LedgerCore):
    """Lot projection: each trade write replays the asset's FIFO and rewrites its EUR values."""

    def _insert_trade(self, trade: TradeRecord) -> int:
        """Insert a trade row with its EUR columns or a pending status."""
        eur = (None, None, None, None, None) if trade.eur is None else eur_columns(trade.eur)
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
                decimal_text(trade.qty),
                trade.pair.base.value,
                decimal_text(trade.price),
                decimal_text(trade.fee.amount),
                trade.fee.currency.value,
                iso_utc(trade.executed_at.astimezone(UTC)),
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
        qty = net_base_qty(side=trade.side, qty=trade.qty, fee=trade.fee, base=trade.pair.base)
        quote_fee = trade.fee.amount if trade.fee.currency == trade.pair.quote else Decimal(0)
        native_basis = acquisition_value(price=trade.price, qty=trade.qty, fee=quote_fee) / qty
        self._conn.execute(
            "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, "
            "                  cost_basis_per_unit, strategy_id, opened_at, "
            "                  cost_basis_native_per_unit, native_currency, trade_id) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
            (
                trade.lot_id.value,
                trade.pair.base.value,
                decimal_text(qty),
                decimal_text(qty),
                trade.strategy_id.value,
                iso_utc(trade.executed_at.astimezone(UTC)),
                decimal_text(native_basis),
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
        legs = self._leg_trades("base", asset.value)
        trades = sorted(
            (base_leg_trade(leg) for leg in legs), key=lambda t: (t.executed_at, int(t.ref))
        )
        self._write_projection(asset, trades, "base")

    def _reproject_quote(self, quote: Currency) -> None:
        """Rewrite the quote leg's lots and consumptions once the currency has a seed.

        Without a seed the quote currency is not tracked and nothing changes
        (change ``405-usdt-fifo`` D-3). SELL trades without their quote lot
        (stored before the seed) gain one, held by the first seed's strategy.

        Raises:
            LedgerError: When a trade has no quote leg or the replay fails.
            sqlite3.Error: On any SQL failure; the caller rolls back.
        """
        seed_rows = self._conn.execute(
            "SELECT id, strategy_id, amount, seeded_at, eur_rate FROM capital_seeds "
            "WHERE asset = ? ORDER BY id",
            (quote.value,),
        ).fetchall()
        if not seed_rows:
            return
        for (trade_id,) in self._conn.execute(
            "SELECT id FROM trades WHERE pair_quote = ? AND side = 'SELL' "
            "  AND id NOT IN (SELECT trade_id FROM lots WHERE trade_id IS NOT NULL)",
            (quote.value,),
        ).fetchall():
            self._insert_quote_lot(int(trade_id), str(seed_rows[0][1]))
        derived = quote_leg_trades(
            quote, self._seeds_of(quote), self._leg_trades("quote", quote.value)
        )
        if isinstance(derived, Failure):
            raise LedgerError(msg=f"quote leg of {quote} failed: {derived.failure()}")
        self._write_projection(quote, list(derived.unwrap()), "quote")

    def _seeds_of(self, quote: Currency) -> dict[str, CapitalSeed]:
        """Return the seeds held in ``quote``, keyed by their replay ref."""
        return {
            f"{_SEED_REF_PREFIX}{row[0]}": CapitalSeed(
                strategy_id=StrategyId(row[1]),
                amount=Money(amount=Decimal(row[2]), currency=quote),
                seeded_at=datetime.fromisoformat(row[3]),
                eur_rate=optional_decimal(row[4]),
            )
            for row in self._conn.execute(
                "SELECT id, strategy_id, amount, seeded_at, eur_rate FROM capital_seeds "
                "WHERE asset = ? ORDER BY id",
                (quote.value,),
            ).fetchall()
        }

    def _leg_trades(self, selection: str, key: str | int) -> list[LegTrade]:
        """Read trades with what both legs need.

        ``selection`` is ``base`` (the replayed trades of base asset ``key``),
        ``quote`` (every trade quoted in ``key``) or ``id`` (trade ``key``).
        """
        rows = self._conn.execute(_LEG_TRADES_SQL[selection], (key,)).fetchall()
        return [
            LegTrade(
                ref=str(row[0]),
                pair=Pair(base=Currency(row[1]), quote=Currency(row[2])),
                side=OrderSide(row[3]),
                qty=Decimal(row[4]),
                price=Decimal(row[5]),
                fee=Money(amount=Decimal(row[6]), currency=Currency(row[7])),
                executed_at=datetime.fromisoformat(row[8]),
                eur_rate=optional_decimal(row[9]),
                price_eur=optional_decimal(row[10]),
                fee_eur=optional_decimal(row[11]),
            )
            for row in rows
        ]

    def _lot_of(self, ref: str) -> str:
        """Return the lot a replay ref opened: a seed's lot or a trade's lot."""
        if ref.startswith(_SEED_REF_PREFIX):
            row = self._conn.execute(
                "SELECT lot_id FROM capital_seeds WHERE id = ?",
                (int(ref.removeprefix(_SEED_REF_PREFIX)),),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT lot_id FROM lots WHERE trade_id = ?", (int(ref),)
            ).fetchone()
        if row is None:
            raise LedgerError(msg=f"FIFO replay: no lot for {ref}")
        return str(row[0])

    def _write_projection(self, asset: Currency, trades: list[EurTrade], leg: str) -> None:
        """Replay ``trades`` and rewrite the asset's lots and consumptions.

        Consumptions are replaced by asset and by the trades the asset is the
        ``leg`` of (``base`` or ``quote``), so one leg never deletes the other
        leg's rows.

        Raises:
            LedgerError: When the replay fails (a disposal beyond the open quantity).
            sqlite3.Error: On any SQL failure; the caller rolls back.
        """
        projected = replay_fifo_eur(asset, trades)
        if isinstance(projected, Failure):
            raise LedgerError(msg=f"FIFO replay of {asset} failed: {projected.failure()}")
        projection = projected.unwrap()

        lot_by_trade: dict[str, str] = {}
        for lot_value in projection.lots:
            lot_id = self._lot_of(lot_value.buy_ref)
            lot_by_trade[lot_value.buy_ref] = lot_id
            self._conn.execute(
                "UPDATE lots SET remaining_qty = ?, cost_basis_per_unit = ? WHERE lot_id = ?",
                (
                    decimal_text(lot_value.remaining_qty),
                    optional_decimal_text(lot_value.cost_basis_per_unit_eur),
                    lot_id,
                ),
            )
        self._conn.execute(_DELETE_CONSUMPTIONS_SQL[leg], (asset.value, asset.value))
        for piece in projection.consumptions:
            self._conn.execute(
                "INSERT INTO lot_consumptions (lot_id, consumed_qty, consumed_asset, "
                "                              realized_gain_eur, consumed_at, proceeds_eur, "
                "                              cost_eur, fee_eur, trade_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    lot_by_trade[piece.buy_ref],
                    decimal_text(piece.qty),
                    asset.value,
                    optional_decimal_text(piece.gain_eur),
                    iso_utc(piece.consumed_at),
                    optional_decimal_text(piece.proceeds_eur),
                    optional_decimal_text(piece.cost_eur),
                    optional_decimal_text(piece.fee_eur),
                    int(piece.sell_ref),
                ),
            )

    def _has_seed(self, asset: Currency) -> bool:
        """Return whether ``asset`` has a capital seed, so its quote leg is tracked."""
        row = self._conn.execute(
            "SELECT 1 FROM capital_seeds WHERE asset = ? LIMIT 1", (asset.value,)
        ).fetchone()
        return row is not None

    def _insert_quote_lot(self, trade_id: int, strategy_id: str) -> None:
        """Insert the quote-currency lot a SELL opens; the projection values it."""
        (leg,) = self._leg_trades("id", trade_id)
        moved = quote_leg_qty(leg)
        if isinstance(moved, Failure):
            raise LedgerError(msg=f"quote leg of trade {trade_id} failed: {moved.failure()}")
        self._insert_quote_lot_row(
            lot_id=LotId.new().value,
            asset=leg.pair.quote,
            qty=moved.unwrap(),
            strategy_id=strategy_id,
            opened_at=leg.executed_at,
            trade_id=trade_id,
        )

    def _insert_quote_lot_row(
        self,
        *,
        lot_id: str,
        asset: Currency,
        qty: Decimal,
        strategy_id: str,
        opened_at: datetime,
        trade_id: int | None,
    ) -> None:
        """Insert a lot of a quote currency; one unit costs one unit of itself."""
        self._conn.execute(
            "INSERT INTO lots (lot_id, asset, original_qty, remaining_qty, "
            "                  cost_basis_per_unit, strategy_id, opened_at, "
            "                  cost_basis_native_per_unit, native_currency, trade_id) "
            "VALUES (?, ?, ?, ?, NULL, ?, ?, '1', ?, ?)",
            (
                lot_id,
                asset.value,
                decimal_text(qty),
                decimal_text(qty),
                strategy_id,
                iso_utc(opened_at.astimezone(UTC)),
                asset.value,
                trade_id,
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
                iso_utc(lot.opened_at),
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
                iso_utc(consumption.consumed_at),
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
