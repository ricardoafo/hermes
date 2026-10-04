"""Property tests: invalid config never validates silently.

The property under test: for every model in the config schema, any input
outside the declared bounds must produce a ``ConfigError`` with a clear
message that names the offending field. There is no "accidentally valid"
malformed config.

This is the ADR-007 property test the issue #103 acceptance criteria
demands: invalid config raises with a clear message.

Tests validate ``model_cls.model_validate(...)`` directly rather than going
through the loader — the loader path is covered by ``test_loader.py``; here
we're pinning the schema invariants themselves.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from hermes.infrastructure.config import (
    BinanceExchangeConfig,
    MLConfig,
    StrategiesConfig,
    TaxGuardConfig,
)

# Deterministic profile — CI must be reproducible.
_SETTINGS = settings(max_examples=50, deadline=None, derandomize=True)


def _assert_field_in_validation_error(data: dict[str, Any], model: type, field: str) -> None:
    """Validate ``data`` against ``model``, expect failure, assert error names ``field``."""
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(data)
    # Every pydantic ValidationError lists offending locs; the field name
    # must appear in at least one loc tuple.
    fields_in_errors = {loc for err in excinfo.value.errors() for loc in err["loc"]}
    assert field in fields_in_errors


# ---------------------------------------------------------------------------
# TaxGuardConfig — three bounded fields, easy to enumerate the failure surface
# ---------------------------------------------------------------------------


@_SETTINGS
@given(days=st.integers(max_value=0))
def test_tax_guard_rejects_non_positive_window_days(days: int) -> None:
    """wash_sale_window_days must be >= 1; zero/negative fail validation."""
    _assert_field_in_validation_error(
        {
            "annual_realised_pnl_alert_eur": "1000",
            "wash_sale_window_days": days,
            "max_position_pct_portfolio": "0.25",
            "recharacterization_window_days": 90,
            "trade_count_90d_max": 500,
            "distinct_assets_90d_max": 12,
            "fee_income_90d_max_eur": "0",
        },
        TaxGuardConfig,
        "wash_sale_window_days",
    )


@_SETTINGS
@given(days=st.integers(min_value=366, max_value=10_000))
def test_tax_guard_rejects_window_days_above_year(days: int) -> None:
    """wash_sale_window_days must be <= 365; larger values fail validation."""
    _assert_field_in_validation_error(
        {
            "annual_realised_pnl_alert_eur": "1000",
            "wash_sale_window_days": days,
            "max_position_pct_portfolio": "0.25",
            "recharacterization_window_days": 90,
            "trade_count_90d_max": 500,
            "distinct_assets_90d_max": 12,
            "fee_income_90d_max_eur": "0",
        },
        TaxGuardConfig,
        "wash_sale_window_days",
    )


@_SETTINGS
@given(pct=st.decimals(min_value=Decimal("1.01"), max_value=Decimal("100"), allow_nan=False))
def test_tax_guard_rejects_pct_above_one(pct: Decimal) -> None:
    """max_position_pct_portfolio > 1 is nonsense — cannot exceed 100% of the portfolio."""
    _assert_field_in_validation_error(
        {
            "annual_realised_pnl_alert_eur": "1000",
            "wash_sale_window_days": 30,
            "max_position_pct_portfolio": str(pct),
            "recharacterization_window_days": 90,
            "trade_count_90d_max": 500,
            "distinct_assets_90d_max": 12,
            "fee_income_90d_max_eur": "0",
        },
        TaxGuardConfig,
        "max_position_pct_portfolio",
    )


# ---------------------------------------------------------------------------
# BinanceExchangeConfig — recv_window_ms bounds + https-only base_url
# ---------------------------------------------------------------------------


@_SETTINGS
@given(ms=st.integers(max_value=0))
def test_binance_rejects_non_positive_recv_window(ms: int) -> None:
    """recv_window_ms must be >= 1; non-positive values fail validation."""
    _assert_field_in_validation_error(
        {
            "base_url": "https://x.example",
            "ws_url": "wss://x.example/ws",
            "recv_window_ms": ms,
            "testnet": True,
        },
        BinanceExchangeConfig,
        "recv_window_ms",
    )


@_SETTINGS
@given(
    scheme=st.sampled_from(["http://", "ftp://", "file://", "ws://"]),
    host=st.text(alphabet="abcdefghijklmnopqrstuvwxyz.", min_size=3, max_size=30),
)
def test_binance_rejects_non_https_base_url(scheme: str, host: str) -> None:
    """base_url must start with https:// — plaintext HTTP is rejected."""
    _assert_field_in_validation_error(
        {
            "base_url": f"{scheme}{host}",
            "ws_url": "wss://x.example/ws",
            "recv_window_ms": 5000,
            "testnet": True,
        },
        BinanceExchangeConfig,
        "base_url",
    )


# ---------------------------------------------------------------------------
# MLConfig — supervisor bounds
# ---------------------------------------------------------------------------


@_SETTINGS
@given(temp=st.decimals(min_value=Decimal("2.01"), max_value=Decimal("100"), allow_nan=False))
def test_ml_rejects_temperature_above_two(temp: Decimal) -> None:
    """Temperature must be <= 2; larger sampling temps are outside model range."""
    _assert_field_in_validation_error(
        {
            "supervisor_model": "claude",
            "temperature": str(temp),
            "max_tokens": 100,
            "enabled": True,
        },
        MLConfig,
        "temperature",
    )


@_SETTINGS
@given(tokens=st.integers(max_value=0))
def test_ml_rejects_non_positive_max_tokens(tokens: int) -> None:
    """max_tokens must be >= 1; zero/negative values fail validation."""
    _assert_field_in_validation_error(
        {
            "supervisor_model": "claude",
            "temperature": "0.2",
            "max_tokens": tokens,
            "enabled": True,
        },
        MLConfig,
        "max_tokens",
    )


# ---------------------------------------------------------------------------
# StrategyConfig — weight in [0, 1]; name non-empty; capital and quote required
# ---------------------------------------------------------------------------


def _strategy_entry(**overrides: Any) -> dict[str, Any]:
    """Return a valid strategy roster entry with ``overrides`` applied."""
    entry: dict[str, Any] = {
        "name": "x",
        "enabled": True,
        "weight": "1.0",
        "capital_budget": "5000",
        "quote": "USDT",
        "sizing": {
            "risk_pct": "0.01",
            "max_position_pct": "0.05",
            "max_total_exposure_pct": "0.15",
        },
    }
    entry.update(overrides)
    return entry


@_SETTINGS
@given(w=st.decimals(min_value=Decimal("1.01"), max_value=Decimal("100"), allow_nan=False))
def test_strategy_rejects_weight_above_one(w: Decimal) -> None:
    """Weight must be <= 1; a strategy cannot hold more than the whole portfolio."""
    _assert_field_in_validation_error(
        {"strategies": [_strategy_entry(weight=str(w))]},
        StrategiesConfig,
        "weight",
    )


@_SETTINGS
@given(budget=st.decimals(max_value=Decimal("0"), allow_nan=False, allow_infinity=False, places=8))
def test_strategy_rejects_non_positive_capital_budget(budget: Decimal) -> None:
    """capital_budget must be > 0; a strategy cannot be seeded with zero or negative capital."""
    _assert_field_in_validation_error(
        {"strategies": [_strategy_entry(capital_budget=str(budget))]},
        StrategiesConfig,
        "capital_budget",
    )


def test_strategy_rejects_missing_quote() -> None:
    """Quote is required: a roster entry without it fails at load, not at first use."""
    entry = _strategy_entry()
    del entry["quote"]
    _assert_field_in_validation_error({"strategies": [entry]}, StrategiesConfig, "quote")


def test_strategy_rejects_missing_capital_budget() -> None:
    """capital_budget is required: there is no silent default seed for the tracker."""
    entry = _strategy_entry()
    del entry["capital_budget"]
    _assert_field_in_validation_error({"strategies": [entry]}, StrategiesConfig, "capital_budget")


def test_strategy_rejects_missing_sizing() -> None:
    """The sizing block is required: an entry without limits fails at load, never trades."""
    entry = _strategy_entry()
    del entry["sizing"]
    _assert_field_in_validation_error({"strategies": [entry]}, StrategiesConfig, "sizing")


@_SETTINGS
@given(
    field=st.sampled_from(["risk_pct", "max_position_pct", "max_total_exposure_pct"]),
    share=st.one_of(
        st.decimals(max_value=Decimal("0"), allow_nan=False, allow_infinity=False),
        st.decimals(min_value=Decimal("1.0001"), allow_nan=False, allow_infinity=False),
    ),
)
def test_strategy_rejects_sizing_share_outside_zero_one(field: str, share: Decimal) -> None:
    """Each sizing share must lie in (0, 1]: zero, negative or above 100% of capital fails."""
    sizing = {"risk_pct": "0.01", "max_position_pct": "0.05", "max_total_exposure_pct": "0.15"}
    sizing[field] = str(share)
    _assert_field_in_validation_error(
        {"strategies": [_strategy_entry(sizing=sizing)]}, StrategiesConfig, field
    )


def test_strategy_rejects_quote_outside_the_currency_allowlist() -> None:
    """Quote must be a known Currency; a typo such as USTD fails validation."""
    _assert_field_in_validation_error(
        {"strategies": [_strategy_entry(quote="USTD")]},
        StrategiesConfig,
        "quote",
    )
