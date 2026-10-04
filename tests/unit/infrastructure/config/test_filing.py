"""Tests for the tax-filing config and the local Modelo 721 history (change 36 D-7, D-9)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from hermes.domain.filing.modelo721 import Modelo721Rules, PreviousDeclaration
from hermes.domain.value_objects.currency import Currency
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.filing import (
    MODELO721_HISTORY_ENV,
    load_filing_config,
    load_modelo721_history,
    resolve_modelo721_history_path,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_CONFIG_DIR = _PROJECT_ROOT / "config"


def test_repo_config_holds_the_721_thresholds() -> None:
    """Invariant: the shipped config gives the 50,000 / 20,000 EUR rules."""
    config = load_filing_config(_CONFIG_DIR, env={})
    assert config.modelo721.to_rules() == Modelo721Rules(
        threshold_eur=Decimal("50000"), increase_eur=Decimal("20000")
    )


def test_repo_config_leaves_the_custodian_country_unset() -> None:
    """Invariant: the country waits for the gestor (ADR-002 Pending)."""
    custodian = load_filing_config(_CONFIG_DIR, env={}).custodian
    assert (custodian.name, custodian.country) == ("Binance", None)


def test_example_history_parses() -> None:
    """Invariant: the committed example is a valid history file."""
    history = load_modelo721_history(_CONFIG_DIR / "modelo721.example.yaml", env={})
    assert sorted(history.declarations) == [2026]


def test_missing_history_means_none_filed(tmp_path: Path) -> None:
    """Invariant: no file means no earlier 721."""
    history = load_modelo721_history(tmp_path / "absent.yaml", env={})
    assert (history.declarations, history.latest_before(2027)) == ({}, None)


def test_latest_before_picks_the_most_recent_earlier_year(tmp_path: Path) -> None:
    """Invariant: the report year and later are skipped; the latest earlier year is used."""
    path = tmp_path / "h.yaml"
    path.write_text(
        'declarations:\n  2024: {BTC: "1"}\n  2025: {BTC: "2"}\n  2026: {BTC: "3"}\n',
        encoding="utf-8",
    )
    assert load_modelo721_history(path, env={}).latest_before(2026) == PreviousDeclaration(
        year=2025, values_eur={Currency.BTC: Decimal("2")}
    )


def test_env_var_overrides_the_history_path(tmp_path: Path) -> None:
    """Invariant: HERMES_MODELO721_HISTORY points at another file."""
    target = tmp_path / "custom.yaml"
    assert resolve_modelo721_history_path({MODELO721_HISTORY_ENV: str(target)}) == target


def test_bad_history_fails_closed_without_echoing_values(tmp_path: Path) -> None:
    """Invariant (ADR-016 item 12): a negative value is rejected and not printed back."""
    path = tmp_path / "h.yaml"
    path.write_text('declarations:\n  2025: {BTC: "-98765.43"}\n', encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_modelo721_history(path, env={})
    assert "98765" not in str(excinfo.value)


def test_unknown_coin_in_history_is_rejected(tmp_path: Path) -> None:
    """Invariant: a coin hermes does not know cannot be silently dropped."""
    path = tmp_path / "h.yaml"
    path.write_text('declarations:\n  2025: {DOGE: "1"}\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid 721 history"):
        load_modelo721_history(path, env={})


def test_history_repr_hides_values(tmp_path: Path) -> None:
    """Invariant: values never reach logs through repr."""
    path = tmp_path / "h.yaml"
    path.write_text('declarations:\n  2025: {BTC: "12345.67"}\n', encoding="utf-8")
    assert "12345" not in repr(load_modelo721_history(path, env={}))
