"""Read what a scenario run left in the ledger, as plain values to compare."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from tests.support.serve_loop import events


@dataclass(frozen=True)
class Signal:
    """One recorded ``SignalEmitted``.

    Attributes:
        pair: ``BASE/QUOTE``.
        kind: ``entry`` or ``exit``.
        exit_reason: ``chandelier``, ``time_stop``, ``regime_break`` or ``None``.
        at: The signal bar's close time.
        entry_price: The entry bar's close (carried on exits too).
        stop_price: The chandelier stop when the signal fired.

    """

    pair: str
    kind: str
    exit_reason: str | None
    at: datetime
    entry_price: Decimal
    stop_price: Decimal


@dataclass(frozen=True)
class Trade:
    """One row of the ``trades`` table.

    Attributes:
        pair: ``BASE/QUOTE``.
        side: ``BUY`` or ``SELL``.
        qty: Base-asset quantity.
        price: Average fill price.
        fee: Fee in the quote asset.
        at: Fill time.
        eur_status: ``ok`` once priced in euros, otherwise why not.

    """

    pair: str
    side: str
    qty: Decimal
    price: Decimal
    fee: Decimal
    at: datetime
    eur_status: str


def signals(db: Path) -> list[Signal]:
    """Return every recorded signal, oldest first."""
    rows = [json.loads(r["payload_json"]) for r in events(db, "SignalEmitted")]
    return [
        Signal(
            pair=f"{p['pair']['base']}/{p['pair']['quote']}",
            kind=p["kind"],
            exit_reason=p["exit_reason"],
            at=datetime.fromisoformat(p["occurred_at"]),
            entry_price=Decimal(p["entry_price"]),
            stop_price=Decimal(p["stop_price"]),
        )
        for p in rows
    ]


def trades(db: Path) -> list[Trade]:
    """Return every recorded trade, oldest first."""
    connection = sqlite3.connect(db)
    try:
        rows = connection.execute(
            "SELECT pair_base, pair_quote, side, qty_amount, price_amount, fee_amount,"
            " executed_at, eur_status FROM trades ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return [
        Trade(
            pair=f"{base}/{quote}",
            side=side,
            qty=Decimal(qty),
            price=Decimal(price),
            fee=Decimal(fee),
            at=datetime.fromisoformat(at),
            eur_status=status,
        )
        for base, quote, side, qty, price, fee, at, status in rows
    ]


def outline(db: Path) -> dict[str, list[tuple[str, ...]]]:
    """Return the signals and trades as short tuples, for one readable comparison."""
    return {
        "signals": [(s.pair, s.kind, s.exit_reason or "") for s in signals(db)],
        "trades": [(t.pair, t.side) for t in trades(db)],
    }
