"""Shared fixtures and builders for risk overlay rule tests.

Provides ``make_portfolio_state`` and ``make_market_snapshot`` builder
functions so each test file can construct minimal, override-friendly
snapshots without boilerplate.

Reference: spec-8-risk-overlay change.md § Test plan.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from hermes.domain.risk.state import MarketSnapshot, PortfolioState
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair


def _btc_usdt() -> Pair:
    return Pair(base=Currency.BTC, quote=Currency.USDT)


def _eth_usdt() -> Pair:
    return Pair(base=Currency.ETH, quote=Currency.USDT)


def make_portfolio_state(
    *,
    peak_value: Decimal = Decimal("10000"),
    current_value: Decimal = Decimal("10000"),
    realized_slippage_cumulative: Decimal = Decimal("0"),
    correlations: dict[tuple[Pair, Pair], float] | None = None,
    dd_halt_required: bool = False,
    held_pairs: frozenset[Pair] = frozenset(),
) -> PortfolioState:
    """Build a PortfolioState with safe defaults for testing.

    Args:
        peak_value: Portfolio peak value (default: 10000 — no drawdown).
        current_value: Current portfolio value (default: equal to peak).
        realized_slippage_cumulative: Running cumulative slippage fraction.
        correlations: Pair-pair correlation dict (default: empty — uncorrelated).
        dd_halt_required: Drawdown halt flag (default: False).
        held_pairs: Pairs with an open position (default: none).

    Returns:
        A fully-specified :class:`~hermes.domain.risk.state.PortfolioState`.

    """
    return PortfolioState(
        peak_value=peak_value,
        current_value=current_value,
        realized_slippage_cumulative=realized_slippage_cumulative,
        correlations=correlations if correlations is not None else {},
        dd_halt_required=dd_halt_required,
        held_pairs=held_pairs,
    )


def make_market_snapshot(
    *,
    pair: Pair | None = None,
    current_spread: Decimal = Decimal("0.001"),
    median_spread_1h: Decimal = Decimal("0.001"),
    orderbook_age_seconds: float = 0.5,
    orderbook_timestamp: datetime | None = None,
    realized_slippage_last: Decimal = Decimal("0"),
    stable_price: Decimal | None = None,
    btc_log_return_1d: float | None = 0.0,
    candidate_pair: Pair | None = None,
) -> MarketSnapshot:
    """Build a MarketSnapshot with safe defaults for testing.

    Args:
        pair: Trading pair (default: BTC/USDT).
        current_spread: Current bid-ask spread fraction (default: 0.001).
        median_spread_1h: 1H median spread fraction (default: 0.001).
        orderbook_age_seconds: Orderbook age (default: 0.5s — fresh).
        orderbook_timestamp: UTC timestamp (default: now).
        realized_slippage_last: Last fill slippage fraction (default: 0).
        stable_price: Stablecoin price in USD (default: None — not stable).
        btc_log_return_1d: BTC daily log-return (default: 0.0 — flat).
        candidate_pair: Pair for entry (default: None — no candidate).

    Returns:
        A fully-specified :class:`~hermes.domain.risk.state.MarketSnapshot`.

    """
    return MarketSnapshot(
        pair=pair if pair is not None else _btc_usdt(),
        current_spread=current_spread,
        median_spread_1h=median_spread_1h,
        orderbook_age_seconds=orderbook_age_seconds,
        orderbook_timestamp=(
            orderbook_timestamp if orderbook_timestamp is not None else datetime.now(tz=UTC)
        ),
        realized_slippage_last=realized_slippage_last,
        stable_price=stable_price,
        btc_log_return_1d=btc_log_return_1d,
        candidate_pair=candidate_pair,
    )


@pytest.fixture()
def btc_usdt() -> Pair:
    """BTC/USDT trading pair fixture."""
    return _btc_usdt()


@pytest.fixture()
def eth_usdt() -> Pair:
    """ETH/USDT trading pair fixture."""
    return _eth_usdt()
