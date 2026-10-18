"""Tests for which endpoints and keys each tier may use (ADR-024 §4, #970).

Invariants protected here:

- ``qa`` is a tier: it loads, and like dev and sandbox it may point every
  endpoint at a loopback fake;
- staging and prod refuse a loopback value for every endpoint, so neither
  can run against a fake by mistake;
- prod refuses a non-default Frankfurter, Kraken, Telegram or Slack base,
  so prod talks only to the real vendors;
- qa and staging refuse a trading key; dev and sandbox keep their testnet
  keys;
- every refusal names the field or variable and never echoes its value,
  since the ping URL, the DSN and the keys carry credentials.
"""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.infrastructure.config import ConfigError, load_config
from hermes.infrastructure.config.loader import env_tier_problems
from hermes.infrastructure.config.models import (
    FrankfurterConfig,
    KrakenPublicConfig,
    MarketDataConfig,
)

_REPO_CONFIG = Path(__file__).resolve().parents[4] / "config"
_MARKER = "sekrit42"
_LOOPBACK = f"http://{_MARKER}@127.0.0.1:9000"

# (field named in the error, env var that sets it, a loopback value)
_ENDPOINTS: list[tuple[str, str, str]] = [
    ("market_data.base_url", "HERMES_MARKET_DATA__BASE_URL", f"http://127.0.0.1:9000/{_MARKER}"),
    ("frankfurter.base_url", "HERMES_FRANKFURTER__BASE_URL", f"http://127.0.0.1:9000/{_MARKER}"),
    ("kraken_public.base_url", "HERMES_KRAKEN_PUBLIC__BASE_URL", f"http://localhost:9/{_MARKER}"),
    ("healthchecks.ping_url", "HERMES_HEALTHCHECKS__PING_URL", f"{_LOOPBACK}/ping/{_MARKER}"),
    ("sentry.dsn", "HERMES_SENTRY__DSN", f"{_LOOPBACK}/1"),
    ("HERMES_TELEGRAM__API_BASE", "HERMES_TELEGRAM__API_BASE", f"http://[::1]:8081/{_MARKER}"),
    ("HERMES_SLACK__API_BASE", "HERMES_SLACK__API_BASE", f"http://127.0.0.1:8082/{_MARKER}"),
]
_TRADING_KEYS = (
    "HERMES_BINANCE__API_KEY",
    "HERMES_BINANCE__API_SECRET",
    "HERMES_KRAKEN__API_KEY",
    "HERMES_KRAKEN__API_SECRET",
)


def _refusal(env: dict[str, str]) -> str:
    """Load the shipped config with ``env`` and return the refusal's text."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(_REPO_CONFIG, env=env)
    return str(excinfo.value)


def test_qa_is_a_tier() -> None:
    """HERMES_ENV=qa loads the shipped config as the qa tier."""
    assert load_config(_REPO_CONFIG, env={"HERMES_ENV": "qa"}).env == "qa"


@pytest.mark.parametrize("tier", ["dev", "sandbox", "qa"])
def test_the_lower_tiers_may_point_every_endpoint_at_a_loopback_fake(tier: str) -> None:
    """With every endpoint on loopback at once, dev, sandbox and qa still load."""
    env = {"HERMES_ENV": tier} | {var: value for _, var, value in _ENDPOINTS}

    config = load_config(_REPO_CONFIG, env=env)

    assert (config.market_data.base_url, config.frankfurter.base_url) == (
        f"http://127.0.0.1:9000/{_MARKER}",
        f"http://127.0.0.1:9000/{_MARKER}",
    )


@pytest.mark.parametrize("tier", ["staging", "prod"])
@pytest.mark.parametrize(("field", "var", "value"), _ENDPOINTS)
def test_staging_and_prod_refuse_a_loopback_endpoint_by_name(
    tier: str, field: str, var: str, value: str
) -> None:
    """A loopback value is refused above qa, naming the field and not its value."""
    message = _refusal({"HERMES_ENV": tier, var: value})

    assert f"{field} points at a loopback host, which only dev, qa, sandbox may use" in message
    assert _MARKER not in message


@pytest.mark.parametrize(
    ("field", "var", "value"),
    [
        ("frankfurter.base_url", "HERMES_FRANKFURTER__BASE_URL", f"https://fx.example/{_MARKER}"),
        (
            "kraken_public.base_url",
            "HERMES_KRAKEN_PUBLIC__BASE_URL",
            f"https://k.example/{_MARKER}",
        ),
        ("HERMES_TELEGRAM__API_BASE", "HERMES_TELEGRAM__API_BASE", f"https://t.example/{_MARKER}"),
        ("HERMES_SLACK__API_BASE", "HERMES_SLACK__API_BASE", f"https://s.example/{_MARKER}"),
    ],
)
def test_prod_refuses_a_non_default_vendor_endpoint(field: str, var: str, value: str) -> None:
    """Prod talks only to the real vendor; another https host is refused by name."""
    message = _refusal({"HERMES_ENV": "prod", var: value})

    assert f"{field} is not the default endpoint, which prod requires" in message
    assert _MARKER not in message


def test_staging_may_use_another_https_vendor_endpoint() -> None:
    """Below prod a non-default https endpoint is allowed, e.g. a mirror for the lab."""
    env = {
        "HERMES_ENV": "staging",
        "HERMES_FRANKFURTER__BASE_URL": "https://fx.example/v1",
        "HERMES_TELEGRAM__API_BASE": "https://t.example",
    }

    assert load_config(_REPO_CONFIG, env=env).frankfurter.base_url == "https://fx.example/v1"


def test_prod_accepts_the_default_endpoints_with_a_trailing_slash() -> None:
    """The shipped defaults, written with a trailing slash, are still the default."""
    env = {
        "HERMES_ENV": "prod",
        "HERMES_FRANKFURTER__BASE_URL": "https://api.frankfurter.dev/v1/",
        "HERMES_TELEGRAM__API_BASE": "https://api.telegram.org/",
    }

    assert load_config(_REPO_CONFIG, env=env).env == "prod"


# SEV-1: no rubric row matches; real-capital gate (ADR-003 §00 I-08)
@pytest.mark.parametrize("tier", ["qa", "staging"])
@pytest.mark.parametrize("key", _TRADING_KEYS)
def test_qa_and_staging_refuse_a_trading_key_by_name(tier: str, key: str) -> None:
    """A key that can place orders stops qa and staging from starting, unechoed."""
    message = _refusal({"HERMES_ENV": tier, key: _MARKER})

    assert f"{key} is set, but {tier} holds no trading key" in message
    assert _MARKER not in message


@pytest.mark.parametrize("tier", ["dev", "sandbox"])
def test_dev_and_sandbox_keep_their_testnet_keys(tier: str) -> None:
    """The testnet tiers may hold every trading key."""
    env = {"HERMES_ENV": tier} | dict.fromkeys(_TRADING_KEYS, _MARKER)

    assert load_config(_REPO_CONFIG, env=env).env == tier


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_trading_key_is_not_a_key(blank: str) -> None:
    """An empty variable, as a rendered env file may leave, holds no key."""
    assert env_tier_problems("staging", {"HERMES_BINANCE__API_KEY": blank}) == []


def test_every_problem_is_reported_at_once() -> None:
    """Two problems in one environment are both named, so one restart fixes both."""
    env = {
        "HERMES_KRAKEN__API_KEY": "x",
        "HERMES_SLACK__API_BASE": "http://127.0.0.1:1",
    }

    assert env_tier_problems("staging", env) == [
        "HERMES_KRAKEN__API_KEY is set, but staging holds no trading key",
        "HERMES_SLACK__API_BASE points at a loopback host, which only dev, qa, sandbox may use",
    ]


@given(octets=st.tuples(*[st.integers(0, 255)] * 3), port=st.integers(1, 65535))
def test_any_127_address_counts_as_loopback_in_staging(
    octets: tuple[int, int, int], port: int
) -> None:
    """Every host in 127.0.0.0/8 is a fake, not only 127.0.0.1."""
    host = "127." + ".".join(map(str, octets))

    problems = env_tier_problems("staging", {"HERMES_TELEGRAM__API_BASE": f"http://{host}:{port}"})

    assert problems == [
        "HERMES_TELEGRAM__API_BASE points at a loopback host, which only dev, qa, sandbox may use"
    ]


@pytest.mark.parametrize("model", [FrankfurterConfig, KrakenPublicConfig])
def test_vendor_bases_accept_http_on_loopback(
    model: type[FrankfurterConfig | KrakenPublicConfig],
) -> None:
    """A loopback fake needs no certificate, so http is accepted there."""
    assert model(base_url="http://127.0.0.1:9000/x").base_url == "http://127.0.0.1:9000/x"


@pytest.mark.parametrize("model", [FrankfurterConfig, KrakenPublicConfig])
@pytest.mark.parametrize("url", ["http://fx.example", "ftp://127.0.0.1", "127.0.0.1:9000"])
def test_vendor_bases_refuse_http_off_loopback_and_other_schemes(
    model: type[FrankfurterConfig | KrakenPublicConfig], url: str
) -> None:
    """Plain http to another host would carry requests in clear."""
    with pytest.raises(ValueError, match=r"must be https://, or http:// on a loopback host"):
        model(base_url=url)


def test_market_data_accepts_a_loopback_fake_without_its_trailing_slash() -> None:
    """The fake market service is an allowed market-data host."""
    assert MarketDataConfig(base_url="http://127.0.0.1:9000/binance/").base_url == (
        "http://127.0.0.1:9000/binance"
    )


def test_market_data_still_refuses_any_other_host() -> None:
    """Only the public host and loopback fakes are allowed."""
    with pytest.raises(ValueError, match=r"not an allowed market-data host .* or a loopback fake"):
        MarketDataConfig(base_url="https://api.binance.com")
