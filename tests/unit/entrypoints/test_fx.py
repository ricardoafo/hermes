"""Tests for ``hermes fx backfill`` (change trade-eur-values A-3.3).

Reference: ADR-016 item 5.
"""

import io
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from hermes.adapters.fx.frankfurter import FrankfurterAdapter
from hermes.adapters.ledger.sqlite_ledger import SqliteLedger
from hermes.application.ledger.eur_pricer import EurPricer
from hermes.application.ledger.trade_recorder import TradeRecorder
from hermes.domain.events.shadow_capital_seeded import ShadowCapitalSeeded
from hermes.domain.lots.quote_leg import CapitalSeed
from hermes.domain.trading.fill import Fill
from hermes.domain.trading.order import Order, OrderSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.quantity import Quantity
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints import cli as cli_module
from hermes.entrypoints import fx as fx_module
from hermes.infrastructure.config.models import FrankfurterConfig
from tests.support.paths import REPO_ROOT
from tests.unit.application.ledger.fake_fx import FakeFx

_CONFIG_DIR = REPO_ROOT / "config"
_FILL_DAY = date(2026, 1, 2)


def _db_with_one_pending_buy(path: Path) -> None:
    """Record one BUY during an FX outage into a ledger file."""
    conn = sqlite3.connect(path)
    try:
        recorder = TradeRecorder(SqliteLedger(conn), EurPricer(FakeFx(down=True)))
        recorder.record(
            Fill(
                order=Order(
                    side=OrderSide.BUY,
                    pair=Pair(base=Currency.BTC, quote=Currency.USDT),
                    qty=Quantity(amount=Decimal("1"), asset=Currency.BTC),
                    strategy_id=StrategyId("swing"),
                ),
                executed_qty=Decimal("1"),
                avg_price=Decimal("117.21"),
                fee=Money(amount=Decimal("0"), currency=Currency.USDT),
                timestamp=datetime(2026, 1, 2, 12, tzinfo=UTC),
            ),
            correlation_id="c-1",
        )
    finally:
        conn.close()


def test_backfill_command_prices_pending_rows(tmp_path: Path) -> None:
    """Invariant (A-3.3): the command exits 0, reports counts and leaves the row priced."""
    db = tmp_path / "ledger.sqlite3"
    _db_with_one_pending_buy(db)
    stream = io.StringIO()

    code = fx_module.main(
        ["backfill", "--db", str(db), "--config-dir", str(_CONFIG_DIR)],
        stream=stream,
        fx_factory=lambda _cfg: FakeFx(rates={_FILL_DAY: (Decimal("1.1721"), _FILL_DAY)}),
    )

    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT eur_status, price_eur FROM trades").fetchone()
    finally:
        conn.close()
    assert (code, stream.getvalue(), row) == (0, "filled=1 pending=0\n", ("ok", "100"))


def test_cli_routes_fx_to_the_backfill_usage() -> None:
    """Invariant: `hermes fx` without a command prints usage and exits 2."""
    stream = io.StringIO()
    code = cli_module.main(["fx"], stream=stream)
    assert (code, stream.getvalue().splitlines()[0]) == (
        2,
        "Usage: hermes fx backfill [--config-dir DIR] [--db PATH]",
    )


def test_unknown_fx_command_is_a_usage_error() -> None:
    """Invariant: an unknown `hermes fx` command exits 2 and names the command."""
    stream, err = io.StringIO(), io.StringIO()
    code = fx_module.main(["rebuild"], stream=stream, err=err)
    assert (code, stream.getvalue()) == (2, "")
    assert "unknown `hermes fx` command 'rebuild'" in err.getvalue()


def test_build_fx_sends_usdt_as_usd_to_the_configured_host() -> None:
    """Invariant (D-1): the production provider reads USDT as USD on the configured host."""
    fx = fx_module.build_fx(FrankfurterConfig(base_url="https://api.frankfurter.dev/v1"))
    assert isinstance(fx, FrankfurterAdapter)
    try:
        assert (str(fx.client.base_url), fx.config.wire_code(Currency.USDT)) == (
            "https://api.frankfurter.dev/v1/",
            "USD",
        )
    finally:
        fx.close()


def test_backfill_command_reports_seeds_when_the_ledger_has_one(tmp_path: Path) -> None:
    """Invariant (405 D-1): a pending seed is priced and counted on its own output line."""
    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    try:
        seed = CapitalSeed(
            strategy_id=StrategyId("swing"),
            amount=Money(amount=Decimal("1000"), currency=Currency.USDT),
            seeded_at=datetime(2026, 1, 2, 9, tzinfo=UTC),
        )
        SqliteLedger(conn).record_capital_seed(
            seed,
            ShadowCapitalSeeded(
                occurred_at=seed.seeded_at,
                correlation_id="seed",
                strategy_id=seed.strategy_id,
                amount=seed.amount,
            ),
        )
    finally:
        conn.close()
    stream = io.StringIO()

    code = fx_module.main(
        ["backfill", "--db", str(db), "--config-dir", str(_CONFIG_DIR)],
        stream=stream,
        fx_factory=lambda _cfg: FakeFx(rates={_FILL_DAY: (Decimal("1.1721"), _FILL_DAY)}),
    )

    assert (code, stream.getvalue()) == (0, "filled=0 pending=0\nseeds_filled=1 seeds_pending=0\n")


def test_backfill_on_a_missing_ledger_creates_no_file(tmp_path: Path) -> None:
    """A wrong --db path is reported, never created as an empty ledger (#1187)."""
    missing = tmp_path / "absent.sqlite3"
    err = io.StringIO()

    fx_module.main(
        ["backfill", "--db", str(missing), "--config-dir", str(_CONFIG_DIR)],
        stream=io.StringIO(),
        fx_factory=lambda _cfg: FakeFx(rates={}),
        err=err,
    )

    assert (missing.exists(), err.getvalue()) == (False, f"ERROR: no ledger at {missing}\n"), (
        "nothing created, the path named"
    )
