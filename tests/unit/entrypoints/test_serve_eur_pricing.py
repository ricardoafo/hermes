"""Tests for the start-up pricing of pending EUR values in ``hermes serve`` (issue #630).

The FX seam is the scripted ``FakeFx``; the Frankfurter adapter itself is covered
against recorded responses in ``tests/integration/adapters/fx``.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from hermes.application.ledger.eur_pricer import EurPricer
from hermes.domain.errors import LedgerError
from hermes.domain.events.anomaly_detected import AnomalyDetected, AnomalySeverity
from hermes.domain.events.signal_emitted import SignalEmitted
from hermes.domain.strategy.signal import SignalKind, SignalSide
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.domain.value_objects.strategy_id import StrategyId
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.loader import load_config
from tests.support.serve_loop import (
    BASE,
    PROJECT_ROOT,
    FakeClock,
    FakeFeed,
    UnavailableBooks,
    build,
    run_once,
)
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


def _entry_event(pair_quote_ccy: Currency = Currency.USDT, at: datetime = BASE) -> SignalEmitted:
    """Return an entry signal on a pair quoted in ``pair_quote_ccy``."""
    return SignalEmitted(
        occurred_at=at,
        correlation_id="01J9Z8T3M5BQR2FVKN4YK7Q2ZP",
        strategy_id=StrategyId("swing-donchian-4h"),
        pair=Pair(base=Currency.BTC, quote=pair_quote_ccy),
        kind=SignalKind.ENTRY,
        side=SignalSide.LONG,
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        atr_context=Decimal("5"),
    )


def test_the_notifier_lookup_reads_the_pricer_cache_and_never_the_provider() -> None:
    """Invariant (668 D-1): the push path answers from memory; cold is None, no FX call."""
    fx = FakeFx(rates={_SEED_DAY: (Decimal("1.25"), _SEED_DAY)})
    pricer = EurPricer(fx)
    lookup = serve_module._eur_rate_lookup(pricer)

    cold = lookup(_entry_event())
    pricer.price(Currency.USDT, _SEED_DAY, today=_SEED_DAY)
    warm = lookup(_entry_event())

    assert (cold, warm, len(fx.calls)) == (None, Decimal("1.25"), 1)


def test_the_notifier_lookup_uses_the_madrid_date_of_the_event() -> None:
    """Invariant (668 D-2): 23:30 UTC on Dec 31 is Jan 1 in Madrid; that day's rate is used."""
    jan1 = date(2024, 1, 1)
    fx = FakeFx(rates={jan1: (Decimal("1.25"), jan1)})
    pricer = EurPricer(fx)
    pricer.price(Currency.USDT, jan1, today=jan1)
    lookup = serve_module._eur_rate_lookup(pricer)

    late_utc = datetime(2023, 12, 31, 23, 30, tzinfo=UTC)
    assert lookup(_entry_event(at=late_utc)) == Decimal("1.25")


def test_the_notifier_lookup_has_no_rate_for_an_event_without_a_pair() -> None:
    """Invariant (668 D-2): events that carry no pair (an anomaly) get no rate and no lookup."""
    fx = FakeFx()
    lookup = serve_module._eur_rate_lookup(EurPricer(fx))
    anomaly = AnomalyDetected(
        occurred_at=BASE,
        correlation_id="01J9Z8T3M5BQR2FVKN4YK7Q2ZP",
        source="swing.risk",
        severity=AnomalySeverity.CRITICAL,
        reason="ledger reconciliation mismatch",
    )
    assert (lookup(anomaly), fx.calls) == (None, [])


def test_boot_warms_one_rate_per_quote_currency(tmp_path: Path) -> None:
    """Invariant (668 D-1): one bounded boot lookup per quote, so the first push can show EUR."""
    fx = FakeFx(rates={BASE.date(): (Decimal("1.25"), BASE.date())})
    composition = build(tmp_path / "t.sqlite3", FakeFeed(), BASE, fx=fx)

    serve_module.warm_eur_rates(composition)

    assert (
        fx.calls,
        composition.eur_pricer.cached_rate(Currency.USDT, BASE.date()),
    ) == ([(Currency.EUR, Currency.USDT, BASE.date())], Decimal("1.25"))
    composition.sqlite_connection.close()


def test_boot_does_not_refetch_a_rate_it_already_holds(tmp_path: Path) -> None:
    """Invariant (668 D-1): the warm-up skips a quote the pricer already knows."""
    fx = FakeFx(rates={BASE.date(): (Decimal("1.25"), BASE.date())})
    composition = build(tmp_path / "t.sqlite3", FakeFeed(), BASE, fx=fx)
    composition.eur_pricer.price(Currency.USDT, BASE.date(), today=BASE.date())
    fx.calls.clear()

    serve_module.warm_eur_rates(composition)

    assert fx.calls == []
    composition.sqlite_connection.close()


def test_a_failing_warm_up_is_logged_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant (668 D-1): an FX outage or a pricer bug at boot never stops serve."""
    composition = build(tmp_path / "t.sqlite3", FakeFeed(), BASE, fx=FakeFx(down=True))

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("pricer broke")

    monkeypatch.setattr(composition.eur_pricer, "price", _boom)
    with capture_logs() as logs:
        serve_module.warm_eur_rates(composition)

    warned = [e for e in logs if e["event"] == "serve.eur_warm_failed"]
    assert [(w["log_level"], w["quote"], w["error"]) for w in warned] == [
        ("warning", "USDT", "RuntimeError")
    ]
    composition.sqlite_connection.close()


def test_serve_gives_both_notifiers_the_pricer_lookup(tmp_path: Path) -> None:
    """Invariant (668 D-2): with credentials set, Telegram and Slack are built with the lookup."""
    env = {
        "HERMES_TELEGRAM__BOT_TOKEN": "token-abc",  # gitleaks:allow
        "HERMES_TELEGRAM__CHAT_ID": "chat-xyz",
        "HERMES_SLACK__BOT_TOKEN": "xoxb-test",  # gitleaks:allow
        "HERMES_SLACK__DEFAULT_CHANNEL": "C0TEST",
    }
    composition = serve_module.build_composition(
        load_config(PROJECT_ROOT / "config", env={}),
        db_path=tmp_path / "t.sqlite3",
        env=env,
        feed=FakeFeed(),
        clock=FakeClock(BASE),
        orderbooks=UnavailableBooks(),
        fx=FakeFx(down=True),
    )
    try:
        assert composition.telegram is not None
        assert composition.slack is not None
        assert (
            composition.telegram.eur_rate is not None,
            composition.slack.eur_rate is not None,
        ) == (
            True,
            True,
        )
    finally:
        composition.sqlite_connection.close()
        if composition.telegram is not None:
            composition.telegram.close()
        if composition.slack is not None:
            composition.slack.close()
