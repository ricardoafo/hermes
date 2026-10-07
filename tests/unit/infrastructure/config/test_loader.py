"""Config loader: YAML, env overrides, dotenv, runtime-tier gate and failure paths.

Every test protects one rule of the config-loader contract in
``hermes.infrastructure.config.loader`` (precedence: shell env over
``.env.local`` over the YAML file; see
``docs/decisions/2026-08-23-credential-storage-layered-by-env.md``). A
failure here means the process could start with wrong or corrupted config,
a real-capital risk (ADR-003 §00 invariant I-05).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from textwrap import dedent

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hermes.domain.errors import InfraError
from hermes.domain.value_objects.currency import Currency
from hermes.infrastructure.config import (
    BinanceExchangeConfig,
    ConfigError,
    KrakenPublicConfig,
    MLConfig,
    SentryConfig,
    StrategiesConfig,
    load_config,
)
from hermes.infrastructure.config.loader import load_yaml_model
from hermes.infrastructure.config.models import FrankfurterConfig, HealthchecksConfig

_REPO_CONFIG = Path(__file__).resolve().parents[4] / "config"

# ---------------------------------------------------------------------------
# Fixtures: a full valid config tree written to a tmp dir per test. Values
# differ from the model defaults where possible, so a section read from the
# wrong file (or not read at all) shows up.
# ---------------------------------------------------------------------------

_VALID_STRATEGIES = dedent(
    """
    strategies:
      - name: rebalancer
        enabled: true
        weight: "0.6"
        capital_budget: "3000"
        quote: USDT
        sizing:
          risk_pct: "0.01"
          max_position_pct: "0.05"
          max_total_exposure_pct: "0.15"
      - name: donchian
        enabled: false
        weight: "0.4"
        capital_budget: "2000"
        quote: USDC
        sizing:
          risk_pct: "0.01"
          max_position_pct: "0.05"
          max_total_exposure_pct: "0.15"
    """
).strip()

_VALID_BINANCE = dedent(
    """
    base_url: https://testnet.binance.vision
    ws_url: wss://testnet.binance.vision/ws
    recv_window_ms: 5000
    testnet: true
    """
).strip()

_VALID_FEES = dedent(
    """
    default:
      maker_bps: "10"
      taker_bps: "10"
    venues:
      binance:
        maker_bps: "8"
        taker_bps: "10"
    """
).strip()

_VALID_TAX_GUARD = dedent(
    """
    annual_realised_pnl_alert_eur: "1000"
    wash_sale_window_days: 30
    max_position_pct_portfolio: "0.25"
    recharacterization_window_days: 90
    trade_count_90d_max: 500
    distinct_assets_90d_max: 12
    fee_income_90d_max_eur: "0"
    """
).strip()

_VALID_ML = dedent(
    """
    supervisor_model: claude-sonnet-4-7
    temperature: "0.2"
    max_tokens: 4096
    enabled: true
    """
).strip()

_VALID_KLINES = dedent(
    """
    store_db_path: var/hermes/klines.db
    rate_limit_max_weight: 5000
    rate_limit_window_seconds: 60
    """
).strip()

_VALID_FRANKFURTER = dedent(
    """
    base_url: https://api.frankfurter.app
    timeout_sec: 10.0
    requests_per_minute: 60
    """
).strip()

_VALID_HEALTHCHECKS = dedent(
    """
    ping_url: ""
    timeout_sec: 4.0
    requests_per_minute: 30
    """
).strip()

_VALID_SENTRY = dedent(
    """
    dsn: ""
    environment: sandbox
    """
).strip()

_VALID_KRAKEN_PUBLIC = dedent(
    """
    base_url: https://api.kraken.com
    timeout_sec: 7.5
    requests_per_second: 1
    fees_cache_ttl_seconds: 86400
    """
).strip()

_VALID_MARKET_DATA = dedent(
    """
    base_url: https://data-api.binance.vision
    pairs: [BTC/USDT, ETH/USDT, SOL/USDT]
    intervals: [4h, 1d]
    warmup_bars_4h: 120
    """
).strip()


def _write_tree(root: Path) -> Path:
    """Write a fully-valid config tree under ``root`` and return ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "strategies.yaml").write_text(_VALID_STRATEGIES, encoding="utf-8")
    (root / "exchanges").mkdir()
    (root / "exchanges" / "binance.yaml").write_text(_VALID_BINANCE, encoding="utf-8")
    (root / "fees.yaml").write_text(_VALID_FEES, encoding="utf-8")
    (root / "tax_guard.yaml").write_text(_VALID_TAX_GUARD, encoding="utf-8")
    (root / "ml.yaml").write_text(_VALID_ML, encoding="utf-8")
    (root / "klines.yaml").write_text(_VALID_KLINES, encoding="utf-8")
    (root / "frankfurter.yaml").write_text(_VALID_FRANKFURTER, encoding="utf-8")
    (root / "healthchecks.yaml").write_text(_VALID_HEALTHCHECKS, encoding="utf-8")
    (root / "sentry.yaml").write_text(_VALID_SENTRY, encoding="utf-8")
    (root / "kraken_public.yaml").write_text(_VALID_KRAKEN_PUBLIC, encoding="utf-8")
    (root / "market_data.yaml").write_text(_VALID_MARKET_DATA, encoding="utf-8")
    return root


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """Write a fully-valid config tree under ``tmp_path`` and return the root."""
    return _write_tree(tmp_path)


@pytest.fixture()
def clean_process_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove the env vars the dotenv tests touch, restoring them afterwards.

    ``load_dotenv`` writes into ``os.environ`` directly. Setting each name
    before deleting it makes monkeypatch record it, so whatever the dotenv
    file wrote is undone at teardown and cannot leak into the next test.
    """
    for name in ("HERMES_ENV", "HERMES_ALLOW_MAINNET", "HERMES_BINANCE__RECV_WINDOW_MS"):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    return monkeypatch


def _strategy_weights(config_dir: Path, env: dict[str, str]) -> list[tuple[str, Decimal]]:
    """Return ``(name, weight)`` per strategy after loading ``config_dir`` with ``env``."""
    return [(s.name, s.weight) for s in load_config(config_dir, env=env).strategies.strategies]


# ---------------------------------------------------------------------------
# Happy path: a valid tree parses into the composite root
# ---------------------------------------------------------------------------


def test_load_config_reads_every_section_from_its_own_file(config_dir: Path) -> None:
    """Each of the eleven sections takes its values from its own YAML file."""
    cfg = load_config(config_dir, env={})
    assert (
        [s.name for s in cfg.strategies.strategies],
        cfg.binance.base_url,
        cfg.fees.venues["binance"].maker_bps,
        cfg.tax_guard.max_position_pct_portfolio,
        cfg.ml.max_tokens,
        cfg.klines.store_db_path,
        cfg.frankfurter.base_url,
        cfg.healthchecks.timeout_sec,
        cfg.sentry.environment,
        cfg.kraken_public.timeout_sec,
        cfg.market_data.warmup_bars_4h,
    ) == (
        ["rebalancer", "donchian"],
        "https://testnet.binance.vision",
        Decimal("8"),
        Decimal("0.25"),
        4096,
        "var/hermes/klines.db",
        "https://api.frankfurter.app",
        4.0,
        "sandbox",
        7.5,
        120,
    )


def test_load_config_preserves_decimal_precision_from_string_yaml(config_dir: Path) -> None:
    """Weights authored as quoted YAML strings become exact Decimals, in YAML order."""
    assert _strategy_weights(config_dir, env={}) == [
        ("rebalancer", Decimal("0.6")),
        ("donchian", Decimal("0.4")),
    ]


def test_load_config_parses_capital_budget_and_quote_exactly(config_dir: Path) -> None:
    """capital_budget stays an exact Decimal and quote becomes the Currency member."""
    first = load_config(config_dir, env={}).strategies.strategies[0]
    assert (first.capital_budget, first.quote) == (Decimal("3000"), Currency.USDT)


def test_load_config_reads_the_config_directory_in_cwd_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no ``config_dir`` the loader reads ``./config`` relative to the working directory."""
    _write_tree(tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    assert load_config(env={}).klines.store_db_path == "var/hermes/klines.db"


@pytest.mark.parametrize(
    ("model", "section", "filename"),
    [
        (FrankfurterConfig, "FRANKFURTER", "frankfurter.yaml"),
        (KrakenPublicConfig, "KRAKEN_PUBLIC", "kraken_public.yaml"),
        (HealthchecksConfig, "HEALTHCHECKS", "healthchecks.yaml"),
        (SentryConfig, "SENTRY", "sentry.yaml"),
    ],
)
def test_shipped_optional_section_yaml_equals_the_model_defaults(
    model: type[FrankfurterConfig | KrakenPublicConfig | HealthchecksConfig | SentryConfig],
    section: str,
    filename: str,
) -> None:
    """A section left out of the YAML behaves exactly like the shipped file for that section."""
    shipped = load_yaml_model(_REPO_CONFIG / filename, model, env_section=section, env={})
    assert shipped == model()


def test_shipped_strategies_yaml_is_the_swing_only_roster() -> None:
    """The committed strategies.yaml loads as one swing-donchian-4h entry seeded 5000 USDT."""
    roster = _shipped_roster(env={})
    assert [
        (s.name, s.enabled, s.weight, s.capital_budget, s.quote) for s in roster.strategies
    ] == [("swing-donchian-4h", True, Decimal("1.0"), Decimal("5000"), Currency.USDT)]


def _shipped_roster(env: dict[str, str]) -> StrategiesConfig:
    """Load the committed strategies.yaml with ``env`` as the only overrides."""
    return load_yaml_model(
        _REPO_CONFIG / "strategies.yaml", StrategiesConfig, env_section="STRATEGIES", env=env
    )


# SEV-1: no rubric row matches; pre-reg §5.4/§5.5 limits must reach sizing unchanged
def test_shipped_strategies_yaml_carries_the_preregistered_sizing_limits() -> None:
    """At 5,000 USDT the shipped config gives a 50 risk budget, 250 position cap, 750 total cap."""
    policy = _shipped_roster(env={}).strategies[0].sizing_policy()
    assert (policy.risk_budget, policy.position_cap, policy.exposure_cap) == (
        Decimal("50"),
        Decimal("250"),
        Decimal("750"),
    )


def test_changing_the_configured_capital_scales_every_sizing_limit() -> None:
    """capital_budget 10,000 doubles all three limits: no limit is a fixed dollar amount."""
    roster = _shipped_roster(env={"HERMES_STRATEGIES__STRATEGIES__0__CAPITAL_BUDGET": "10000"})
    policy = roster.strategies[0].sizing_policy()
    assert (policy.risk_budget, policy.position_cap, policy.exposure_cap) == (
        Decimal("100"),
        Decimal("500"),
        Decimal("1500"),
    )


# ---------------------------------------------------------------------------
# Env override: HERMES_<section>__<field> lifts values above YAML
# ---------------------------------------------------------------------------


def test_env_override_lifts_capital_budget(config_dir: Path) -> None:
    """HERMES_STRATEGIES__STRATEGIES__0__CAPITAL_BUDGET replaces the seed, as an exact Decimal."""
    cfg = load_config(
        config_dir,
        env={"HERMES_STRATEGIES__STRATEGIES__0__CAPITAL_BUDGET": "1234.5"},
    )
    assert cfg.strategies.strategies[0].capital_budget == Decimal("1234.5")


def test_env_override_flips_scalar_bool(config_dir: Path) -> None:
    """HERMES_BINANCE__TESTNET=false overrides the YAML-provided testnet: true."""
    cfg = load_config(config_dir, env={"HERMES_BINANCE__TESTNET": "false"})
    assert cfg.binance.testnet is False


@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(window=st.integers(min_value=1, max_value=60_000))
def test_env_override_reaches_an_int_field_exactly(config_dir: Path, window: int) -> None:
    """Any in-range integer in HERMES_BINANCE__RECV_WINDOW_MS replaces the YAML value exactly."""
    cfg = load_config(config_dir, env={"HERMES_BINANCE__RECV_WINDOW_MS": str(window)})
    assert cfg.binance.recv_window_ms == window


def test_env_override_lifts_one_list_element_and_leaves_the_others(config_dir: Path) -> None:
    """HERMES_STRATEGIES__STRATEGIES__0__WEIGHT lifts strategies[0].weight only."""
    assert _strategy_weights(
        config_dir, env={"HERMES_STRATEGIES__STRATEGIES__0__WEIGHT": "0.9"}
    ) == [("rebalancer", Decimal("0.9")), ("donchian", Decimal("0.4"))]


def test_env_override_replaces_a_list_item_addressed_by_its_index(config_dir: Path) -> None:
    """HERMES_MARKET_DATA__PAIRS__2 replaces the third pair in place."""
    cfg = load_config(config_dir, env={"HERMES_MARKET_DATA__PAIRS__2": "SOL/USDC"})
    assert cfg.market_data.pairs == ["BTC/USDT", "ETH/USDT", "SOL/USDC"]


def test_env_override_lifts_nested_dict_entry(config_dir: Path) -> None:
    """HERMES_FEES__VENUES__BINANCE__MAKER_BPS lifts the per-venue override."""
    cfg = load_config(config_dir, env={"HERMES_FEES__VENUES__BINANCE__MAKER_BPS": "5"})
    assert cfg.fees.venues["binance"].maker_bps == Decimal("5")


@pytest.mark.parametrize("index", ["2", "99"])
def test_env_override_past_the_end_of_a_list_is_ignored(config_dir: Path, index: str) -> None:
    """An override at or past the list length is dropped; the list is not extended."""
    assert _strategy_weights(
        config_dir, env={f"HERMES_STRATEGIES__STRATEGIES__{index}__WEIGHT": "0.1"}
    ) == [("rebalancer", Decimal("0.6")), ("donchian", Decimal("0.4"))]


@pytest.mark.parametrize(
    "key", ["HERMES_BINANCE__", "HERMES_BINANCE__TESTNET__", "HERMES_BINANCE____X"]
)
def test_env_override_with_an_empty_path_segment_is_ignored(config_dir: Path, key: str) -> None:
    """A key with an empty segment (trailing or doubled ``__``) is skipped, not applied."""
    assert load_config(config_dir, env={key: "x"}).binance == BinanceExchangeConfig(
        base_url="https://testnet.binance.vision",
        ws_url="wss://testnet.binance.vision/ws",
        recv_window_ms=5000,
        testnet=True,
    )


def test_env_override_after_a_malformed_key_still_applies(config_dir: Path) -> None:
    """Skipping a malformed key does not stop the overrides listed after it."""
    cfg = load_config(
        config_dir, env={"HERMES_BINANCE__": "x", "HERMES_BINANCE__RECV_WINDOW_MS": "7000"}
    )
    assert cfg.binance.recv_window_ms == 7000


def test_env_override_below_a_scalar_list_item_is_ignored(config_dir: Path) -> None:
    """HERMES_MARKET_DATA__PAIRS__0__X cannot drill into the string ``BTC/USDT``; it is dropped."""
    cfg = load_config(config_dir, env={"HERMES_MARKET_DATA__PAIRS__0__X": "y"})
    assert cfg.market_data.pairs == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def test_env_override_without_the_hermes_prefix_is_ignored(config_dir: Path) -> None:
    """A section-shaped name without the HERMES_ prefix never reaches config."""
    cfg = load_config(config_dir, env={"BINANCE__RECV_WINDOW_MS": "7"})
    assert cfg.binance.recv_window_ms == 5000


def test_env_override_for_one_section_does_not_reach_another(config_dir: Path) -> None:
    """A HERMES_KLINES__ override leaves the same-named market-data knob alone."""
    cfg = load_config(config_dir, env={"HERMES_KLINES__RATE_LIMIT_MAX_WEIGHT": "1200"})
    assert (cfg.klines.rate_limit_max_weight, cfg.market_data.rate_limit_max_weight) == (1200, 600)


# ---------------------------------------------------------------------------
# Scalar coercion: the 12-factor bool / int / float pre-pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("Yes", True),
        (" on ", True),
        ("FALSE", False),
        ("no", False),
        ("off", False),
    ],
)
def test_env_override_reads_12_factor_booleans(config_dir: Path, raw: str, expected: bool) -> None:
    """true/yes/on and false/no/off, any case and padding, set a bool field."""
    assert load_config(config_dir, env={"HERMES_ML__ENABLED": raw}).ml.enabled is expected


def test_env_override_float_string_reaches_decimal_field(config_dir: Path) -> None:
    """A non-int numeric string goes through the float path and still lands as Decimal 0.15."""
    cfg = load_config(config_dir, env={"HERMES_TAX_GUARD__MAX_POSITION_PCT_PORTFOLIO": "0.15"})
    assert cfg.tax_guard.max_position_pct_portfolio == Decimal("0.15")


def test_env_override_float_string_reaches_float_field(config_dir: Path) -> None:
    """HERMES_KRAKEN_PUBLIC__TIMEOUT_SEC=2.5 sets the float timeout to 2.5."""
    cfg = load_config(config_dir, env={"HERMES_KRAKEN_PUBLIC__TIMEOUT_SEC": "2.5"})
    assert cfg.kraken_public.timeout_sec == 2.5


def test_env_override_non_numeric_string_stays_string(config_dir: Path) -> None:
    """A non-numeric, non-boolean env value reaches a str field unchanged."""
    cfg = load_config(config_dir, env={"HERMES_ML__SUPERVISOR_MODEL": "gpt-4o-mini"})
    assert cfg.ml.supervisor_model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Nested traversal: missing intermediate dicts, list elements, scalars
# ---------------------------------------------------------------------------


def test_env_override_creates_missing_intermediate_dict(config_dir: Path) -> None:
    """A deeply-nested override path materialises a venue the YAML does not list."""
    cfg = load_config(
        config_dir,
        env={
            "HERMES_FEES__VENUES__NEWVENUE__MAKER_BPS": "12",
            "HERMES_FEES__VENUES__NEWVENUE__TAKER_BPS": "15",
        },
    )
    venue = cfg.fees.venues["newvenue"]
    assert (venue.maker_bps, venue.taker_bps) == (Decimal("12"), Decimal("15"))


def test_env_override_numeric_segment_under_a_mapping_is_a_key(config_dir: Path) -> None:
    """A digit segment under a mapping (venues) names a key, not a list index."""
    cfg = load_config(
        config_dir,
        env={
            "HERMES_FEES__VENUES__2024__MAKER_BPS": "3",
            "HERMES_FEES__VENUES__2024__TAKER_BPS": "4",
        },
    )
    venue = cfg.fees.venues["2024"]
    assert (venue.maker_bps, venue.taker_bps) == (Decimal("3"), Decimal("4"))


def test_env_override_traversing_list_element_reaches_nested_field(config_dir: Path) -> None:
    """A non-terminal list index resolves onto the dict element for further traversal."""
    cfg = load_config(config_dir, env={"HERMES_STRATEGIES__STRATEGIES__1__NAME": "alt_strategy"})
    assert [s.name for s in cfg.strategies.strategies] == ["rebalancer", "alt_strategy"]


def test_env_override_that_would_shadow_a_scalar_fails_loudly(config_dir: Path) -> None:
    """Drilling past a scalar (SUPERVISOR_MODEL__NESTED) replaces it with a dict and fails load."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(config_dir, env={"HERMES_ML__SUPERVISOR_MODEL__NESTED": "ignored"})
    assert "supervisor_model\n  Input should be a valid string" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Failure paths: each mode raises ConfigError with a specific message
# ---------------------------------------------------------------------------


def test_missing_file_raises_config_error_with_path(tmp_path: Path) -> None:
    """A missing YAML file raises ConfigError naming the missing path."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path, env={})
    assert (
        str(excinfo.value) == f"Config error: config file not found: {tmp_path / 'strategies.yaml'}"
    )


def test_config_failure_is_caught_by_an_infra_error_handler(tmp_path: Path) -> None:
    """An adapter's ``except InfraError`` catch-all also catches a config failure."""
    with pytest.raises(InfraError, match=r"^Config error: config file not found: "):
        load_config(tmp_path, env={})


def test_unreadable_file_raises_config_error_with_path_and_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OS error while reading the file becomes ConfigError naming the path and the cause."""
    path = tmp_path / "ml.yaml"
    path.write_text(_VALID_ML, encoding="utf-8")

    def _deny(self: Path, encoding: str | None = None) -> str:
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "read_text", _deny)
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(path, MLConfig, env_section="ML", env={})
    assert str(excinfo.value) == f"Config error: cannot read {path}: access denied"


def test_non_utf8_file_raises_config_error_not_a_decode_traceback(tmp_path: Path) -> None:
    """Issue #807: bytes that are not UTF-8 become a ConfigError naming the path and the byte."""
    path = tmp_path / "ml.yaml"
    path.write_bytes(b"model: caf\xe9\n")
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(path, MLConfig, env_section="ML", env={})
    assert str(excinfo.value) == (
        f"Config error: cannot read {path}: not valid UTF-8 (byte 10: invalid continuation byte)"
    )


def test_invalid_yaml_raises_config_error_with_path(tmp_path: Path) -> None:
    """A YAML syntax error raises ConfigError naming the offending file."""
    path = tmp_path / "strategies.yaml"
    path.write_text(": : : not yaml", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(path, StrategiesConfig, env_section="STRATEGIES", env={})
    assert str(excinfo.value).startswith(f"Config error: invalid YAML in {path}: ")


def test_non_mapping_root_raises_config_error(tmp_path: Path) -> None:
    """A YAML file whose root is a list (not a mapping) fails naming the file and the type."""
    path = tmp_path / "strategies.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(path, StrategiesConfig, env_section="STRATEGIES", env={})
    assert str(excinfo.value) == f"Config error: top-level of {path} must be a mapping, got list"


def test_schema_validation_failure_names_the_file_and_model(tmp_path: Path) -> None:
    """A wrong-typed field raises ConfigError naming the file, the model and the field."""
    path = tmp_path / "binance.yaml"
    path.write_text(
        "base_url: not-a-url\nws_url: wss://x\nrecv_window_ms: 5000\ntestnet: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(path, BinanceExchangeConfig, env_section="BINANCE", env={})
    assert str(excinfo.value).startswith(
        f"Config error: validation failed for {path} against BinanceExchangeConfig: "
        "1 validation error for BinanceExchangeConfig\nbase_url\n"
    )


def test_unknown_field_is_forbidden(tmp_path: Path) -> None:
    """extra=forbid catches typos: an unknown key aborts load and is named."""
    path = tmp_path / "ml.yaml"
    path.write_text(_VALID_ML + "\ntypoed_field: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(path, MLConfig, env_section="ML", env={})
    assert "typoed_field\n  Extra inputs are not permitted" in str(excinfo.value)


def test_empty_yaml_file_fails_every_required_field(tmp_path: Path) -> None:
    """An empty YAML file is an empty mapping, so all four required MLConfig fields fail."""
    path = tmp_path / "ml.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(path, MLConfig, env_section="ML", env={})
    assert str(excinfo.value).startswith(
        f"Config error: validation failed for {path} against MLConfig: "
        "4 validation errors for MLConfig\n"
    )


def test_config_error_message_carries_the_prefix() -> None:
    """__str__ prefixes the message with 'Config error:' so log grep is deterministic."""
    err = ConfigError(msg="binance.yaml: base_url required")
    assert str(err) == "Config error: binance.yaml: base_url required"


# ---------------------------------------------------------------------------
# Secrets never surface in error text
# ---------------------------------------------------------------------------


def test_section_validation_error_does_not_echo_a_secret_from_another_field(
    config_dir: Path,
) -> None:
    """A bad Sentry environment fails load without printing the configured DSN."""
    secret = "PLANTED-SENTRY-KEY"  # noqa: S105  # gitleaks:allow
    with pytest.raises(ConfigError) as excinfo:
        load_config(
            config_dir,
            env={
                "HERMES_SENTRY__DSN": f"https://{secret}@o1.ingest.example/1",
                "HERMES_SENTRY__ENVIRONMENT": "",
            },
        )
    message = str(excinfo.value)
    assert (message.startswith("Config error: validation failed for "), secret in message) == (
        True,
        False,
    )


def test_tier_gate_error_does_not_echo_configured_secrets(config_dir: Path) -> None:
    """The mainnet-outside-prod rejection names the tier but neither the DSN nor the ping URL."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(
            config_dir,
            env={
                "HERMES_ENV": "dev",
                "HERMES_ALLOW_MAINNET": "yes",
                "HERMES_SENTRY__DSN": "https://PLANTED-SENTRY-KEY@o1.ingest.example/1",
                "HERMES_HEALTHCHECKS__PING_URL": "https://hc-ping.com/PLANTED-PING-UUID",
            },
        )
    message = str(excinfo.value)
    assert (
        "current env='dev'" in message,
        "PLANTED-SENTRY-KEY" in message,
        "PLANTED-PING-UUID" in message,
    ) == (True, False, False)


# ---------------------------------------------------------------------------
# Root-level env vars: HERMES_ENV + HERMES_ALLOW_MAINNET
# ---------------------------------------------------------------------------


def test_load_config_defaults_to_dev_without_mainnet_when_unset(config_dir: Path) -> None:
    """With neither HERMES_ENV nor HERMES_ALLOW_MAINNET set, the tier is dev and mainnet is off."""
    cfg = load_config(config_dir, env={})
    assert (cfg.env, cfg.allow_mainnet) == ("dev", False)


@pytest.mark.parametrize("tier", ["dev", "sandbox", "staging", "prod"])
def test_load_config_reads_the_tier_from_hermes_env(config_dir: Path, tier: str) -> None:
    """Each of the four tiers in HERMES_ENV becomes HermesConfig.env, mainnet still off."""
    cfg = load_config(config_dir, env={"HERMES_ENV": tier, "HERMES_ALLOW_MAINNET": "no"})
    assert (cfg.env, cfg.allow_mainnet) == (tier, False)


def test_load_config_rejects_unknown_hermes_env_value(config_dir: Path) -> None:
    """HERMES_ENV=qa is not one of the four tiers, so the root fails validation on ``env``."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(config_dir, env={"HERMES_ENV": "qa"})
    assert str(excinfo.value).startswith(
        "Config error: HermesConfig validation failed: 1 validation error for HermesConfig\nenv\n"
    )


# SEV-1: no rubric row matches; real-capital gate (ADR-003 §00 I-08)
def test_load_config_allows_mainnet_in_prod(config_dir: Path) -> None:
    """HERMES_ALLOW_MAINNET=yes with HERMES_ENV=prod is the one tier that may touch mainnet."""
    cfg = load_config(config_dir, env={"HERMES_ENV": "prod", "HERMES_ALLOW_MAINNET": "yes"})
    assert (cfg.env, cfg.allow_mainnet) == ("prod", True)


# SEV-1: no rubric row matches; real-capital gate (ADR-003 §00 I-08)
@pytest.mark.parametrize("tier", ["dev", "sandbox", "staging"])
def test_load_config_rejects_mainnet_outside_prod(config_dir: Path, tier: str) -> None:
    """HERMES_ALLOW_MAINNET=yes in any non-prod tier fails load, naming the tier."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(config_dir, env={"HERMES_ENV": tier, "HERMES_ALLOW_MAINNET": "yes"})
    assert f"HERMES_ALLOW_MAINNET may only be set in prod env; current env={tier!r}" in str(
        excinfo.value
    )


# ---------------------------------------------------------------------------
# Dotenv: shell env over .env.local over the YAML file
# ---------------------------------------------------------------------------


def test_dotenv_value_overrides_the_yaml_file(
    config_dir: Path, tmp_path: Path, clean_process_env: pytest.MonkeyPatch
) -> None:
    """A section override in .env.local beats the YAML value when no shell var is set."""
    dotenv = tmp_path / ".env.local"
    dotenv.write_text("HERMES_BINANCE__RECV_WINDOW_MS=7000\n", encoding="utf-8")
    assert load_config(config_dir, dotenv_path=dotenv).binance.recv_window_ms == 7000


def test_shell_env_overrides_dotenv(
    config_dir: Path, tmp_path: Path, clean_process_env: pytest.MonkeyPatch
) -> None:
    """A real shell env var wins over a conflicting .env.local value."""
    clean_process_env.setenv("HERMES_ENV", "prod")
    dotenv = tmp_path / ".env.local"
    dotenv.write_text("HERMES_ENV=sandbox\n", encoding="utf-8")
    assert load_config(config_dir, dotenv_path=dotenv).env == "prod"


def test_load_config_reads_dotenv_file_when_env_not_injected(
    config_dir: Path, tmp_path: Path, clean_process_env: pytest.MonkeyPatch
) -> None:
    """With ``env`` unset, HERMES_ENV from the dotenv file sets the tier."""
    dotenv = tmp_path / ".env.local"
    dotenv.write_text("HERMES_ENV=sandbox\n", encoding="utf-8")
    assert load_config(config_dir, dotenv_path=dotenv).env == "sandbox"


def test_load_config_reads_env_local_in_cwd_by_default(
    config_dir: Path, tmp_path: Path, clean_process_env: pytest.MonkeyPatch
) -> None:
    """With no ``dotenv_path`` the loader reads ``.env.local`` in the working directory."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / ".env.local").write_text("HERMES_ENV=sandbox\n", encoding="utf-8")
    clean_process_env.chdir(workdir)
    assert load_config(config_dir).env == "sandbox"


def test_load_config_dotenv_missing_is_silent_noop(
    config_dir: Path, tmp_path: Path, clean_process_env: pytest.MonkeyPatch
) -> None:
    """A missing dotenv file does not raise and the default tier applies: prod has no .env.local."""
    cfg = load_config(config_dir, dotenv_path=tmp_path / "does-not-exist.env")
    assert cfg.env == "dev"


def test_load_config_dotenv_ignored_when_env_snapshot_supplied(
    config_dir: Path, tmp_path: Path, clean_process_env: pytest.MonkeyPatch
) -> None:
    """When ``env`` is passed explicitly, the dotenv file is never read: tests own their env."""
    dotenv = tmp_path / ".env.local"
    dotenv.write_text("HERMES_ENV=sandbox\n", encoding="utf-8")
    assert load_config(config_dir, env={}, dotenv_path=dotenv).env == "dev"
