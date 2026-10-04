"""Tests for the start-up pricing of pending EUR values in ``hermes serve`` (issue #630).

The FX seam is the scripted ``FakeFx``; the Frankfurter adapter itself is covered
against recorded responses in ``tests/integration/adapters/fx``.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.errors import LedgerError
from hermes.entrypoints import serve as serve_module
from tests.support.serve_loop import BASE, FakeFeed, build, run_once
from tests.unit.application.ledger.fake_fx import FakeFx

_SEED_DAY = date(2024, 1, 1)


def _seed_row(composition: serve_module.Composition) -> tuple[str, str | None, str | None]:
    """Return ``(eur_status, eur_rate, value_eur)`` of the capital seed."""
    row = composition.sqlite_connection.execute(
        "SELECT eur_status, eur_rate, value_eur FROM capital_seeds"
    ).fetchone()
    return (str(row[0]), row[1], row[2])


def test_serve_prices_the_pending_seed_at_boot_when_fx_works(tmp_path: Path) -> None:
    """Invariant (#630): a fresh boot with FX reachable leaves the seed priced, no manual step."""
    fx = FakeFx(rates={_SEED_DAY: (Decimal("1.25"), _SEED_DAY)})
    db = tmp_path / "t.sqlite3"
    composition = build(db, FakeFeed(), BASE, fx=fx)
    assert _seed_row(composition)[0] == "pending"

    with capture_logs() as logs:
        code = run_once(composition, FakeFeed())

    priced = [entry for entry in logs if entry["event"] == "serve.eur_priced"]
    assert code == 0
    assert [(e["seeds_filled"], e["seeds_pending"]) for e in priced] == [(1, 0)]
    assert len(fx.calls) == 1
    reopened = build(db, FakeFeed(), BASE, fx=fx)
    try:
        assert _seed_row(reopened) == ("ok", "1.25", "4000.000000000000000000000000000000")
    finally:
        reopened.sqlite_connection.close()


def test_serve_keeps_running_and_leaves_the_seed_pending_when_fx_is_down(tmp_path: Path) -> None:
    """Invariant (#630): an FX outage at boot never stops serve and leaves the seed pending."""
    fx = FakeFx(down=True)
    db = tmp_path / "t.sqlite3"
    composition = build(db, FakeFeed(), BASE, fx=fx)

    with capture_logs() as logs:
        code = run_once(composition, FakeFeed())

    priced = [entry for entry in logs if entry["event"] == "serve.eur_priced"]
    assert code == 0
    assert [(e["seeds_filled"], e["seeds_pending"]) for e in priced] == [(0, 1)]
    reopened = build(db, FakeFeed(), BASE, fx=fx)
    try:
        assert _seed_row(reopened) == ("pending", None, None)
    finally:
        reopened.sqlite_connection.close()


def test_a_ledger_failure_while_pricing_is_logged_and_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant (#630): any failure of the boot pricing is a warning, never a start-up failure."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise LedgerError(msg="disk full")

    monkeypatch.setattr(serve_module, "backfill_pending_eur", _boom)
    composition = build(tmp_path / "t.sqlite3", FakeFeed(), BASE)

    with capture_logs() as logs:
        serve_module.price_pending_eur_once(composition)

    warnings = [e for e in logs if e["event"] == "serve.eur_pricing_failed"]
    assert [(w["log_level"], w["error"]) for w in warnings] == [("warning", "LedgerError")]
    composition.sqlite_connection.close()


def test_boot_pricing_leaves_an_already_priced_seed_untouched(tmp_path: Path) -> None:
    """Invariant (#630): a second boot never re-prices a seed that already carries its EUR value."""
    first = FakeFx(rates={_SEED_DAY: (Decimal("1.25"), _SEED_DAY)})
    composition = build(tmp_path / "t.sqlite3", FakeFeed(), BASE, fx=first)
    serve_module.price_pending_eur_once(composition)
    priced = _seed_row(composition)
    assert priced[0] == "ok"

    second = FakeFx(rates={_SEED_DAY: (Decimal("2"), _SEED_DAY)})
    again = dataclasses.replace(composition, eur_pricer=EurPricer(second))
    with capture_logs() as logs:
        serve_module.price_pending_eur_once(again)

    assert _seed_row(composition) == priced
    assert second.calls == []
    assert [
        (e["seeds_filled"], e["seeds_pending"]) for e in logs if e["event"] == "serve.eur_priced"
    ] == [(0, 0)]
    composition.sqlite_connection.close()
