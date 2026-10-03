"""Typed configuration loader — pydantic-settings + YAML + env overrides.

The config subsystem is INFRASTRUCTURE, not domain. Import-linter contract
``domain-no-pydantic`` forbids pydantic / pydantic_settings under
``hermes.domain``. Every strategy parameter, exchange endpoint, fee schedule,
tax-guard threshold and ML config field is typed and validated at load time,
so downstream code never handles ``dict[str, Any]`` config blobs.

See ADR-003 §00 invariant I-05 (no magic constants at runtime) and §04
(secrets via pydantic-settings env-var loading).

Public surface:

- :class:`HermesConfig` — composite root wiring all five sub-configs.
- :func:`load_config` — reads YAML files, applies env overrides, validates.
- :class:`ConfigError` — raised on malformed YAML / validation failure.
- Individual sub-config classes for consumers that only need one slice.
"""

from __future__ import annotations

from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_config
from hermes.infrastructure.config.models import (
    BinanceExchangeConfig,
    FeesConfig,
    KlinesConfig,
    KrakenPublicConfig,
    MarketDataConfig,
    MLConfig,
    SentryConfig,
    StrategiesConfig,
    TaxGuardConfig,
)
from hermes.infrastructure.config.root import HermesConfig
from hermes.infrastructure.config.tax import (
    NO_PROFILE_NOTICE,
    SavingsScale,
    TaxpayerProfile,
    TaxScalesConfig,
    load_tax_scales,
    load_taxpayer_profile,
)

__all__ = [
    "DEFAULT_CONFIG_DIR",
    "NO_PROFILE_NOTICE",
    "BinanceExchangeConfig",
    "ConfigError",
    "FeesConfig",
    "HermesConfig",
    "KlinesConfig",
    "KrakenPublicConfig",
    "MLConfig",
    "MarketDataConfig",
    "SavingsScale",
    "SentryConfig",
    "StrategiesConfig",
    "TaxGuardConfig",
    "TaxScalesConfig",
    "TaxpayerProfile",
    "load_config",
    "load_tax_scales",
    "load_taxpayer_profile",
]
