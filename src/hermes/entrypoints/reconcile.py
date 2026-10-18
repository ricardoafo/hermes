"""``hermes reconcile`` — monthly reconciliation of the ledger.

Compares the trade book with the ``TradeExecuted`` replay and the lot table,
reports the month's realized EUR P&L and the lots open at its end, and gives a
verdict: CLEAN, REVIEW (EUR values pending) or DRIFT. Reads the ledger with
SQLite's ``mode=ro`` and is safe next to ``hermes serve``. Its only write is
the ``--notify`` alert, appended to the ledger ``events`` table before it is
sent (#136).

Usage: ``hermes reconcile [YYYY-MM] [--config-dir DIR] [--db PATH]
[--tolerance QTY] [--json] [--notify]``

Without a month it covers the previous Madrid calendar month, the one a
month-end cut closes. ``--notify`` sends a Telegram alert when the verdict is
not CLEAN, and records that alert in the ledger.

With ``allow_mainnet`` set (prod only), the current month is also compared
with the live Binance spot balances, read with a dedicated read-only key from
``HERMES_BINANCE_READ__API_KEY`` and ``HERMES_BINANCE_READ__API_SECRET`` (#29).
A missing key is an error, never a silent fall back to the ledger-only check.

Exit codes: 0 clean, 1 review or drift, 2 usage, 3 config error, no ledger,
missing read-only key or unreadable exchange.

Reference: openspec/archive/35-monthly-reconciliation/change.md.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final, Protocol, TextIO

from hermes.adapters.exchanges.binance_balance_reader import (
    API_KEY_ENV,
    API_SECRET_ENV,
    BinanceBalanceReader,
    BinanceBalanceReaderConfig,
)
from hermes.adapters.ledger.reconciliation_reader import SqliteReconciliationReader
from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)
from hermes.application.reconciliation import (
    StrategySeed,
    reconcile_month,
    reconciliation_alert,
)
from hermes.domain.errors import InfraError, LedgerError
from hermes.domain.reconciliation import ReconciliationMonth, ReconciliationReport, Verdict
from hermes.domain.value_objects.money import Money
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints import serve as serve_module
from hermes.entrypoints._anomaly_record import record_anomaly
from hermes.entrypoints._streams import resolve_err
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.time import to_madrid
from hermes.ports.notifier import NotifierPort
from hermes.ports.reconciliation import BalanceReadPort

__all__ = ["DEFAULT_TOLERANCE", "ClosableBalanceReader", "main", "report_to_dict"]

DEFAULT_TOLERANCE: Final = Decimal("1e-8")
"""Binance's smallest step for spot quantities; anything above it is real drift."""

_EUR_PLACES: Final = Decimal("0.01")
_USAGE: Final = (
    "Usage: hermes reconcile [YYYY-MM] [--config-dir DIR] [--db PATH]\n"
    "                        [--tolerance QTY] [--json] [--notify]\n"
    "\n"
    "Monthly reconciliation: the trade book against the event replay and\n"
    "the lot table, the month's realized EUR P&L and the lots open at its end.\n"
    "Without YYYY-MM it covers the previous month (Europe/Madrid).\n"
    "--notify sends a Telegram alert when the verdict is not CLEAN and records\n"
    "it in the ledger.\n"
    "With allow_mainnet set it also reads the live Binance balances, using\n"
    f"{API_KEY_ENV} and {API_SECRET_ENV} (a read-only key).\n"
    "Exit codes: 0 clean, 1 review or drift, 2 usage, 3 config error, no ledger,\n"
    "missing read-only key or unreadable exchange.\n"
)
_NEXT_STEP: Final = {
    Verdict.CLEAN: "nothing to do",
    Verdict.REVIEW: "run `hermes fx backfill`, then reconcile again",
    Verdict.DRIFT: "resolve every drift and problem before the month-end lot cut",
}

NotifierFactory = Callable[[Mapping[str, str]], NotifierPort | None]


class ClosableBalanceReader(BalanceReadPort, Protocol):
    """A live balance reader that holds a connection until closed."""

    def close(self) -> None:
        """Release the connection."""
        ...


BalanceReaderFactory = Callable[[Mapping[str, str]], ClosableBalanceReader]
"""Builds the live reader from the environment; raises ValueError without a key."""


def main(
    rest: list[str],
    *,
    stream: TextIO,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    notifier_factory: NotifierFactory | None = None,
    balance_reader_factory: BalanceReaderFactory | None = None,
    err: TextIO | None = None,
) -> int:
    """Handle ``hermes reconcile``.

    Args:
        rest: Residual argv after the ``reconcile`` token.
        stream: Output stream.
        now: Run time; defaults to the wall clock. Injected by tests.
        env: Environment snapshot for the Telegram credentials and the
            read-only Binance key; the key is read from ``os.environ`` when
            this is ``None``.
        notifier_factory: Builds the alert channel from ``env``; defaults to
            Telegram. Injected by tests so no socket opens.
        balance_reader_factory: Builds the live balance reader from ``env``
            when ``allow_mainnet`` is set and the month is still open;
            defaults to Binance. Injected by
            tests so no socket opens.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    at = now if now is not None else datetime.now(UTC)
    err = resolve_err(err)
    request = _parse(rest, stream, err, at)
    if isinstance(request, int):
        return request
    parsed, month, tolerance = request

    try:
        config = load_config(parsed.config_dir)
    except ConfigError as exc:
        err.write(f"ERROR: config load failed: {exc}\n")
        return 3
    # A disabled entry (v2 waiting for its shadow) has funded nothing; seeding
    # it would add its capital to the book the ledger is checked against.
    seeds = [
        StrategySeed(
            strategy_id=StrategyId(s.name),
            capital=Money(amount=s.capital_budget, currency=s.quote),
        )
        for s in config.strategies.strategies
        if s.enabled
    ]

    exchange: ClosableBalanceReader | None = None
    # A venue reports balances now, so an ended month never reads them and
    # needs no key (the month-end cut runs on the previous month).
    if config.allow_mainnet and at < month.end:
        factory = balance_reader_factory or _binance_balance_reader
        try:
            exchange = factory(env if env is not None else os.environ)
        except ValueError as exc:
            err.write(
                f"ERROR: allow_mainnet is set, so reconcile reads the live balances and needs "
                f"a read-only key in {API_KEY_ENV} and {API_SECRET_ENV}: {exc}\n"
            )
            return 3
    try:
        report = _report(
            parsed.db,
            month=month,
            seeds=seeds,
            at=at,
            tolerance=tolerance,
            exchange=exchange,
            err=err,
        )
    finally:
        if exchange is not None:
            exchange.close()
    if report is None:
        return 3

    if parsed.json:
        stream.write(json.dumps(report_to_dict(report), indent=2) + "\n")
    else:
        stream.write(render_report(report))
    if parsed.notify:
        _send_alert(
            report,
            at,
            env or {},
            notifier_factory or _telegram_notifier,
            stream,
            db=parsed.db,
            err=err,
        )
    return 0 if report.verdict is Verdict.CLEAN else 1


def _report(
    db: Path,
    *,
    month: ReconciliationMonth,
    seeds: list[StrategySeed],
    at: datetime,
    tolerance: Decimal,
    exchange: BalanceReadPort | None,
    err: TextIO,
) -> ReconciliationReport | None:
    """Build the report; write the ERROR line and return ``None`` when a source fails."""
    try:
        with SqliteReconciliationReader.open(db) as reader:
            return reconcile_month(
                reader,
                month=month,
                seeds=seeds,
                now=at,
                tolerance=tolerance,
                exchange=exchange,
            )
    except FileNotFoundError:
        err.write(f"ERROR: no ledger at {db}\n")
    except LedgerError as exc:
        err.write(f"ERROR: could not read the ledger: {exc}\n")
    except InfraError as exc:
        # LedgerError is caught above, so what is left comes from the exchange.
        err.write(f"ERROR: could not read the exchange balances: {exc}\n")
    return None


def _parse(
    rest: list[str], stream: TextIO, err: TextIO, at: datetime
) -> tuple[argparse.Namespace, ReconciliationMonth, Decimal] | int:
    """Parse argv; return the request, or the exit code to stop with."""
    parser = argparse.ArgumentParser(prog="hermes reconcile", add_help=False)
    parser.add_argument("month", nargs="?", default=None)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--db", type=Path, default=serve_module.DEFAULT_DB_PATH)
    parser.add_argument("--tolerance", default=str(DEFAULT_TOLERANCE))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(rest)
    except SystemExit:
        err.write("ERROR: could not parse `hermes reconcile` arguments.\n" + _USAGE)
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    try:
        month = (
            ReconciliationMonth.parse(parsed.month)
            if parsed.month is not None
            else ReconciliationMonth.containing(at).previous()
        )
        tolerance = Decimal(parsed.tolerance)
    except (ValueError, InvalidOperation) as exc:
        err.write(f"ERROR: {exc}\n" + _USAGE)
        return 2
    if not tolerance.is_finite() or tolerance < 0:
        err.write(f"ERROR: --tolerance must be a number >= 0, got {parsed.tolerance}\n")
        return 2
    if month.start > at:
        err.write(f"ERROR: {month} has not started yet\n")
        return 2
    return parsed, month, tolerance


def render_report(report: ReconciliationReport) -> str:
    """Return the plain-text view of ``report``."""
    as_of = to_madrid(report.as_of).strftime("%Y-%m-%d %H:%M")
    verdict = report.verdict
    if report.ready_for_cut:
        status = "ready for the month-end lot cut"
    elif verdict is Verdict.CLEAN:
        status = "month still in progress"
    else:
        status = _NEXT_STEP[verdict]
    lines = [
        f"Reconciliation {report.month} (Europe/Madrid month), as of {as_of} Madrid",
        f"Verdict: {verdict.value.upper()} - {status}",
        "",
        "Holdings: trade book against each view",
    ]
    if report.checks:
        lines.append(f"  {'view':<9}{'asset':<7}{'book':>22}{'observed':>22}{'drift':>16}  status")
        lines.extend(
            f"  {c.source:<9}{c.asset.value:<7}{_qty(c.book):>22}{_qty(c.observed):>22}"
            f"{_qty(c.drift):>16}  {c.status.value}"
            for c in report.checks
        )
    else:
        lines.append("  nothing held in any view")
    for title, items in (("Problems", report.problems), ("Not compared", report.skipped)):
        if items:
            lines.extend(["", title, *(f"  - {item}" for item in items)])
    tax = report.tax
    if tax is not None:
        pnl = tax.pnl
        lines.extend(
            [
                "",
                "Month P&L (FIFO, EUR, fees included)",
                f"  fills: {pnl.buys} buy(s), {pnl.sells} sell(s)",
                f"  proceeds {_eur(pnl.proceeds_eur)}  cost {_eur(pnl.cost_eur)}  "
                f"fees {_eur(pnl.fees_eur)}  realized {_eur(pnl.realized_eur)}",
                f"  trades without EUR values: {tax.pending_trades}",
                "",
                f"Open lots at {as_of}: {len(tax.open_lots)} "
                f"(acquisition value {_eur(tax.open_cost_eur)} EUR)",
            ]
        )
        lines.extend(
            f"  {lot.asset.value:<6}opened {to_madrid(lot.opened_at):%Y-%m-%d %H:%M}  "
            f"remaining {_qty(lot.remaining_qty)}  "
            f"basis {_eur(lot.cost_basis_per_unit_eur)} EUR/unit"
            for lot in tax.open_lots
        )
    return "\n".join(lines) + "\n"


def report_to_dict(report: ReconciliationReport) -> dict[str, Any]:
    """Return ``report`` as JSON-ready data; decimals are exact strings."""
    tax = report.tax
    return {
        "month": str(report.month),
        "as_of": report.as_of.isoformat(),
        "verdict": report.verdict.value,
        "month_closed": report.month_closed,
        "ready_for_cut": report.ready_for_cut,
        "checks": [
            {
                "view": c.source,
                "asset": c.asset.value,
                "book": _qty(c.book),
                "observed": _qty(c.observed),
                "drift": _qty(c.drift),
                "tolerance": _qty(c.tolerance),
                "status": c.status.value,
            }
            for c in report.checks
        ],
        "problems": list(report.problems),
        "skipped": list(report.skipped),
        "tax": None
        if tax is None
        else {
            "buys": tax.pnl.buys,
            "sells": tax.pnl.sells,
            "proceeds_eur": _exact(tax.pnl.proceeds_eur),
            "cost_eur": _exact(tax.pnl.cost_eur),
            "fees_eur": _exact(tax.pnl.fees_eur),
            "realized_eur": _exact(tax.pnl.realized_eur),
            "pending_trades": tax.pending_trades,
            "open_cost_eur": _exact(tax.open_cost_eur),
            "open_lots": [
                {
                    "asset": lot.asset.value,
                    "buy_trade_id": lot.buy_trade_id,
                    "opened_at": lot.opened_at.isoformat(),
                    "remaining_qty": _qty(lot.remaining_qty),
                    "cost_basis_per_unit_eur": _exact(lot.cost_basis_per_unit_eur),
                }
                for lot in tax.open_lots
            ],
        },
    }


def _send_alert(
    report: ReconciliationReport,
    at: datetime,
    env: Mapping[str, str],
    factory: NotifierFactory,
    stream: TextIO,
    *,
    db: Path,
    err: TextIO,
) -> None:
    alert = reconciliation_alert(report, now=at)
    if alert is None:
        return
    record_anomaly(db, alert, err)
    notifier = factory(env)
    if notifier is None:
        stream.write("WARNING: --notify given but Telegram is not configured; no alert sent\n")
        return
    try:
        notifier.notify(alert)
    except InfraError as exc:
        stream.write(f"WARNING: the alert could not be sent: {exc}\n")
        return
    finally:
        if isinstance(notifier, TelegramAdapter):
            notifier.close()
    stream.write("Alert sent.\n")


def _binance_balance_reader(env: Mapping[str, str]) -> ClosableBalanceReader:
    return BinanceBalanceReader.create(BinanceBalanceReaderConfig.from_env(env))


def _telegram_notifier(env: Mapping[str, str]) -> NotifierPort | None:
    try:
        config = TelegramConfig.from_env(dict(env))
    except TelegramNotifierError:
        return None
    return TelegramAdapter.create(config)


def _qty(value: Decimal) -> str:
    normalized = value.normalize()
    return f"{normalized:f}" if normalized != 0 else "0"


def _exact(value: Decimal | None) -> str | None:
    return None if value is None else _qty(value)


def _eur(value: Decimal | None) -> str:
    return "pending" if value is None else f"{value.quantize(_EUR_PLACES):f}"
