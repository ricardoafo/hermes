"""Tests for :class:`MarketDataConfig` and its loader wiring (live-bar-stream D-2, D-4).

Invariants:

- the host allow-list holds exactly one market-data host: no trading host, no
  testnet, no plain HTTP, no look-alike;
- warm-up depth never falls below what the strategy's indicators need
  (45 4h bars, 200 1d bars) and never exceeds the klines page (999);
- the shipped ``config/market_data.yaml`` loads and carries the pre-registered
  pairs and both intervals;
- ``HERMES_MARKET_DATA__<FIELD>`` overrides reach the model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair
from hermes.infrastructure.config import ConfigError, MarketDataConfig, load_config
from hermes.infrastructure.config.loader import load_yaml_model

_REPO_CONFIG = Path(__file__).parents[4] / "config"


class TestMarketDataConfigHostAllowList:
    """Only ``https://data-api.binance.vision`` is accepted as ``base_url``."""

    def test_market_data_default_host_is_the_public_market_data_host(self) -> None:
        """The default base_url is the market-data-only host."""
        assert MarketDataConfig().base_url == "https://data-api.binance.vision"

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.binance.com",
            "https://testnet.binance.vision",
            "https://stream.binance.com",
            "http://data-api.binance.vision",
            "https://data-api.binance.vision.example.org",
            "https://example.org/https://data-api.binance.vision",
            "",
        ],
    )
    def test_market_data_rejects_any_other_host(self, url: str) -> None:
        """Trading hosts, testnet, plain HTTP and look-alikes fail validation."""
        with pytest.raises(ValidationError, match="not an allowed market-data host"):
            MarketDataConfig(base_url=url)

    def test_market_data_trailing_slash_is_normalised_away(self) -> None:
        """A trailing slash is stripped so URL joining never doubles it."""
        assert MarketDataConfig(base_url="https://data-api.binance.vision/").base_url == (
            "https://data-api.binance.vision"
        )


class TestMarketDataConfigWarmupFloors:
    """Warm-up depth: floor 45 (4h) and 200 (1d), cap 999 (one klines page)."""

    @pytest.mark.parametrize(("field", "floor"), [("warmup_bars_4h", 45), ("warmup_bars_1d", 200)])
    def test_market_data_rejects_warmup_below_the_floor(self, field: str, floor: int) -> None:
        """One bar below the floor is rejected."""
        with pytest.raises(ValidationError, match="greater than or equal"):
            MarketDataConfig.model_validate({field: floor - 1})

    @pytest.mark.parametrize(("field", "floor"), [("warmup_bars_4h", 45), ("warmup_bars_1d", 200)])
    def test_market_data_accepts_warmup_exactly_at_the_floor(self, field: str, floor: int) -> None:
        """The floor itself is valid."""
        assert getattr(MarketDataConfig.model_validate({field: floor}), field) == floor

    @pytest.mark.parametrize("field", ["warmup_bars_4h", "warmup_bars_1d"])
    def test_market_data_rejects_warmup_above_one_klines_page(self, field: str) -> None:
        """1000 would need a 1001-row page: rejected, 999 is the cap."""
        with pytest.raises(ValidationError, match="less than or equal"):
            MarketDataConfig.model_validate({field: 1000})

    def test_market_data_defaults_warm_up_300_bars_per_stream(self) -> None:
        """D-4: 300 closed bars for both intervals, the strategy's own buffer cap."""
        config = MarketDataConfig()
        assert (config.warmup_bars_4h, config.warmup_bars_1d) == (300, 300)

    @given(depth=st.integers(min_value=200, max_value=999))
    def test_market_data_warmup_bars_returns_the_depth_for_each_interval(self, depth: int) -> None:
        """warmup_bars(interval) maps 4h and 1d to their own configured depth."""
        config = MarketDataConfig(warmup_bars_4h=depth, warmup_bars_1d=999 - depth + 200)
        assert (config.warmup_bars(KlineInterval.H4), config.warmup_bars(KlineInterval.D1)) == (
            depth,
            999 - depth + 200,
        )

    def test_market_data_warmup_bars_rejects_an_unconfigured_interval(self) -> None:
        """Only 4h and 1d have a depth."""
        with pytest.raises(ValueError, match="no warm-up depth"):
            MarketDataConfig().warmup_bars(KlineInterval.H1)


class TestMarketDataConfigStreams:
    """Pairs and intervals: parsed, de-duplicated, limited to what the strategy consumes."""

    def test_market_data_default_pairs_are_the_pre_registered_three(self) -> None:
        """Pre-registration v1 section 3: BTC/USDT, ETH/USDT, SOL/USDT."""
        assert MarketDataConfig().pairs == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

    def test_market_data_parsed_pairs_returns_pair_values_in_order(self) -> None:
        """parsed_pairs turns the text into Pair value objects, same order."""
        assert MarketDataConfig(pairs=["ETH/USDT", "BTC/USDT"]).parsed_pairs() == (
            Pair(base=Currency.ETH, quote=Currency.USDT),
            Pair(base=Currency.BTC, quote=Currency.USDT),
        )

    def test_market_data_pairs_are_canonicalised(self) -> None:
        """Concatenated and lowercase spellings normalise to BASE/QUOTE."""
        assert MarketDataConfig(pairs=["btcusdt"]).pairs == ["BTC/USDT"]

    @pytest.mark.parametrize("pairs", [["NOPE/USDT"], ["BTC"], ["BTC/BTC"]])
    def test_market_data_rejects_an_unparseable_pair(self, pairs: list[str]) -> None:
        """An unknown currency or a malformed pair fails at config load."""
        with pytest.raises(ValidationError, match="not a known BASE/QUOTE pair"):
            MarketDataConfig(pairs=pairs)

    def test_market_data_rejects_duplicate_pairs(self) -> None:
        """BTC/USDT twice (even spelled differently) would double every bar."""
        with pytest.raises(ValidationError, match="duplicates"):
            MarketDataConfig(pairs=["BTC/USDT", "BTCUSDT"])

    def test_market_data_rejects_an_empty_pair_list(self) -> None:
        """At least one pair is required."""
        with pytest.raises(ValidationError, match="at least 1 item"):
            MarketDataConfig(pairs=[])

    def test_market_data_default_intervals_are_4h_and_1d(self) -> None:
        """The strategy never enters without 1d bars, so both are followed."""
        assert MarketDataConfig().intervals == [KlineInterval.H4, KlineInterval.D1]

    def test_market_data_rejects_intervals_outside_4h_and_1d(self) -> None:
        """1m, 5m and 1h are out of scope for the live feed."""
        with pytest.raises(ValidationError, match="unsupported intervals"):
            MarketDataConfig(intervals=[KlineInterval.H1])

    def test_market_data_rejects_duplicate_intervals(self) -> None:
        """An interval listed twice would double its stream."""
        with pytest.raises(ValidationError, match="intervals contain duplicates"):
            MarketDataConfig(intervals=[KlineInterval.H4, KlineInterval.H4])


class TestMarketDataConfigTiming:
    """Poll timing and budget bounds."""

    def test_market_data_defaults_match_the_spec(self) -> None:
        """Settle 5 s, retry 5 s, slow retry 30 s, stale after 900 s, bucket 600 per 60 s."""
        config = MarketDataConfig()
        assert (
            config.settle_delay_seconds,
            config.retry_interval_seconds,
            config.slow_retry_interval_seconds,
            config.stale_after_seconds,
            config.timeout_sec,
            config.rate_limit_max_weight,
            config.rate_limit_window_seconds,
        ) == (5.0, 5.0, 30.0, 900.0, 10.0, 600, 60)

    def test_market_data_rejects_a_slow_retry_faster_than_the_normal_retry(self) -> None:
        """Slowing down must not speed up."""
        with pytest.raises(ValidationError, match="slow_retry_interval_seconds must be >="):
            MarketDataConfig(retry_interval_seconds=10, slow_retry_interval_seconds=5)

    def test_market_data_rejects_a_bucket_too_small_for_one_klines_request(self) -> None:
        """A bucket under 3 weight could never grant a time call plus a klines call."""
        with pytest.raises(ValidationError, match="greater than or equal to 3"):
            MarketDataConfig(rate_limit_max_weight=2)

    def test_market_data_rejects_a_bucket_above_binances_cap(self) -> None:
        """The exchange caps weight at 6000 per minute."""
        with pytest.raises(ValidationError, match="less than or equal to 6000"):
            MarketDataConfig(rate_limit_max_weight=6001)

    def test_market_data_rejects_unknown_fields(self) -> None:
        """A typo in the YAML fails loudly instead of being ignored."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            MarketDataConfig(settle_delay="5")  # type: ignore[call-arg]

    def test_market_data_config_is_frozen(self) -> None:
        """Config is immutable after load."""
        with pytest.raises(ValidationError, match="frozen"):
            MarketDataConfig().settle_delay_seconds = 1.0


class TestMarketDataConfigLoading:
    """The shipped YAML and the env-override path."""

    def test_market_data_shipped_yaml_equals_the_model_defaults(self) -> None:
        """config/market_data.yaml and the built-in defaults agree (the probe relies on this)."""
        shipped = load_yaml_model(
            _REPO_CONFIG / "market_data.yaml",
            MarketDataConfig,
            env_section="MARKET_DATA",
            env={},
        )
        assert shipped == MarketDataConfig()

    def test_market_data_is_part_of_the_composite_config(self) -> None:
        """load_config() exposes market_data with the three pre-registered pairs."""
        assert load_config(_REPO_CONFIG, env={}).market_data.pairs == [
            "BTC/USDT",
            "ETH/USDT",
            "SOL/USDT",
        ]

    def test_market_data_env_override_reaches_the_model(self) -> None:
        """HERMES_MARKET_DATA__WARMUP_BARS_4H=100 overrides the YAML value."""
        shipped = load_yaml_model(
            _REPO_CONFIG / "market_data.yaml",
            MarketDataConfig,
            env_section="MARKET_DATA",
            env={"HERMES_MARKET_DATA__WARMUP_BARS_4H": "100"},
        )
        assert shipped.warmup_bars_4h == 100

    def test_market_data_env_override_cannot_point_at_a_trading_host(self) -> None:
        """Overriding base_url through the environment still hits the allow-list."""
        with pytest.raises(ConfigError, match="not an allowed market-data host"):
            load_yaml_model(
                _REPO_CONFIG / "market_data.yaml",
                MarketDataConfig,
                env_section="MARKET_DATA",
                env={"HERMES_MARKET_DATA__BASE_URL": "https://api.binance.com"},
            )

    def test_market_data_does_not_depend_on_allow_mainnet(self) -> None:
        """The source loads with allow_mainnet false in prod: it is not a trading path."""
        config = load_config(_REPO_CONFIG, env={"HERMES_ENV": "prod"})
        assert (config.allow_mainnet, config.market_data.base_url) == (
            False,
            "https://data-api.binance.vision",
        )
