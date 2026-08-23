"""Composite ``HermesConfig`` — bundles the five sub-configs into one root.

Consumers depend on a single root object rather than five separate loaders.
Wiring code in ``entrypoints/`` reads ``load_config()`` once at startup and
passes slices of the result down through constructor injection.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from hermes.infrastructure.config.models import (
    BinanceExchangeConfig,
    FeesConfig,
    KlinesConfig,
    MLConfig,
    StrategiesConfig,
    TaxGuardConfig,
)

__all__ = ["HermesConfig"]


class HermesConfig(BaseModel):
    """Root of the hermes runtime configuration graph.

    Attributes:
        strategies: Strategy roster with weights.
        binance: Binance-exchange endpoints and testnet flag.
        fees: Per-venue fee schedules.
        tax_guard: LOPDGDD + tax thresholds.
        ml: LLM supervisor configuration (never trade-signal source per
            ADR-001 + ADR-005).
        klines: Historical-klines fetcher + storage configuration
            (issue #28 foundation).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    strategies: StrategiesConfig
    binance: BinanceExchangeConfig
    fees: FeesConfig
    tax_guard: TaxGuardConfig
    ml: MLConfig
    klines: KlinesConfig
