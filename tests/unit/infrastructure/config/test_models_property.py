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
from pydantic import BaseModel, ValidationError

from hermes.application.execution.prereg_book import ExecutionModel, ExitPricing
from hermes.application.strategy_protocol.strategy_factory import (
    DONCHIAN_BREAKOUT,
    known_signals,
    strategy_kind,
)
from hermes.infrastructure.config import (
    BinanceExchangeConfig,
    KrakenPublicConfig,
    MarketDataConfig,
    MLConfig,
    SentryConfig,
    StrategiesConfig,
    TaxGuardConfig,
)
from hermes.infrastructure.config.models import (
    FrankfurterConfig,
    HealthchecksConfig,
    KlinesConfig,
)

# Deterministic profile — CI must be reproducible.
_SETTINGS = settings(max_examples=50, deadline=None, derandomize=True)


def _assert_field_in_validation_error(
    data: dict[str, Any], model: type[BaseModel], field: str
) -> None:
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


def test_strategy_without_an_exit_pricing_prices_exits_as_v1() -> None:
    """A roster entry written before v2 keeps v1's stop-market exit (v2-strategy-config D-2)."""
    entry = StrategiesConfig.model_validate({"strategies": [_strategy_entry()]}).strategies[0]
    assert entry.exit_pricing is ExitPricing.STOP_ORDER


def test_strategy_reads_bar_close_as_the_close_based_exit() -> None:
    """``exit_pricing: bar_close`` in YAML selects the v2 close-based exit."""
    data = {"strategies": [_strategy_entry(exit_pricing="bar_close")]}
    entry = StrategiesConfig.model_validate(data).strategies[0]
    assert entry.exit_pricing is ExitPricing.BAR_CLOSE


@_SETTINGS
@given(text=st.text(max_size=20).filter(lambda t: t not in {p.value for p in ExitPricing}))
def test_strategy_rejects_an_unknown_exit_pricing(text: str) -> None:
    """Any spelling but stop_order or bar_close fails at load instead of pricing as v1."""
    _assert_field_in_validation_error(
        {"strategies": [_strategy_entry(exit_pricing=text)]}, StrategiesConfig, "exit_pricing"
    )


def test_strategy_without_a_signal_is_the_donchian_breakout() -> None:
    """A v1 or v2 entry has no ``signal:`` key and builds today's adapter (issue #1074)."""
    entry = StrategiesConfig.model_validate({"strategies": [_strategy_entry()]}).strategies[0]
    assert entry.strategy_kind() == strategy_kind(None)


def test_strategy_reads_a_known_signal() -> None:
    """``signal: donchian-breakout`` names the same kind as leaving it out."""
    data = {"strategies": [_strategy_entry(signal=DONCHIAN_BREAKOUT)]}
    entry = StrategiesConfig.model_validate(data).strategies[0]
    assert entry.strategy_kind() == strategy_kind(DONCHIAN_BREAKOUT)


@_SETTINGS
@given(text=st.text(max_size=20).filter(lambda t: t not in known_signals()))
def test_strategy_rejects_an_unknown_signal(text: str) -> None:
    """A misspelt signal fails at load instead of trading the default rule (contract §2a)."""
    _assert_field_in_validation_error(
        {"strategies": [_strategy_entry(signal=text)]}, StrategiesConfig, "signal"
    )


@_SETTINGS
@given(
    pricing=st.sampled_from(list(ExitPricing)),
    depth=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1"), places=2),
)
def test_execution_model_carries_the_selected_pricing(pricing: ExitPricing, depth: Decimal) -> None:
    """The model a strategy builds prices with its own pricing at the given depth, §6.2 bps kept."""
    entry = StrategiesConfig.model_validate(
        {"strategies": [_strategy_entry(exit_pricing=pricing.value)]}
    ).strategies[0]
    assert entry.execution_model(depth_frac=depth) == ExecutionModel(
        depth_frac=depth, exit_pricing=pricing
    )


def test_execution_model_defaults_to_the_preregistered_depth() -> None:
    """Without a depth the model keeps the shared default depth fraction."""
    entry = StrategiesConfig.model_validate({"strategies": [_strategy_entry()]}).strategies[0]
    assert entry.execution_model().depth_frac == ExecutionModel().depth_frac


def test_strategy_rejects_quote_outside_the_currency_allowlist() -> None:
    """Quote must be a known Currency; a typo such as USTD fails validation."""
    _assert_field_in_validation_error(
        {"strategies": [_strategy_entry(quote="USTD")]},
        StrategiesConfig,
        "quote",
    )


# ---------------------------------------------------------------------------
# Every numeric and length bound, pinned at the limit and one step past it
# ---------------------------------------------------------------------------

_BINANCE: dict[str, Any] = {
    "base_url": "https://x.example",
    "ws_url": "wss://x.example/ws",
    "recv_window_ms": 5000,
    "testnet": True,
}
_KLINES: dict[str, Any] = {
    "store_db_path": "k.db",
    "rate_limit_max_weight": 5000,
    "rate_limit_window_seconds": 60,
}
_TAX_GUARD: dict[str, Any] = {
    "annual_realised_pnl_alert_eur": "1000",
    "wash_sale_window_days": 30,
    "max_position_pct_portfolio": "0.25",
    "recharacterization_window_days": 90,
    "trade_count_90d_max": 500,
    "distinct_assets_90d_max": 12,
    "fee_income_90d_max_eur": "0",
}
_ML: dict[str, Any] = {
    "supervisor_model": "claude",
    "temperature": "0.2",
    "max_tokens": 100,
    "enabled": True,
}
# Slow retry must stay >= retry, so the slow-retry floor is probed with a tiny retry.
_FAST_RETRY: dict[str, Any] = {"retry_interval_seconds": 0.001}

# (model, valid base, field, value exactly at the limit)
_AT_LIMIT: list[tuple[type[Any], dict[str, Any], str, Any]] = [
    (BinanceExchangeConfig, _BINANCE, "recv_window_ms", 1),
    (BinanceExchangeConfig, _BINANCE, "recv_window_ms", 60_000),
    (FrankfurterConfig, {}, "timeout_sec", 0.001),
    (FrankfurterConfig, {}, "requests_per_minute", 1),
    (FrankfurterConfig, {}, "requests_per_minute", 6000),
    (KrakenPublicConfig, {}, "timeout_sec", 0.001),
    (KrakenPublicConfig, {}, "requests_per_second", 1),
    (KrakenPublicConfig, {}, "requests_per_second", 60),
    (KrakenPublicConfig, {}, "fees_cache_ttl_seconds", 0),
    (KrakenPublicConfig, {}, "fees_cache_ttl_seconds", 604_800),
    (MarketDataConfig, {}, "settle_delay_seconds", 0.001),
    (MarketDataConfig, {}, "settle_delay_seconds", 60),
    (MarketDataConfig, {}, "retry_interval_seconds", 0.001),
    (MarketDataConfig, {"slow_retry_interval_seconds": 300}, "retry_interval_seconds", 300),
    (MarketDataConfig, _FAST_RETRY, "slow_retry_interval_seconds", 0.001),
    (MarketDataConfig, {}, "slow_retry_interval_seconds", 3_600),
    (MarketDataConfig, {}, "stale_after_seconds", 0.001),
    (MarketDataConfig, {}, "stale_after_seconds", 86_400),
    (MarketDataConfig, {}, "timeout_sec", 0.001),
    (MarketDataConfig, {}, "rate_limit_max_weight", 3),
    (MarketDataConfig, {}, "rate_limit_max_weight", 6_000),
    (MarketDataConfig, {}, "rate_limit_window_seconds", 1),
    (MarketDataConfig, {}, "rate_limit_window_seconds", 3_600),
    (HealthchecksConfig, {}, "timeout_sec", 0.001),
    (HealthchecksConfig, {}, "requests_per_minute", 1),
    (HealthchecksConfig, {}, "requests_per_minute", 6000),
    (SentryConfig, {}, "environment", "d"),
    (KlinesConfig, _KLINES, "store_db_path", "k"),
    (KlinesConfig, _KLINES, "rate_limit_max_weight", 1),
    (KlinesConfig, _KLINES, "rate_limit_max_weight", 6_000),
    (KlinesConfig, _KLINES, "rate_limit_window_seconds", 1),
    (KlinesConfig, _KLINES, "rate_limit_window_seconds", 3_600),
    (TaxGuardConfig, _TAX_GUARD, "wash_sale_window_days", 1),
    (TaxGuardConfig, _TAX_GUARD, "wash_sale_window_days", 365),
    (TaxGuardConfig, _TAX_GUARD, "recharacterization_window_days", 1),
    (TaxGuardConfig, _TAX_GUARD, "recharacterization_window_days", 365),
    (TaxGuardConfig, _TAX_GUARD, "trade_count_90d_max", 1),
    (TaxGuardConfig, _TAX_GUARD, "distinct_assets_90d_max", 1),
    (MLConfig, _ML, "max_tokens", 1),
    (MLConfig, _ML, "max_tokens", 200_000),
    (MLConfig, _ML, "supervisor_model", "m"),
]

# (model, valid base, field, value one step past the limit, the exact pydantic message)
_PAST_LIMIT: list[tuple[type[Any], dict[str, Any], str, Any, str]] = [
    (
        BinanceExchangeConfig,
        _BINANCE,
        "recv_window_ms",
        60_001,
        "Input should be less than or equal to 60000",
    ),
    (FrankfurterConfig, {}, "timeout_sec", 0, "Input should be greater than 0"),
    (FrankfurterConfig, {}, "requests_per_minute", 0, "Input should be greater than 0"),
    (
        FrankfurterConfig,
        {},
        "requests_per_minute",
        6001,
        "Input should be less than or equal to 6000",
    ),
    (KrakenPublicConfig, {}, "timeout_sec", 0, "Input should be greater than 0"),
    (KrakenPublicConfig, {}, "requests_per_second", 0, "Input should be greater than 0"),
    (KrakenPublicConfig, {}, "requests_per_second", 61, "Input should be less than or equal to 60"),
    (
        KrakenPublicConfig,
        {},
        "fees_cache_ttl_seconds",
        -1,
        "Input should be greater than or equal to 0",
    ),
    (
        KrakenPublicConfig,
        {},
        "fees_cache_ttl_seconds",
        604_801,
        "Input should be less than or equal to 604800",
    ),
    (MarketDataConfig, {}, "settle_delay_seconds", 0, "Input should be greater than 0"),
    (
        MarketDataConfig,
        {},
        "settle_delay_seconds",
        60.001,
        "Input should be less than or equal to 60",
    ),
    (MarketDataConfig, {}, "retry_interval_seconds", 0, "Input should be greater than 0"),
    (
        MarketDataConfig,
        {"slow_retry_interval_seconds": 3_600},
        "retry_interval_seconds",
        300.001,
        "Input should be less than or equal to 300",
    ),
    (
        MarketDataConfig,
        _FAST_RETRY,
        "slow_retry_interval_seconds",
        0,
        "Input should be greater than 0",
    ),
    (
        MarketDataConfig,
        {},
        "slow_retry_interval_seconds",
        3_600.001,
        "Input should be less than or equal to 3600",
    ),
    (MarketDataConfig, {}, "stale_after_seconds", 0, "Input should be greater than 0"),
    (
        MarketDataConfig,
        {},
        "stale_after_seconds",
        86_400.001,
        "Input should be less than or equal to 86400",
    ),
    (MarketDataConfig, {}, "timeout_sec", 0, "Input should be greater than 0"),
    (MarketDataConfig, {}, "rate_limit_window_seconds", 0, "Input should be greater than 0"),
    (
        MarketDataConfig,
        {},
        "rate_limit_window_seconds",
        3_601,
        "Input should be less than or equal to 3600",
    ),
    (HealthchecksConfig, {}, "timeout_sec", 0, "Input should be greater than 0"),
    (HealthchecksConfig, {}, "requests_per_minute", 0, "Input should be greater than 0"),
    (
        HealthchecksConfig,
        {},
        "requests_per_minute",
        6001,
        "Input should be less than or equal to 6000",
    ),
    (SentryConfig, {}, "environment", "", "String should have at least 1 character"),
    (KlinesConfig, _KLINES, "store_db_path", "", "String should have at least 1 character"),
    (KlinesConfig, _KLINES, "rate_limit_max_weight", 0, "Input should be greater than 0"),
    (
        KlinesConfig,
        _KLINES,
        "rate_limit_max_weight",
        6_001,
        "Input should be less than or equal to 6000",
    ),
    (KlinesConfig, _KLINES, "rate_limit_window_seconds", 0, "Input should be greater than 0"),
    (
        KlinesConfig,
        _KLINES,
        "rate_limit_window_seconds",
        3_601,
        "Input should be less than or equal to 3600",
    ),
    (
        TaxGuardConfig,
        _TAX_GUARD,
        "recharacterization_window_days",
        0,
        "Input should be greater than or equal to 1",
    ),
    (
        TaxGuardConfig,
        _TAX_GUARD,
        "recharacterization_window_days",
        366,
        "Input should be less than or equal to 365",
    ),
    (
        TaxGuardConfig,
        _TAX_GUARD,
        "trade_count_90d_max",
        0,
        "Input should be greater than or equal to 1",
    ),
    (
        TaxGuardConfig,
        _TAX_GUARD,
        "distinct_assets_90d_max",
        0,
        "Input should be greater than or equal to 1",
    ),
    (MLConfig, _ML, "max_tokens", 200_001, "Input should be less than or equal to 200000"),
    (MLConfig, _ML, "supervisor_model", "", "String should have at least 1 character"),
]


@pytest.mark.parametrize(
    ("model", "base", "field", "value"),
    _AT_LIMIT,
    ids=[f"{m.__name__}.{f}={v!r}" for m, _, f, v in _AT_LIMIT],
)
def test_config_bound_accepts_the_value_exactly_at_the_limit(
    model: type[Any], base: dict[str, Any], field: str, value: Any
) -> None:
    """A value exactly at a declared bound loads unchanged: every declared bound is inclusive."""
    loaded = model.model_validate({**base, field: value})
    assert getattr(loaded, field) == value


@pytest.mark.parametrize(
    ("model", "base", "field", "value", "message"),
    _PAST_LIMIT,
    ids=[f"{m.__name__}.{f}={v!r}" for m, _, f, v, _ in _PAST_LIMIT],
)
def test_config_bound_rejects_the_value_one_step_past_the_limit(
    model: type[Any], base: dict[str, Any], field: str, value: Any, message: str
) -> None:
    """A value one step past a declared bound fails on that field alone, naming the bound."""
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate({**base, field: value})
    assert [(e["loc"], e["msg"]) for e in excinfo.value.errors()] == [((field,), message)]


def test_strategy_rejects_an_empty_name() -> None:
    """A strategy name needs at least one character: it keys event payloads and log lines."""
    with pytest.raises(ValidationError) as excinfo:
        StrategiesConfig.model_validate({"strategies": [_strategy_entry(name="")]})
    assert [(e["loc"], e["msg"]) for e in excinfo.value.errors()] == [
        (("strategies", 0, "name"), "String should have at least 1 character")
    ]


def test_strategy_roster_rejects_an_empty_list() -> None:
    """The roster needs at least one strategy: an empty strategies.yaml never starts the bot."""
    with pytest.raises(ValidationError) as excinfo:
        StrategiesConfig.model_validate({"strategies": []})
    assert [(e["loc"], e["msg"]) for e in excinfo.value.errors()] == [
        (("strategies",), "List should have at least 1 item after validation, not 0")
    ]


def test_strategy_roster_accepts_a_single_one_character_strategy() -> None:
    """One strategy named with one character is the smallest valid roster."""
    roster = StrategiesConfig.model_validate({"strategies": [_strategy_entry(name="x")]})
    assert [s.name for s in roster.strategies] == ["x"]


# ---------------------------------------------------------------------------
# Opt-in integrations: an empty URL or DSN means disabled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ping_url", "enabled"), [("", False), ("https://hc-ping.com/0000-uuid", True)]
)
def test_healthchecks_is_enabled_only_with_a_ping_url(ping_url: str, enabled: bool) -> None:
    """Healthchecks is enabled exactly when a ping URL is configured; blank means opted out."""
    assert HealthchecksConfig(ping_url=ping_url).enabled is enabled


@pytest.mark.parametrize(("dsn", "enabled"), [("", False), ("https://k@o1.ingest.example/1", True)])
def test_sentry_is_enabled_only_with_a_dsn(dsn: str, enabled: bool) -> None:
    """Sentry is enabled exactly when a DSN is configured; blank means opted out."""
    assert SentryConfig(dsn=dsn).enabled is enabled
