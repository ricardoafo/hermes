"""Fuzz properties for the config loaders (issue #842).

Every YAML file Hermes reads at start-up, and the two operator files outside
the repo (721 history, taxpayer profile), load into their model or raise
``ConfigError``, nothing else: start-up reports a ``ConfigError`` as a named
bad file, while a raw ``RecursionError`` or ``UnicodeDecodeError`` is a crash
with a traceback. Seeds are the repo's own ``config/*.yaml`` files, mutated
in structure (wrong types, missing and extra keys, empty lists, unicode) or
replaced by bytes that are not YAML or not UTF-8. Env overrides are fuzzed
the same way. An accepted config holds only finite numbers.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest
import yaml
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import BaseModel

from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.filing import (
    FilingConfig,
    load_modelo721_history,
)
from hermes.infrastructure.config.loader import load_yaml_model
from hermes.infrastructure.config.models import (
    BinanceExchangeConfig,
    FeesConfig,
    FrankfurterConfig,
    HealthchecksConfig,
    KlinesConfig,
    KrakenPublicConfig,
    MarketDataConfig,
    MLConfig,
    SentryConfig,
    StrategiesConfig,
    TaxGuardConfig,
)
from hermes.infrastructure.config.renta import RentaCampaignsConfig
from hermes.infrastructure.config.tax import TaxScalesConfig, load_taxpayer_profile
from tests.support.paths import REPO_ROOT
from tests.unit.adapters.wire_fuzz import (
    FUZZ_SLOW,
    accepted,
    clean_violations,
    foreign_error,
    mutated,
)

_CONFIG: Final = REPO_ROOT / "config"

_SECTIONS: Final[dict[str, tuple[type[BaseModel], str]]] = {
    "strategies.yaml": (StrategiesConfig, "STRATEGIES"),
    "exchanges/binance.yaml": (BinanceExchangeConfig, "BINANCE"),
    "fees.yaml": (FeesConfig, "FEES"),
    "tax_guard.yaml": (TaxGuardConfig, "TAX_GUARD"),
    "ml.yaml": (MLConfig, "ML"),
    "klines.yaml": (KlinesConfig, "KLINES"),
    "frankfurter.yaml": (FrankfurterConfig, "FRANKFURTER"),
    "healthchecks.yaml": (HealthchecksConfig, "HEALTHCHECKS"),
    "sentry.yaml": (SentryConfig, "SENTRY"),
    "kraken_public.yaml": (KrakenPublicConfig, "KRAKEN_PUBLIC"),
    "market_data.yaml": (MarketDataConfig, "MARKET_DATA"),
    "filing.yaml": (FilingConfig, "FILING"),
    "renta_campaigns.yaml": (RentaCampaignsConfig, "RENTA"),
    "tax_scales.yaml": (TaxScalesConfig, "TAX_SCALES"),
}

_not_yaml: Final = st.one_of(
    st.binary(max_size=24),
    st.sampled_from(
        [
            b"\xff\xfe not utf-8",
            b"a: &loop {b: *loop}",
            b"a: !!python/object:os.system x",
            b"? [a]\n: 1",
        ]
    ),
)
"""Files that are not UTF-8, not YAML, or refer to themselves (too deep: pinned by @example)."""


def _seed(name: str) -> object:
    return yaml.safe_load((_CONFIG / name).read_text(encoding="utf-8"))


def _load_section(path: Path, name: str, env: dict[str, str] | None = None) -> BaseModel:
    model, section = _SECTIONS[name]
    return load_yaml_model(path, model, env_section=section, env=env or {})


def _write(path: Path, document: object) -> Path:
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("name", sorted(_SECTIONS))
@FUZZ_SLOW
@given(data=st.data())
def test_a_config_file_loads_or_raises_config_error(
    name: str, data: st.DataObject, tmp_path: Path
) -> None:
    """Invariant: any mutation of a repo config file loads or raises ConfigError."""
    path = _write(tmp_path / "section.yaml", data.draw(mutated(_seed(name))))
    assert foreign_error(lambda: _load_section(path, name), ConfigError) == ""


@pytest.mark.parametrize("name", sorted(_SECTIONS))
@FUZZ_SLOW
@given(data=st.data())
def test_an_accepted_config_file_holds_only_finite_numbers(
    name: str, data: st.DataObject, tmp_path: Path
) -> None:
    """Invariant: every number in a config the loader accepts is finite."""
    path = _write(tmp_path / "section.yaml", data.draw(mutated(_seed(name))))
    assert clean_violations(accepted(lambda: _load_section(path, name), ConfigError)) == []


@FUZZ_SLOW
@given(raw=_not_yaml)
@example(raw=b"\xff\xfe not utf-8")
@example(raw=b"[" * 1000)
@example(raw=b"a: &loop {b: *loop}")
def test_a_config_file_that_is_not_yaml_raises_config_error(raw: bytes, tmp_path: Path) -> None:
    """Invariant: bytes that are not UTF-8 YAML make the loader raise ConfigError, not crash."""
    path = tmp_path / "strategies.yaml"
    path.write_bytes(raw)
    assert foreign_error(lambda: _load_section(path, "strategies.yaml"), ConfigError) == ""


@FUZZ_SLOW
@given(
    suffix=st.lists(st.text(min_size=0, max_size=6), min_size=1, max_size=4),
    value=st.text(max_size=12),
)
def test_an_env_override_loads_or_raises_config_error(
    suffix: list[str], value: str, tmp_path: Path
) -> None:
    """Invariant: a strategies env override of any key and value loads or raises ConfigError."""
    path = _write(tmp_path / "strategies.yaml", _seed("strategies.yaml"))
    env = {"HERMES_STRATEGIES__" + "__".join(suffix): value}
    assert foreign_error(lambda: _load_section(path, "strategies.yaml", env), ConfigError) == ""


@FUZZ_SLOW
@given(document=mutated(_seed("modelo721.example.yaml")))
def test_a_721_history_loads_or_raises_config_error(document: object, tmp_path: Path) -> None:
    """Invariant: any mutation of the 721 history file loads or raises ConfigError."""
    path = _write(tmp_path / "modelo721.yaml", document)
    assert foreign_error(lambda: load_modelo721_history(path), ConfigError) == ""


@FUZZ_SLOW
@given(document=mutated(_seed("taxpayer.example.yaml")))
def test_a_taxpayer_profile_loads_or_raises_config_error(document: object, tmp_path: Path) -> None:
    """Invariant: any mutation of the taxpayer profile loads or raises ConfigError."""
    path = _write(tmp_path / "taxpayer.yaml", document)
    assert foreign_error(lambda: load_taxpayer_profile(path), ConfigError) == ""


_OPERATOR_FILES: Final[dict[str, Callable[[Path], object]]] = {
    "721 history": load_modelo721_history,
    "taxpayer profile": load_taxpayer_profile,
}


@pytest.mark.parametrize("kind", sorted(_OPERATOR_FILES))
@FUZZ_SLOW
@given(raw=_not_yaml)
@example(raw=b"\xff\xfe not utf-8")
@example(raw=b"[" * 1000)
def test_an_operator_file_that_is_not_yaml_raises_config_error(
    kind: str, raw: bytes, tmp_path: Path
) -> None:
    """Invariant: a 721 history or taxpayer profile that is not UTF-8 YAML raises ConfigError."""
    path = tmp_path / "operator.yaml"
    path.write_bytes(raw)
    assert foreign_error(lambda: _OPERATOR_FILES[kind](path), ConfigError) == ""
