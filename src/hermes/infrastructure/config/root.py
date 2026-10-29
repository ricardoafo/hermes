"""Composite ``HermesConfig`` — bundles the sub-configs into one root.

Consumers depend on a single root object rather than five separate loaders.
Wiring code in ``entrypoints/`` reads ``load_config()`` once at startup and
passes slices of the result down through constructor injection.

Runtime-shape knobs (``env`` + ``allow_mainnet``) live at the root because
they gate ADAPTER wiring, not domain logic. See
``docs/decisions/2026-08-23-credential-storage-layered-by-env.md`` for the
three-tier credential story (dotenv → GH Secrets → systemd EnvironmentFile).
"""

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from hermes.adapters.endpoint import is_loopback
from hermes.infrastructure.config.models import (
    DEFAULT_FRANKFURTER_BASE_URL,
    DEFAULT_KRAKEN_PUBLIC_BASE_URL,
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

__all__ = ["EndpointTierError", "HermesConfig"]


EnvName = Literal["dev", "sandbox", "qa", "staging", "prod"]

LOOPBACK_TIERS: Final[frozenset[str]] = frozenset({"dev", "sandbox", "qa"})
"""Tiers that may point an endpoint at a loopback fake (ADR-024 §4).

Staging reads only public market data and the lab's own notifiers, and prod
only its own, so neither may run against a fake by mistake.
"""


class EndpointTierError(ValueError):
    """An endpoint in the config breaks its tier's rules (ADR-024 §4, #970).

    Pydantic keeps the exception in the validation error's context, so the
    loader can report each problem by name.

    Args:
        env: The tier.
        problems: One entry per problem, naming the field and never its value.
    """

    def __init__(self, env: str, problems: list[str]) -> None:
        super().__init__(f"HERMES_ENV={env}: " + "; ".join(problems))
        self.env = env
        self.problems = tuple(problems)


class HermesConfig(BaseModel):
    """Root of the hermes runtime configuration graph.

    Attributes:
        env: Runtime environment tier. Controls which adapters may be wired
            (dev + sandbox reject anything that touches mainnet money;
            ``prod`` is the only tier where ``allow_mainnet=True`` is
            permitted; ``staging`` is the isolated pre-prod copy on the
            prod box and stays off mainnet too). Sourced from ``HERMES_ENV``.
        allow_mainnet: Belt-and-suspenders kill switch for real-capital
            paths. Even in ``prod`` this must be flipped explicitly before
            any adapter that touches mainnet exchange endpoints is allowed
            to construct. Sourced from ``HERMES_ALLOW_MAINNET``. Setting
            this outside ``prod`` is a validation error — fail-fast at
            process start rather than at first-trade time.
        strategies: Strategy roster with weights.
        binance: Binance-exchange endpoints and testnet flag.
        fees: Per-venue fee schedules.
        tax_guard: LOPDGDD + tax thresholds. Serve reads the
            recharacterization guard's window and ceilings; the file is
            optional and the section has defaults.
        ml: LLM supervisor configuration (never trade-signal source per
            ADR-001 + ADR-005). Reserved: no code reads it yet, so the file
            is optional and the section has defaults.
        klines: Historical-klines fetcher + storage configuration
            (issue #28 foundation).
        frankfurter: FX-rate provider configuration — the
            ECB-reference-rate wrapper used for EUR conversion at
            tax-reporting time (see the FX decisions journal entry).
        healthchecks: Dead-man's-switch heartbeat configuration for
            Healthchecks.io. Defaults to an empty ping URL — the
            integration is opt-in; a fresh clone reports NOT_CONFIGURED
            (exit 3) from ``hermes health healthchecks`` until the
            operator populates ``HERMES_HEALTHCHECKS__PING_URL``.
        sentry: Sentry error-reporting SDK configuration. Defaults to
            an empty DSN — the integration is opt-in; a fresh clone
            reports NOT_CONFIGURED (exit 3) from ``hermes health sentry``
            and :func:`hermes.infrastructure.sentry_integration.initialize`
            becomes a no-op until the operator populates
            ``HERMES_SENTRY__DSN``.
        kraken_public: Kraken read-only endpoints configuration
            (fee schedules, klines, orderbook, ticker). Defaults are
            safe — the endpoints require no auth and the rate limiter
            sizes at Kraken's published 1 call/second public-tier cap.
            See docs/decisions/2026-08-24-kraken-public-adapter.md.
        market_data: Live closed-bar source (public Binance klines, REST
            polling): host allow-list, pairs, intervals, warm-up depth and
            poll timing. Needs no credentials and is unrelated to
            ``allow_mainnet``, which gates trading adapters only.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    env: EnvName = "dev"
    allow_mainnet: bool = False
    strategies: StrategiesConfig
    binance: BinanceExchangeConfig
    fees: FeesConfig
    klines: KlinesConfig
    frankfurter: FrankfurterConfig = FrankfurterConfig()
    healthchecks: HealthchecksConfig = HealthchecksConfig()
    sentry: SentryConfig = SentryConfig()
    tax_guard: TaxGuardConfig = TaxGuardConfig()
    ml: MLConfig = MLConfig()
    kraken_public: KrakenPublicConfig = KrakenPublicConfig()
    market_data: MarketDataConfig = MarketDataConfig()

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

    @model_validator(mode="after")
    def _endpoints_fit_the_tier(self) -> HermesConfig:
        """Refuse a loopback endpoint above qa, and any non-default one in prod.

        Errors name the field, never its value: the ping URL and the DSN
        carry credentials (#970).
        """
        endpoints = {
            "market_data.base_url": self.market_data.base_url,
            "frankfurter.base_url": self.frankfurter.base_url,
            "kraken_public.base_url": self.kraken_public.base_url,
            "healthchecks.ping_url": self.healthchecks.ping_url,
            "sentry.dsn": self.sentry.dsn,
        }
        problems = []
        if self.env not in LOOPBACK_TIERS:
            problems += [
                f"{name} points at a loopback host, which only {_tiers()} may use"
                for name, url in endpoints.items()
                if url and is_loopback(url)
            ]
        if self.env == "prod":
            problems += [
                f"{name} is not the default endpoint, which prod requires"
                for name, url, default in (
                    (
                        "frankfurter.base_url",
                        self.frankfurter.base_url,
                        DEFAULT_FRANKFURTER_BASE_URL,
                    ),
                    (
                        "kraken_public.base_url",
                        self.kraken_public.base_url,
                        DEFAULT_KRAKEN_PUBLIC_BASE_URL,
                    ),
                )
                if url.rstrip("/") != default and not is_loopback(url)
            ]
        if problems:
            raise EndpointTierError(self.env, problems)
        return self


def _tiers() -> str:
    return ", ".join(sorted(LOOPBACK_TIERS))
