"""Serve feeds RO-02, the depeg rules and the recovery kill real inputs with real money (#1158).

Invariants:

- Shadow (``allow_mainnet`` off) builds no live inputs, no stablecoin watch
  and the shadow-set risk engine: it runs what the backtest runs (#514).
- With ``allow_mainnet`` serve hands the executor the ledger-backed live
  inputs, runs RO-02 and polls the USDT price beside the bar loop, the first
  poll at start.
- The default price source is Kraken's public USDT/USD book, closed on
  release.

The executor's use of each input is covered by
``tests/unit/application/execution/test_shadow.py``; the Kraken seam by
``tests/unit/adapters/exchanges/test_kraken_public.py``.
"""

import asyncio
from decimal import Decimal
from pathlib import Path

from hermes.adapters.exchanges.kraken_public import KrakenPublicAdapter
from hermes.application.live_risk_inputs import LedgerLiveRiskInputs, StablePriceWatch
from hermes.domain.risk.spread_spike import SpreadSpike
from hermes.entrypoints import serve as serve_module
from hermes.infrastructure.config.loader import load_config
from hermes.infrastructure.config.root import HermesConfig
from tests.support.clock import FakeClock
from tests.support.paths import REPO_ROOT
from tests.support.serve_loop import BASE, FakeFeed, UnavailableBooks, drive
from tests.unit.application.ledger.fake_fx import FakeFx


def _config(*, mainnet: bool) -> HermesConfig:
    """Return the shipped config, with ``allow_mainnet`` set as asked.

    ``model_copy`` skips the prod-only validator: these tests build the
    composition, they never pass ``check_startup``'s live-approval gate.
    """
    config = load_config(REPO_ROOT / "config", env={})
    return config.model_copy(update={"allow_mainnet": mainnet})


def _build(
    tmp_path: Path, *, mainnet: bool, prices: list[Decimal] | None = None
) -> serve_module.Composition:
    """Build serve over fakes; with ``prices`` each USDT fetch appends and returns 1.0002."""

    def fetch() -> Decimal:
        assert prices is not None
        prices.append(Decimal("1.0002"))
        return prices[-1]

    clock = FakeClock(BASE)
    return serve_module.build_composition(
        _config(mainnet=mainnet),
        db_path=tmp_path / "test.sqlite3",
        env={},
        feed=FakeFeed(),
        clock=clock,
        orderbooks=UnavailableBooks(),
        fx=FakeFx(down=True),
        new_ledger=True,
        stable_watch=None if prices is None else StablePriceWatch(fetch=fetch, clock=clock),
    )


def _rules(composition: serve_module.Composition) -> list[str]:
    """Return the rule names of the executor's risk engine."""
    engine = composition.executor._kernel._router._risk_engine
    return [type(rule).__name__ for rule in engine._rules]


def test_shadow_builds_no_live_inputs_and_never_runs_ro02(tmp_path: Path) -> None:
    """Without real money the executor reads neutral inputs, as the backtest does."""
    composition = _build(tmp_path, mainnet=False)
    try:
        assert (
            composition.executor._live_inputs,
            composition.stable_price_watch,
            composition.kraken_adapter,
            "SpreadSpike" in _rules(composition),
        ) == (None, None, None, False)
    finally:
        composition.sqlite_connection.close()


# SEV-1: S12 — with real money RO-02, the depeg rules and the recovery kill read real inputs.
def test_real_money_hands_the_executor_live_inputs_and_runs_ro02(tmp_path: Path) -> None:
    """``allow_mainnet`` wires the ledger-backed inputs and adds RO-02 to the engine."""
    composition = _build(tmp_path, mainnet=True, prices=[])
    try:
        assert isinstance(composition.executor._live_inputs, LedgerLiveRiskInputs)
        assert composition.stable_price_watch is not None
        assert _rules(composition)[-1] == SpreadSpike.__name__
    finally:
        composition.sqlite_connection.close()


def test_real_money_polls_the_usdt_price_at_start(tmp_path: Path) -> None:
    """The watch runs beside the bar loop, and its first poll lands before any step."""
    prices: list[Decimal] = []
    composition = _build(tmp_path, mainnet=True, prices=prices)
    feed = composition.feed
    assert isinstance(feed, FakeFeed)

    asyncio.run(drive(composition, feed, until=lambda: bool(prices)))

    watch = composition.stable_price_watch
    assert watch is not None
    assert (len(prices) >= 1, watch.price(BASE)) == (True, Decimal("1.0002"))


def test_the_default_price_source_is_krakens_public_book(tmp_path: Path) -> None:
    """Without an override serve builds the keyless Kraken adapter, and release closes it."""
    composition = _build(tmp_path, mainnet=True)
    kraken = composition.kraken_adapter
    asyncio.run(serve_module._release(composition))

    assert isinstance(kraken, KrakenPublicAdapter)

    assert kraken.client.is_closed
