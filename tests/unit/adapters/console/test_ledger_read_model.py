"""The ledger-backed console read model (#546, slice 1).

Invariants:

- positions come from the newest portfolio mark plus the recorded trades: the
  worked example in ``_ledger_builder.one_btc_position`` reads back exactly;
- the model never writes: its connection refuses a write, reading every view
  leaves the file's bytes unchanged, and a missing file is never created;
- a fill without EUR values yields an EUR-pending row, never a guessed figure.

The home views (portfolio, activity, shadow test) are covered in
``test_ledger_read_model_home.py``.

Reference: openspec/archive/ledger-read-model/change.md (R-1 to R-3, D-8).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.console.ledger_read_model import (
    LATE_AFTER,
    LedgerReadModel,
    connect_read_only,
)
from hermes.application.console import (
    ConsoleReadModel,
    HeartbeatState,
)
from hermes.domain.errors import LedgerError
from hermes.domain.trading.order import OrderSide
from tests.unit.adapters.console._ledger_builder import (
    BTC,
    ETH,
    H4,
    SWING,
    T0,
    Held,
    LedgerBuilder,
    one_btc_position,
)

_READ_AT = T0 + timedelta(hours=9)


def _model(path: Path, at: datetime = _READ_AT) -> LedgerReadModel:
    return LedgerReadModel.open(path, clock=lambda: at)


@pytest.fixture
def btc_ledger(tmp_path: Path) -> Path:
    """Return a ledger holding the worked-example BTC position, priced in EUR."""
    path = tmp_path / "hermes.sqlite3"
    one_btc_position(path)
    return path


def test_model_satisfies_the_console_protocol(btc_ledger: Path) -> None:
    """It can stand wherever the console expects a read model, with no demo tag."""
    model: ConsoleReadModel = _model(btc_ledger)
    assert (model.demo_scenario, [row.coin for row in model.positions()]) == (None, ["BTC"])


def test_row_reads_the_mark_signal_and_eur_values(btc_ledger: Path) -> None:
    """Entry, size, now price, stop and net follow the worked example (A-1.1)."""
    (row,) = _model(btc_ledger).positions()
    assert (
        row.coin,
        row.timeframe,
        row.side,
        row.size,
        row.entry_price,
        row.current_price,
        row.stop_price,
        row.entry_eur,
        row.current_eur,
        row.net,
        row.eur_pending,
    ) == (
        "BTC",
        "4h",
        "long",
        Decimal("0.5"),
        Decimal(50000),
        Decimal(52000),
        Decimal(48000),
        Decimal(40000),
        Decimal(41600),
        Decimal("759.2"),
        False,
    )


def test_detail_money_adds_up_to_the_acquisition_value(btc_ledger: Path) -> None:
    """Cost is price_eur x qty + fee_eur of the held lot (A-1.3); gross and fees follow."""
    detail = _model(btc_ledger).position("btc")
    assert detail is not None
    assert (
        detail.cost,
        detail.now_worth,
        detail.gross,
        detail.fees_paid,
        detail.fees_to_close,
    ) == (
        Decimal(20020),
        Decimal(20800),
        Decimal(800),
        Decimal(20),
        Decimal("20.8"),
    )


def test_high_and_path_come_from_the_marks_since_entry(btc_ledger: Path) -> None:
    """The path is one mark price per bar since the fill; the high is its maximum."""
    detail = _model(btc_ledger).position("BTC")
    assert detail is not None
    assert (detail.price_path, detail.row.high_since_entry) == (
        (Decimal(50000), Decimal(53000), Decimal(52000)),
        Decimal(53000),
    )


def test_stop_is_the_entry_signal_stop_with_no_moves(btc_ledger: Path) -> None:
    """Per-bar stops are not recorded: the entry stop shows and no move is claimed (D-5)."""
    detail = _model(btc_ledger).position("BTC")
    assert detail is not None
    assert (detail.row.stop_distance_atr, detail.stop_moves) == (Decimal(4), 0)


def test_coin_not_held_has_no_detail(btc_ledger: Path) -> None:
    """``position`` answers None for a coin the snapshot does not hold (A-1.2)."""
    model = _model(btc_ledger)
    assert [model.position(coin) is None for coin in ("ETH", "BTC")] == [True, False]


def test_sold_out_position_is_not_listed(tmp_path: Path) -> None:
    """After the SELL the newest mark holds nothing, so the list is empty (A-1.2)."""
    path = tmp_path / "hermes.sqlite3"
    one_btc_position(path)
    b = LedgerBuilder(path)
    b.trade(OrderSide.SELL, "0.5", "52000", "26", T0 + H4 * 3)
    b.mark(T0 + H4 * 3, ())
    b.close()
    assert _model(path).positions() == ()


def test_partly_sold_lot_keeps_its_share_of_the_buy_fee(tmp_path: Path) -> None:
    """Selling half of the lot halves the fees paid and the EUR entry total."""
    path = tmp_path / "hermes.sqlite3"
    one_btc_position(path)
    b = LedgerBuilder(path)
    b.trade(OrderSide.SELL, "0.25", "52000", "13", T0 + H4 * 3)
    b.mark(T0 + H4 * 3, (Held(BTC, "0.25", "12500", "52000", T0 + H4 * 3),))
    b.close()
    detail = _model(path).position("BTC")
    assert detail is not None
    assert (detail.fees_paid, detail.cost) == (Decimal(10), Decimal(10010))


def test_unpriced_buy_makes_the_row_eur_pending(tmp_path: Path) -> None:
    """A BUY stored with eur_status pending yields an EUR-pending row (A-3.1)."""
    path = tmp_path / "hermes.sqlite3"
    one_btc_position(path, priced=False)
    (row,) = _model(path).positions()
    assert row.eur_pending is True


def test_untracked_fill_makes_the_row_eur_pending(tmp_path: Path) -> None:
    """A snapshot position with no recorded trade cannot be valued in EUR: pending."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    b.entry_signal(T0, stop="2400", atr="30", close="2500", pair=ETH)
    b.mark(T0, (Held(ETH, "1", "2500", "2550", T0),), pair=ETH)
    b.close()
    (row,) = _model(path).positions()
    assert (row.coin, row.eur_pending, row.opened_at) == ("ETH", True, T0)


def test_open_position_without_entry_signal_is_a_ledger_error(tmp_path: Path) -> None:
    """Fills come from signals; a held coin with no entry signal is reported, not invented."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    b.mark(T0, (Held(ETH, "1", "2500", "2550", T0),), pair=ETH)
    b.close()
    with pytest.raises(LedgerError, match="no entry signal"):
        _model(path).positions()


def test_ledger_without_marks_has_no_positions(tmp_path: Path) -> None:
    """A fresh ledger (no bar handled yet) lists nothing."""
    path = tmp_path / "hermes.sqlite3"
    LedgerBuilder(path).close()
    assert _model(path).positions() == ()


def test_read_only_connection_refuses_writes(btc_ledger: Path) -> None:
    """The connection the model reads through cannot write (A-2.1)."""
    conn = connect_read_only(btc_ledger)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM trades")
    finally:
        conn.close()


def test_reading_every_view_leaves_the_file_unchanged(btc_ledger: Path) -> None:
    """Positions, detail and heartbeat reads change no byte of the ledger (A-2.2)."""
    before = btc_ledger.read_bytes()
    model = _model(btc_ledger)
    model.positions()
    model.position("BTC")
    model.heartbeat()
    assert btc_ledger.read_bytes() == before


def test_missing_ledger_is_never_created(tmp_path: Path) -> None:
    """Opening a path with no file raises and leaves no file behind (A-2.2)."""
    path = tmp_path / "absent.sqlite3"
    with pytest.raises(FileNotFoundError):
        LedgerReadModel.open(path)
    assert path.exists() is False


def test_heartbeat_reports_the_newest_bar_check(btc_ledger: Path) -> None:
    """Last check is the newest bar boundary; the next is one 4h interval later."""
    heartbeat = _model(btc_ledger).heartbeat()
    assert (heartbeat.state, heartbeat.last_check_at, heartbeat.next_check_at) == (
        HeartbeatState.OK,
        T0 + H4 * 2,
        T0 + H4 * 3,
    )


def test_empty_ledger_heartbeat_looks_ahead_to_the_next_4h_boundary(tmp_path: Path) -> None:
    """With no bar check yet, the next check is the next 4h boundary, never the read time."""
    path = tmp_path / "hermes.sqlite3"
    LedgerBuilder(path).close()
    at = datetime(2026, 10, 3, 12, 12, tzinfo=UTC)
    heartbeat = _model(path, at=at).heartbeat()
    assert (heartbeat.state, heartbeat.last_check_at, heartbeat.next_check_at) == (
        HeartbeatState.OK,
        at,
        datetime(2026, 10, 3, 16, 0, tzinfo=UTC),
    )


def test_heartbeat_turns_late_past_the_grace(btc_ledger: Path) -> None:
    """A due check missed by more than the grace reads LATE, never DOWN (D-8)."""
    late_at = T0 + H4 * 3 + LATE_AFTER + timedelta(seconds=1)
    assert _model(btc_ledger, at=late_at).heartbeat().state is HeartbeatState.LATE


def test_heartbeat_on_time_inside_the_grace(btc_ledger: Path) -> None:
    """At exactly the grace boundary the heartbeat is still OK."""
    at = T0 + H4 * 3 + LATE_AFTER
    assert _model(btc_ledger, at=at).heartbeat().state is HeartbeatState.OK


def test_strategies_are_read_separately(tmp_path: Path) -> None:
    """Each strategy's newest mark contributes its own positions."""
    path = tmp_path / "hermes.sqlite3"
    one_btc_position(path)
    other = SWING.__class__("swing-donchian-1d")
    b = LedgerBuilder(path)
    b.entry_signal(T0, stop="2400", atr="30", close="2500", pair=ETH, strategy=other)
    b.mark(T0 + H4, (Held(ETH, "1", "2500", "2550", T0),), pair=ETH, strategy=other)
    b.close()
    assert [row.coin for row in _model(path).positions()] == ["ETH", "BTC"]
