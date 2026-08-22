"""Pydantic-settings schema for hermes YAML configs.

One class per YAML file — matches the layout under ``config/``:

- ``config/strategies.yaml``     → :class:`StrategiesConfig`
- ``config/exchanges/binance.yaml`` → :class:`BinanceExchangeConfig`
- ``config/fees.yaml``            → :class:`FeesConfig`
- ``config/tax_guard.yaml``       → :class:`TaxGuardConfig`
- ``config/ml.yaml`               → :class:`MLConfig`

Every class:

- Inherits ``BaseModel`` (not ``BaseSettings``) — env-var override is applied
  at the composite root (:class:`hermes.infrastructure.config.root.HermesConfig`)
  so we get one consistent ``HERMES_`` prefix across ALL knobs, not five
  competing prefixes.
- Uses ``model_config = ConfigDict(frozen=True, extra="forbid")`` — matches
  code-conventions §3 (immutability by default) and §7 (fail-fast on typos
  in config, per ADR-003 §00 invariant I-05).
- Types every field explicitly — no ``Any``, no bare ``dict``.

Values are placeholders until the feature that consumes them lands
(strategies wiring in #27, exchange adapter in #25, etc.). This issue
(#103) delivers the LOADER + SCHEMA; realistic values ship with the
feature that reads them.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BinanceExchangeConfig",
    "FeesConfig",
    "MLConfig",
    "StrategiesConfig",
    "TaxGuardConfig",
]


_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class StrategyConfig(BaseModel):
    """Parameters for a single trading strategy.

    Attributes:
        name: Stable identifier used in event payloads and log lines.
        enabled: If ``False``, the strategy is skipped at signal-engine startup.
        weight: Fraction of the portfolio budget allocated to this strategy;
            all enabled strategies must sum to ``1.0`` (enforced downstream).
    """

    model_config = _MODEL_CONFIG

    name: str = Field(min_length=1)
    enabled: bool
    weight: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))


class StrategiesConfig(BaseModel):
    """Root of ``config/strategies.yaml`` — the enabled strategy roster.

    Attributes:
        strategies: Ordered list of strategy configs. Order matters for
            deterministic event replay (strategies fire in list order).
            The list is not deeply frozen — pydantic 2 does not coerce
            list → tuple under ``strict=True`` — but the enclosing model
            IS frozen, so attribute reassignment raises.
    """

    model_config = _MODEL_CONFIG

    strategies: list[StrategyConfig] = Field(min_length=1)


class BinanceExchangeConfig(BaseModel):
    """Root of ``config/exchanges/binance.yaml``.

    Attributes:
        base_url: HTTPS endpoint for REST calls.
        ws_url: WSS endpoint for user-data + market streams.
        recv_window_ms: Server-side clock-skew tolerance passed on every
            signed request.
        testnet: ``True`` routes to the testnet cluster; ``False`` = mainnet.
            Real-capital paths require ``testnet=False`` AND explicit typed
            confirmation (invariant I-08).
    """

    model_config = _MODEL_CONFIG

    base_url: str = Field(min_length=1, pattern=r"^https://")
    ws_url: str = Field(min_length=1, pattern=r"^wss://")
    recv_window_ms: int = Field(ge=1, le=60_000)
    testnet: bool


class FeeSchedule(BaseModel):
    """Maker / taker fee pair for a single fee tier.

    Attributes:
        maker_bps: Basis-points fee applied to resting orders that provide
            liquidity.
        taker_bps: Basis-points fee applied to marketable orders that take
            liquidity.
    """

    model_config = _MODEL_CONFIG

    maker_bps: Decimal = Field(ge=Decimal("0"))
    taker_bps: Decimal = Field(ge=Decimal("0"))


class FeesConfig(BaseModel):
    """Root of ``config/fees.yaml`` — per-venue fee schedules.

    Attributes:
        default: Fee schedule applied when a specific venue is not listed
            under ``venues``.
        venues: Optional per-venue overrides keyed by lowercase venue name.
    """

    model_config = _MODEL_CONFIG

    default: FeeSchedule
    venues: dict[str, FeeSchedule] = Field(default_factory=dict)


class TaxGuardConfig(BaseModel):
    """Root of ``config/tax_guard.yaml`` — LOPDGDD + tax thresholds.

    Attributes:
        annual_realised_pnl_alert_eur: Absolute EUR gain in a calendar year
            above which the operator receives a Modelo-100 reminder.
        wash_sale_window_days: Rolling window used to flag potential
            wash-sale patterns (informational — Spain has no wash-sale rule
            per se, but the flag helps the operator explain positions to
            the gestor).
        max_position_pct_portfolio: Concentration cap; single-instrument
            exposure above this ratio blocks new entries.
    """

    model_config = _MODEL_CONFIG

    annual_realised_pnl_alert_eur: Decimal = Field(gt=Decimal("0"))
    wash_sale_window_days: int = Field(ge=1, le=365)
    max_position_pct_portfolio: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))


class MLConfig(BaseModel):
    """Root of ``config/ml.yaml`` — ML/LLM subsystem configuration.

    LLMs supervise and monitor per ADR-001 + ADR-005; they never emit trade
    signals. This config carries knobs for the supervisor role only.

    Attributes:
        supervisor_model: Model identifier (e.g. ``"claude-sonnet-4-7"``)
            used for the run-time supervisor summariser.
        temperature: Sampling temperature for the supervisor. Low by default
            because supervision favours consistency over creativity.
        max_tokens: Hard cap on supervisor response length.
        enabled: Kill-switch — if ``False`` the supervisor is bypassed and
            hermes runs unsupervised (dev / offline replay only).
    """

    model_config = _MODEL_CONFIG

    supervisor_model: str = Field(min_length=1)
    temperature: Decimal = Field(ge=Decimal("0"), le=Decimal("2"))
    max_tokens: int = Field(ge=1, le=200_000)
    enabled: bool
