"""The home views of the ledger read model: portfolio, activity, shadow test (#546, slices 2-3).

Ledgers are built through the real ledger API (see ``_ledger_builder``).

Invariants:

- the portfolio sums the seed and the marks at the newest EUR rate, the
  waterfall nets each sale to its FIFO gain, and the start capital is the
  seed's EUR value (change live-home-screen D-1 to D-3);
- with no EUR rate nothing is shown in EUR: the summary is EUR pending;
- a ledger with no mark and no seed answers "not available", never zeros;
- activity rows come from fills when the ledger has fills, from signals when
  it has none, and only inside the read window (D-5);
- the shadow test counts days from the seed, with the fill count (D-4);
- reading every view leaves the ledger file byte-for-byte unchanged.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hermes.adapters.console.ledger_read_model import ACTIVITY_WINDOW, LedgerReadModel
from hermes.application.console import PortfolioRange, ViewNotAvailableError
from hermes.application.console.views import ActivityKind, ShadowTestStatus
from tests.unit.adapters.console._ledger_builder import (
    H4,
    T0,
    LedgerBuilder,
    home_ledger,
    one_btc_position,
)

_READ_AT = T0 + timedelta(hours=9)


def _model(
    path: Path, at: datetime = _READ_AT, *, window: timedelta = ACTIVITY_WINDOW
) -> LedgerReadModel:
    return LedgerReadModel.open(path, clock=lambda: at, activity_window=window)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Return the priced home example ledger."""
    path = tmp_path / "hermes.sqlite3"
    home_ledger(path)
    return path


@pytest.fixture
def unpriced_home(tmp_path: Path) -> Path:
    """Return the home example with no EUR value anywhere."""
    path = tmp_path / "hermes.sqlite3"
    home_ledger(path, priced=False)
    return path


def test_portfolio_follows_the_worked_example(home: Path) -> None:
    """Total 4,078.76, change +78.76 from the 4,000 seed, cash 3,638.76, invested 440 (A-1.1)."""
    summary = _model(home).portfolio(PortfolioRange.TODAY)
    assert (
        summary.total_value,
        summary.change,
        summary.cash,
        summary.invested,
        summary.sparkline,
        summary.eur_pending,
    ) == (
        Decimal("4078.76"),
        Decimal("78.76"),
        Decimal("3638.76"),
        Decimal(440),
        (Decimal(4000), Decimal("3999.2"), Decimal("4031.2"), Decimal("4078.76")),
        False,
    )


def test_waterfall_nets_the_sale_to_its_fifo_gain(home: Path) -> None:
    """The partial sale: gross 40, fees 0.84, net 39.16 today and this year (D-2)."""
    summary = _model(home).portfolio(PortfolioRange.TODAY)
    columns = [(c.gross, c.fees, c.net, c.eur_pending) for c in (summary.today, summary.year)]
    assert columns == [(Decimal(40), Decimal("0.84"), Decimal("39.16"), False)] * 2


def test_open_pnl_is_the_net_of_the_held_position(home: Path) -> None:
    """The 0.01 BTC still held nets 39.16, the same as the live positions view."""
    model = _model(home)
    (row,) = model.positions()
    summary = model.portfolio(PortfolioRange.TODAY)
    assert (summary.open_pnl, summary.open_pnl_pending, row.net) == (
        Decimal("39.16"),
        False,
        Decimal("39.16"),
    )


def test_start_capital_is_the_seed_in_eur(home: Path) -> None:
    """The 5,000 USDT seed is worth 4,000 EUR at its 1.25 rate, started on 1 October (D-3)."""
    summary = _model(home).portfolio(PortfolioRange.YEAR)
    assert (summary.starting_capital, summary.starting_capital_quote, str(summary.started_on)) == (
        Decimal(4000),
        Decimal(5000),
        "2026-10-01",
    )


def test_unpriced_ledger_portfolio_is_eur_pending(unpriced_home: Path) -> None:
    """No rate anywhere: no EUR figure; the USDT total and the seed amount remain (A-1.2)."""
    summary = _model(unpriced_home).portfolio(PortfolioRange.TODAY)
    assert (
        summary.eur_pending,
        summary.total_value,
        summary.total_value_quote,
        summary.starting_capital,
        summary.starting_capital_quote,
    ) == (True, Decimal(0), Decimal("5098.45"), None, Decimal(5000))


def test_empty_ledger_portfolio_is_not_available(tmp_path: Path) -> None:
    """No mark and no seed: the portfolio raises rather than showing a zero total (A-1.3)."""
    path = tmp_path / "hermes.sqlite3"
    LedgerBuilder(path).close()
    with pytest.raises(ViewNotAvailableError, match="not available yet"):
        _model(path).portfolio()


def test_seed_without_marks_is_cash(tmp_path: Path) -> None:
    """A seeded strategy that has not marked a bar yet holds its seed as cash."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    b.seed("5000", T0)
    b.close()
    summary = _model(path).portfolio()
    assert (summary.total_value, summary.cash, summary.invested) == (
        Decimal(4000),
        Decimal(4000),
        Decimal(0),
    )


def test_activity_rows_come_from_the_fills(home: Path) -> None:
    """Opened at the buy and closed at the sale, in EUR at the fill rate, the close with its net."""
    (day,) = _model(home).activity()
    rows = [(i.kind, i.coin, i.price_eur, i.net) for i in day.items]
    assert rows == [
        (ActivityKind.CLOSED, "BTC", Decimal(44000), Decimal("39.16")),
        (ActivityKind.OPENED, "BTC", Decimal(40000), None),
    ]


def test_signals_are_quiet_rows_once_fills_exist(home: Path) -> None:
    """With fills, the entry signal adds a quiet signal row, not a second opened row."""
    (day,) = _model(home).activity(include_quiet=True)
    kinds = [i.kind for i in day.items]
    assert (kinds.count(ActivityKind.OPENED), kinds.count(ActivityKind.SIGNAL)) == (1, 1)


def test_unpriced_fills_show_no_price_and_no_net(unpriced_home: Path) -> None:
    """A fill without EUR values gets no price and no net rather than a USDT figure under €."""
    (day,) = _model(unpriced_home).activity()
    assert [(i.kind, i.price, i.net) for i in day.items] == [
        (ActivityKind.CLOSED, None, None),
        (ActivityKind.OPENED, None, None),
    ]


def test_ledger_without_fills_keeps_signal_rows(tmp_path: Path) -> None:
    """Before fills are recorded, an entry signal is the opened row (today's VPS ledger)."""
    path = tmp_path / "hermes.sqlite3"
    b = LedgerBuilder(path)
    b.bar(T0)
    b.entry_signal(T0 - timedelta(milliseconds=1), stop="48000", atr="1000", close="50000")
    b.close()
    (day,) = _model(path).activity()
    assert [(i.kind, i.coin, i.price) for i in day.items] == [(ActivityKind.OPENED, "BTC", None)]


def test_activity_window_drops_older_rows(home: Path) -> None:
    """A one-hour window read at 17:00 UTC sees nothing from the morning."""
    model = _model(home, window=timedelta(hours=1))
    assert model.activity(include_quiet=True) == ()


def test_shadow_test_counts_from_the_seed(home: Path) -> None:
    """On the seed's day: day 1 of 90, running, two fills, no verdict (A-3.1)."""
    test = _model(home).shadow_test()
    assert (test.status, test.day, test.note) == (
        ShadowTestStatus.RUNNING,
        1,
        "2 fills · verdict not available yet",
    )


def test_shadow_test_without_a_seed_is_not_started(tmp_path: Path) -> None:
    """The worked positions ledger has no seed: not started, and the note says so."""
    path = tmp_path / "hermes.sqlite3"
    one_btc_position(path)
    test = _model(path).shadow_test()
    assert (test.status, test.note) == (
        ShadowTestStatus.NOT_STARTED,
        "no capital seed in the ledger yet",
    )


def test_reading_the_home_views_leaves_the_file_unchanged(home: Path) -> None:
    """Portfolio, activity and shadow test only read: the file hash is the same afterwards."""
    before = hashlib.sha256(home.read_bytes()).hexdigest()
    model = _model(home, at=T0 + H4 * 3)
    for range_ in PortfolioRange:
        model.portfolio(range_)
    model.activity(include_quiet=True)
    model.shadow_test()
    assert hashlib.sha256(home.read_bytes()).hexdigest() == before
