"""Composite ``HermesConfig`` — bundles the sub-configs into one root.

Consumers depend on a single root object rather than five separate loaders.
Wiring code in ``entrypoints/`` reads ``load_config()`` once at startup and
passes slices of the result down through constructor injection.

Runtime-shape knobs (``env`` + ``allow_mainnet``) live at the root because
they gate ADAPTER wiring, not domain logic. See
``docs/decisions/2026-08-23-credential-storage-layered-by-env.md`` for the
three-tier credential story (dotenv → GH Secrets → systemd EnvironmentFile).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from hermes.infrastructure.config.models import (
    BinanceExchangeConfig,
    FeesConfig,
    KlinesConfig,
    MLConfig,
    StrategiesConfig,
    TaxGuardConfig,
)

__all__ = ["HermesConfig"]


EnvName = Literal["dev", "sandbox", "prod"]


class HermesConfig(BaseModel):
    """Root of the hermes runtime configuration graph.

    Attributes:
        env: Runtime environment tier. Controls which adapters may be wired
            (dev + sandbox reject anything that touches mainnet money;
            ``prod`` is the only tier where ``allow_mainnet=True`` is
            permitted). Sourced from ``HERMES_ENV``.
        allow_mainnet: Belt-and-suspenders kill switch for real-capital
            paths. Even in ``prod`` this must be flipped explicitly before
            any adapter that touches mainnet exchange endpoints is allowed
            to construct. Sourced from ``HERMES_ALLOW_MAINNET``. Setting
            this outside ``prod`` is a validation error — fail-fast at
            process start rather than at first-trade time.
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

    env: EnvName = "dev"
    allow_mainnet: bool = False
    strategies: StrategiesConfig
    binance: BinanceExchangeConfig
    fees: FeesConfig
    tax_guard: TaxGuardConfig
    ml: MLConfig
    klines: KlinesConfig

    @model_validator(mode="after")
    def _mainnet_only_in_prod(self) -> HermesConfig:
        """Reject ``allow_mainnet=True`` outside the ``prod`` env tier.

        The runtime-tier gate is the invariant: dev / sandbox MUST NOT
        touch mainnet money regardless of any per-adapter flag flip.
        Failing at config-load time surfaces the misconfig before any
        exchange adapter is constructed.
        """
        if self.allow_mainnet and self.env != "prod":
            raise ValueError(
                f"HERMES_ALLOW_MAINNET may only be set in prod env; current env={self.env!r}"
            )
        return self
