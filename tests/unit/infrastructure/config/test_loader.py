"""Config loader — YAML + env override + failure paths.

Every test protects one invariant of the config-loader contract established
in ``hermes.infrastructure.config``. Failures here mean the process could
start with wrong or corrupted config — a real-capital risk (per ADR-003 §00
invariant I-05).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from textwrap import dedent

import pytest

from hermes.domain.errors import HermesError, InfraError
from hermes.infrastructure.config import (
    BinanceExchangeConfig,
    ConfigError,
    HermesConfig,
    MLConfig,
    StrategiesConfig,
    load_config,
)
from hermes.infrastructure.config.loader import load_yaml_model

# ---------------------------------------------------------------------------
# Fixtures — full valid config tree written to a tmp dir per test
# ---------------------------------------------------------------------------

_VALID_STRATEGIES = dedent(
    """
    strategies:
      - name: rebalancer
        enabled: true
        weight: "0.6"
      - name: donchian
        enabled: false
        weight: "0.4"
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


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """Write a fully-valid config tree under ``tmp_path`` and return the root."""
    (tmp_path / "strategies.yaml").write_text(_VALID_STRATEGIES, encoding="utf-8")
    (tmp_path / "exchanges").mkdir()
    (tmp_path / "exchanges" / "binance.yaml").write_text(_VALID_BINANCE, encoding="utf-8")
    (tmp_path / "fees.yaml").write_text(_VALID_FEES, encoding="utf-8")
    (tmp_path / "tax_guard.yaml").write_text(_VALID_TAX_GUARD, encoding="utf-8")
    (tmp_path / "ml.yaml").write_text(_VALID_ML, encoding="utf-8")
    (tmp_path / "klines.yaml").write_text(_VALID_KLINES, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Happy path — a valid tree parses into the composite root
# ---------------------------------------------------------------------------


def test_load_config_returns_hermes_config_with_all_five_sections(config_dir: Path) -> None:
    """A valid config tree yields a HermesConfig with every section populated."""
    cfg = load_config(config_dir, env={})
    assert isinstance(cfg, HermesConfig)
    assert len(cfg.strategies.strategies) == 2
    assert cfg.binance.testnet is True
    assert cfg.fees.default.maker_bps == Decimal("10")
    assert cfg.tax_guard.wash_sale_window_days == 30
    assert cfg.ml.max_tokens == 4096


def test_load_config_preserves_decimal_precision_from_string_yaml(config_dir: Path) -> None:
    """Weights authored as quoted YAML strings become Decimal — no float rounding."""
    cfg = load_config(config_dir, env={})
    # Exact Decimal comparison — if pydantic went through float, this would
    # be Decimal("0.6000000000000000888178419700125232338905334472656250").
    assert cfg.strategies.strategies[0].weight == Decimal("0.6")
    assert cfg.strategies.strategies[1].weight == Decimal("0.4")


def test_load_config_preserves_strategy_order(config_dir: Path) -> None:
    """Strategies come out in the same order as the YAML — replay determinism."""
    cfg = load_config(config_dir, env={})
    assert [s.name for s in cfg.strategies.strategies] == ["rebalancer", "donchian"]


# ---------------------------------------------------------------------------
# Env override — HERMES_<section>__<field> lifts values above YAML
# ---------------------------------------------------------------------------


def test_env_override_flips_scalar_bool(config_dir: Path) -> None:
    """HERMES_BINANCE__TESTNET=false overrides the YAML-provided testnet: true."""
    cfg = load_config(config_dir, env={"HERMES_BINANCE__TESTNET": "false"})
    assert cfg.binance.testnet is False


def test_env_override_flips_scalar_int(config_dir: Path) -> None:
    """HERMES_BINANCE__RECV_WINDOW_MS=10000 overrides the YAML integer."""
    cfg = load_config(config_dir, env={"HERMES_BINANCE__RECV_WINDOW_MS": "10000"})
    assert cfg.binance.recv_window_ms == 10000


def test_env_override_lifts_nested_list_element(config_dir: Path) -> None:
    """HERMES_STRATEGIES__STRATEGIES__0__WEIGHT lifts strategies[0].weight."""
    cfg = load_config(
        config_dir,
        env={"HERMES_STRATEGIES__STRATEGIES__0__WEIGHT": "0.9"},
    )
    assert cfg.strategies.strategies[0].weight == Decimal("0.9")
    # Other list elements untouched.
    assert cfg.strategies.strategies[1].weight == Decimal("0.4")


def test_env_override_lifts_nested_dict_entry(config_dir: Path) -> None:
    """HERMES_FEES__VENUES__BINANCE__MAKER_BPS lifts the per-venue override."""
    cfg = load_config(
        config_dir,
        env={"HERMES_FEES__VENUES__BINANCE__MAKER_BPS": "5"},
    )
    assert cfg.fees.venues["binance"].maker_bps == Decimal("5")


def test_env_override_out_of_range_index_is_ignored(config_dir: Path) -> None:
    """An env override targeting a non-existent list index is silently ignored."""
    cfg = load_config(
        config_dir,
        env={"HERMES_STRATEGIES__STRATEGIES__99__WEIGHT": "0.1"},
    )
    assert len(cfg.strategies.strategies) == 2


def test_env_override_malformed_key_is_ignored(config_dir: Path) -> None:
    """An env key with a trailing __ (empty path segment) is skipped, not raised."""
    cfg = load_config(config_dir, env={"HERMES_BINANCE__": "x"})
    # If the malformed key had crashed, load_config would raise; reaching this
    # assertion proves it was skipped.
    assert cfg.binance.recv_window_ms == 5000


def test_env_override_non_matching_prefix_is_ignored(config_dir: Path) -> None:
    """Env vars without the HERMES_ prefix never leak into config."""
    cfg = load_config(
        config_dir,
        env={"PATH": "/opt/nasty", "HERMES_OTHER__STUFF": "x"},
    )
    assert cfg.binance.testnet is True


# ---------------------------------------------------------------------------
# Failure paths — each mode raises ConfigError with a specific message
# ---------------------------------------------------------------------------


def test_missing_file_raises_config_error_with_path(tmp_path: Path) -> None:
    """A missing YAML file raises ConfigError naming the missing path."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path, env={})
    assert "strategies.yaml" in str(excinfo.value)
    assert "not found" in str(excinfo.value)


def test_invalid_yaml_raises_config_error_with_path(tmp_path: Path) -> None:
    """A YAML syntax error raises ConfigError naming the offending file."""
    (tmp_path / "strategies.yaml").write_text(": : : not yaml", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(
            tmp_path / "strategies.yaml",
            StrategiesConfig,
            env_section="STRATEGIES",
            env={},
        )
    assert "strategies.yaml" in str(excinfo.value)
    assert "invalid YAML" in str(excinfo.value)


def test_non_mapping_root_raises_config_error(tmp_path: Path) -> None:
    """A YAML file whose root is a list (not a mapping) fails with a typed error."""
    (tmp_path / "strategies.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(
            tmp_path / "strategies.yaml",
            StrategiesConfig,
            env_section="STRATEGIES",
            env={},
        )
    assert "must be a mapping" in str(excinfo.value)


def test_schema_validation_failure_names_the_model(tmp_path: Path) -> None:
    """A wrong-typed field raises ConfigError naming the model that failed."""
    (tmp_path / "binance.yaml").write_text(
        dedent(
            """
            base_url: not-a-url
            ws_url: wss://x
            recv_window_ms: 5000
            testnet: true
            """
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(
            tmp_path / "binance.yaml",
            BinanceExchangeConfig,
            env_section="BINANCE",
            env={},
        )
    msg = str(excinfo.value)
    assert "BinanceExchangeConfig" in msg
    assert "validation failed" in msg


def test_unknown_field_is_forbidden(tmp_path: Path) -> None:
    """extra=forbid catches typos — an unknown key aborts load."""
    (tmp_path / "ml.yaml").write_text(
        dedent(
            """
            supervisor_model: claude-sonnet-4-7
            temperature: "0.2"
            max_tokens: 4096
            enabled: true
            typoed_field: 1
            """
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(tmp_path / "ml.yaml", MLConfig, env_section="ML", env={})
    assert "typoed_field" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Frozen / immutability — mutating an attribute raises
# ---------------------------------------------------------------------------


def test_loaded_config_is_frozen(config_dir: Path) -> None:
    """HermesConfig instances refuse attribute reassignment."""
    cfg = load_config(config_dir, env={})
    with pytest.raises((TypeError, ValueError)):
        cfg.binance.recv_window_ms = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Empty YAML — pydantic surfaces the missing-field error
# ---------------------------------------------------------------------------


def test_empty_yaml_file_fails_required_field_check(tmp_path: Path) -> None:
    """An empty YAML file yields the required-field validation error, not a crash."""
    (tmp_path / "ml.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_yaml_model(tmp_path / "ml.yaml", MLConfig, env_section="ML", env={})
    assert "supervisor_model" in str(excinfo.value) or "Field required" in str(excinfo.value)


# ---------------------------------------------------------------------------
# ConfigError shape — subclasses InfraError so adapter catch-alls work
# ---------------------------------------------------------------------------


def test_config_error_is_infra_error() -> None:
    """ConfigError sits under InfraError so existing except-InfraError catches it."""
    err = ConfigError(msg="test")
    assert isinstance(err, InfraError)
    assert isinstance(err, HermesError)


def test_config_error_message_includes_context() -> None:
    """__str__ formats the message with the 'Config error:' prefix for logs."""
    err = ConfigError(msg="binance.yaml: base_url required")
    assert str(err) == "Config error: binance.yaml: base_url required"


# ---------------------------------------------------------------------------
# Scalar coercion — the 12-factor bool/int/float pre-pass
# ---------------------------------------------------------------------------


def test_env_override_accepts_yes_as_bool_true(config_dir: Path) -> None:
    """12-factor style: 'yes' becomes True (alongside 'true' / 'on')."""
    cfg = load_config(config_dir, env={"HERMES_BINANCE__TESTNET": "yes"})
    assert cfg.binance.testnet is True


def test_env_override_accepts_off_as_bool_false(config_dir: Path) -> None:
    """12-factor style: 'off' becomes False (alongside 'false' / 'no')."""
    cfg = load_config(config_dir, env={"HERMES_BINANCE__TESTNET": "off"})
    assert cfg.binance.testnet is False


def test_env_override_float_string_reaches_decimal_field(config_dir: Path) -> None:
    """A non-int numeric env string coerces via the float path, then Decimal accepts."""
    # 0.15 is not an int → falls through to float → pydantic accepts as Decimal.
    cfg = load_config(
        config_dir,
        env={"HERMES_TAX_GUARD__MAX_POSITION_PCT_PORTFOLIO": "0.15"},
    )
    # Decimal comparison via str because float→Decimal may add trailing digits.
    assert cfg.tax_guard.max_position_pct_portfolio == Decimal("0.15")


def test_env_override_non_numeric_string_stays_string(config_dir: Path) -> None:
    """A non-numeric, non-boolean env value stays as-is for the field to accept."""
    cfg = load_config(
        config_dir,
        env={"HERMES_ML__SUPERVISOR_MODEL": "gpt-4o-mini"},
    )
    assert cfg.ml.supervisor_model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Nested traversal — non-terminal list index, missing intermediate dict
# ---------------------------------------------------------------------------


def test_env_override_creates_missing_intermediate_dict(config_dir: Path) -> None:
    """A deeply-nested override path materialises missing intermediate dicts."""
    # venues.newvenue is not in the YAML — the override creates it.
    cfg = load_config(
        config_dir,
        env={
            "HERMES_FEES__VENUES__NEWVENUE__MAKER_BPS": "12",
            "HERMES_FEES__VENUES__NEWVENUE__TAKER_BPS": "15",
        },
    )
    assert cfg.fees.venues["newvenue"].maker_bps == Decimal("12")
    assert cfg.fees.venues["newvenue"].taker_bps == Decimal("15")


def test_env_override_traversing_list_element_reaches_nested_field(
    config_dir: Path,
) -> None:
    """A non-terminal list index resolves onto the dict element for further traversal."""
    cfg = load_config(
        config_dir,
        env={"HERMES_STRATEGIES__STRATEGIES__1__NAME": "alt_strategy"},
    )
    assert cfg.strategies.strategies[1].name == "alt_strategy"


def test_env_override_that_would_shadow_a_scalar_fails_loudly(config_dir: Path) -> None:
    """Drilling past a scalar (SUPERVISOR_MODEL__NESTED) makes the field invalid — fail loud."""
    # ml.supervisor_model is a string; the env path drills PAST it, so the
    # loader replaces the scalar with a dict, which then fails pydantic
    # validation. Loud failure is the desired outcome — the operator sees
    # the typo, not a silently-ignored override.
    with pytest.raises(ConfigError) as excinfo:
        load_config(
            config_dir,
            env={"HERMES_ML__SUPERVISOR_MODEL__NESTED": "ignored"},
        )
    assert "supervisor_model" in str(excinfo.value)
