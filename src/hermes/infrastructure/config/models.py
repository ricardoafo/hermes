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
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from returns.result import Failure

from hermes.domain.market_data.kline import KlineInterval
from hermes.domain.value_objects.currency import Currency
from hermes.domain.value_objects.pair import Pair

__all__ = [
    "BinanceExchangeConfig",
    "FeesConfig",
    "FrankfurterConfig",
    "HealthchecksConfig",
    "KlinesConfig",
    "KrakenPublicConfig",
    "MLConfig",
    "MarketDataConfig",
    "SentryConfig",
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
        capital_budget: Starting capital for this strategy, in ``quote``
            units. Seeds the portfolio tracker and, later, the risk engine's
            per-strategy budget. Required: a missing value fails at load.
        quote: Currency ``capital_budget`` is denominated in. Required.
    """

    model_config = _MODEL_CONFIG

    name: str = Field(min_length=1)
    enabled: bool
    weight: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    capital_budget: Decimal = Field(gt=Decimal("0"))
    quote: Currency


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


class FrankfurterConfig(BaseModel):
    """Root of ``config/frankfurter.yaml`` — official FX rate provider.

    Consumed by :class:`hermes.adapters.fx.frankfurter.FrankfurterAdapter`.
    Frankfurter is the ECB reference-rate wrapper we use for EUR
    conversion at tax-reporting time — see
    ``docs/decisions/2026-08-23-fx-rate-source-frankfurter.md``.

    Attributes:
        base_url: HTTPS root of the Frankfurter API. Defaults to the
            public endpoint (no key required). Override for a self-hosted
            instance (the Frankfurter project publishes a Docker image).
        timeout_sec: Per-request HTTP timeout in seconds.
        requests_per_minute: Polite rate cap. Frankfurter publishes no
            official limit; 60/min is well below anything a small hobbyist
            wrapper could reasonably reject and covers a full tax-report
            batch (dozens of dates) comfortably.
    """

    model_config = _MODEL_CONFIG

    base_url: str = Field(default="https://api.frankfurter.app", pattern=r"^https://")
    timeout_sec: float = Field(default=10.0, gt=0)
    requests_per_minute: int = Field(default=60, gt=0, le=6000)


class KrakenPublicConfig(BaseModel):
    """Root of ``config/kraken_public.yaml`` — Kraken read-only endpoints.

    Consumed by :class:`hermes.adapters.exchanges.kraken_public.KrakenPublicAdapter`.
    Kraken's public endpoints (fees, klines, orderbook, ticker) require
    no API key — they are IP-rate-limited only (1 call / second per IP).

    Attributes:
        base_url: HTTPS root of the Kraken REST API. Defaults to the
            public endpoint. Override for a Kraken sandbox / staging
            mirror.
        timeout_sec: Per-request HTTP timeout in seconds.
        requests_per_second: Sizing for the internal token-bucket
            limiter. Default 1 matches Kraken's published public-tier
            cap; raising it is a good way to earn 429s.
        fees_cache_ttl_seconds: TTL for the in-memory fee-schedule
            cache. Kraken changes fee schedules infrequently
            (weeks-to-months); the default 86_400 seconds (24 h) keeps
            the commission-calculation path well below the 1 req/s cap
            while still catching schedule changes within a business day.
    """

    model_config = _MODEL_CONFIG

    base_url: str = Field(default="https://api.kraken.com", pattern=r"^https://")
    timeout_sec: float = Field(default=10.0, gt=0)
    requests_per_second: int = Field(default=1, gt=0, le=60)
    fees_cache_ttl_seconds: int = Field(default=86_400, ge=0, le=7 * 86_400)


# The only host the live closed-bar source may read. data-api.binance.vision
# serves public market data only (GET, no keys); trading endpoints live on
# api.binance.com and must never be reachable from this config.
_MARKET_DATA_ALLOWED_BASE_URLS: Final[frozenset[str]] = frozenset(
    {"https://data-api.binance.vision"}
)
_MARKET_DATA_INTERVALS: Final[frozenset[KlineInterval]] = frozenset(
    {KlineInterval.H4, KlineInterval.D1}
)


class MarketDataConfig(BaseModel):
    """Root of ``config/market_data.yaml`` — live closed-bar source.

    Consumed by :class:`hermes.adapters.market_data.binance_live_klines.BinanceLiveKlinesAdapter`
    and the ``hermes health market_data`` probe. Env overrides use
    ``HERMES_MARKET_DATA__<FIELD>``.

    Attributes:
        base_url: HTTPS root of the public market-data host. Only
            ``https://data-api.binance.vision`` is accepted.
        pairs: Pairs to follow, as ``BASE/QUOTE`` text.
        intervals: Candle intervals to follow. ``4h`` and ``1d`` only: the
            swing strategy never enters without 1d bars.
        warmup_bars_4h: Closed 4h bars returned by warm-up. The floor is the
            45 bars after which every 4h indicator value stops changing.
        warmup_bars_1d: Closed 1d bars returned by warm-up. The floor is the
            200 bars the 1d EMA needs.
        settle_delay_seconds: Wait after a candle boundary before the bar
            counts as closed.
        retry_interval_seconds: Re-poll period while a bar is overdue.
        slow_retry_interval_seconds: Re-poll period once the source is stale.
        stale_after_seconds: Overdue time after which the source is stale.
        timeout_sec: Per-request HTTP timeout in seconds.
        rate_limit_max_weight: Token-bucket size per window. The default is
            10 percent of Binance's 6000 weight per minute cap.
        rate_limit_window_seconds: Token-bucket refill window in seconds.
    """

    model_config = _MODEL_CONFIG

    base_url: str = Field(default="https://data-api.binance.vision")
    pairs: list[str] = Field(
        default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"], min_length=1
    )
    intervals: list[KlineInterval] = Field(
        default_factory=lambda: [KlineInterval.H4, KlineInterval.D1], min_length=1
    )
    warmup_bars_4h: int = Field(default=300, ge=45, le=999)
    warmup_bars_1d: int = Field(default=300, ge=200, le=999)
    settle_delay_seconds: float = Field(default=5.0, gt=0, le=60)
    retry_interval_seconds: float = Field(default=5.0, gt=0, le=300)
    slow_retry_interval_seconds: float = Field(default=30.0, gt=0, le=3_600)
    stale_after_seconds: float = Field(default=900.0, gt=0, le=86_400)
    timeout_sec: float = Field(default=10.0, gt=0)
    # Floor of 3 covers the heaviest single request (klines, weight 2) plus
    # a time call (weight 1), so the limiter can always grant a request.
    rate_limit_max_weight: int = Field(default=600, ge=3, le=6_000)
    rate_limit_window_seconds: int = Field(default=60, gt=0, le=3_600)

    @field_validator("base_url")
    @classmethod
    def _host_is_allow_listed(cls, value: str) -> str:
        """Accept only the public market-data host, with no trailing slash."""
        normalised = value.rstrip("/")
        if normalised not in _MARKET_DATA_ALLOWED_BASE_URLS:
            allowed = ", ".join(sorted(_MARKET_DATA_ALLOWED_BASE_URLS))
            raise ValueError(f"base_url {value!r} is not an allowed market-data host ({allowed})")
        return normalised

    @field_validator("pairs")
    @classmethod
    def _pairs_parse(cls, value: list[str]) -> list[str]:
        """Require every pair to parse and reject duplicates."""
        canonical: list[str] = []
        for text in value:
            parsed = Pair.from_str(text)
            if isinstance(parsed, Failure):
                raise ValueError(f"pair {text!r} is not a known BASE/QUOTE pair")
            canonical.append(str(parsed.unwrap()))
        if len(set(canonical)) != len(canonical):
            raise ValueError(f"pairs contain duplicates: {value!r}")
        return canonical

    @field_validator("intervals")
    @classmethod
    def _intervals_supported(cls, value: list[KlineInterval]) -> list[KlineInterval]:
        """Allow only 4h and 1d, each at most once."""
        unsupported = [i for i in value if i not in _MARKET_DATA_INTERVALS]
        if unsupported:
            raise ValueError(f"unsupported intervals {unsupported!r}; allowed: 4h, 1d")
        if len(set(value)) != len(value):
            raise ValueError(f"intervals contain duplicates: {value!r}")
        return value

    @model_validator(mode="after")
    def _slow_retry_not_faster(self) -> MarketDataConfig:
        """Reject a slow retry period shorter than the normal one."""
        if self.slow_retry_interval_seconds < self.retry_interval_seconds:
            raise ValueError("slow_retry_interval_seconds must be >= retry_interval_seconds")
        return self

    def parsed_pairs(self) -> tuple[Pair, ...]:
        """Return :attr:`pairs` as :class:`Pair` values, in configured order."""
        return tuple(Pair.from_str(text).unwrap() for text in self.pairs)

    def warmup_bars(self, interval: KlineInterval) -> int:
        """Return the warm-up depth configured for ``interval``.

        Raises:
            ValueError: ``interval`` is not ``4h`` or ``1d``.
        """
        if interval == KlineInterval.H4:
            return self.warmup_bars_4h
        if interval == KlineInterval.D1:
            return self.warmup_bars_1d
        raise ValueError(f"no warm-up depth configured for interval {interval!r}")


class HealthchecksConfig(BaseModel):
    """Root of ``config/healthchecks.yaml`` — dead-man's-switch heartbeat.

    Consumed by :class:`hermes.adapters.healthchecks.hc.HealthchecksAdapter`.
    Healthchecks.io is a dead-man's-switch / heartbeat monitor: hermes
    POSTs / GETs a unique ping URL periodically, and if the pings stop,
    Healthchecks alerts the operator via its own configured channels.
    See https://healthchecks.io/docs/http_api/.

    Attributes:
        ping_url: The unique ping URL for this environment (one UUID per
            hermes tier — dev / sandbox / prod). Empty string means the
            integration is disabled — the adapter refuses to construct
            and ``hermes health healthchecks`` returns NOT_CONFIGURED
            (exit 3) instead of firing wire calls that would fail.
            Format: ``https://hc-ping.com/<uuid>``.
        timeout_sec: Per-request HTTP timeout in seconds. Kept aggressive
            (default 5s) because a slow ping MUST NEVER block the trading
            path — the ping is informational, not on the critical write
            path.
        requests_per_minute: Polite rate cap for the internal token-bucket
            limiter. Healthchecks does not publish a hard limit, but the
            adapter typically pings ~1/minute; 30/min is generous
            headroom for occasional ``ping_start`` + ``ping_success``
            pairs on long jobs.
    """

    model_config = _MODEL_CONFIG

    ping_url: str = Field(default="")
    timeout_sec: float = Field(default=5.0, gt=0)
    requests_per_minute: int = Field(default=30, gt=0, le=6000)

    @property
    def enabled(self) -> bool:
        """Return ``True`` when a non-empty ping URL is configured.

        A blank ``ping_url`` means the integration is intentionally
        disabled — the health check reports NOT_CONFIGURED (exit 3) and
        the adapter refuses to construct.
        """
        return bool(self.ping_url)


class SentryConfig(BaseModel):
    """Root of ``config/sentry.yaml`` — error-reporting SDK configuration.

    Consumed by :mod:`hermes.infrastructure.sentry_integration`. Sentry is
    a hosted error-monitoring service — the SDK ships unhandled exceptions
    (via ``sys.excepthook``) and explicit ``capture_exception()`` calls to
    the Sentry project identified by :attr:`dsn`. Unlike the notifier
    adapters (Telegram / Slack) or Healthchecks, Sentry is an SDK we
    initialize ONCE at process start — there is no per-call adapter method.

    Attributes:
        dsn: The Sentry Data Source Name — a URL of the form
            ``https://<public-key>@<host>/<project-id>``. Empty string
            means the integration is DISABLED — ``initialize()`` becomes
            a no-op, and ``hermes health sentry`` returns NOT_CONFIGURED
            (exit 3). Matches the "empty ping_url = disabled" pattern
            established by :class:`HealthchecksConfig`.
        environment: Environment tag attached to every event. Mirrors
            :attr:`hermes.infrastructure.config.root.HermesConfig.env`
            by default (``"dev"`` on a fresh clone) so Sentry's UI can
            filter dev / sandbox / prod events accordingly. No PII —
            this is purely a runtime tier label.
    """

    model_config = _MODEL_CONFIG

    dsn: str = Field(default="")
    environment: str = Field(default="dev", min_length=1)

    @property
    def enabled(self) -> bool:
        """Return ``True`` when a non-empty DSN is configured.

        A blank ``dsn`` means the integration is intentionally disabled —
        :func:`hermes.infrastructure.sentry_integration.initialize` becomes
        a no-op and ``hermes health sentry`` reports NOT_CONFIGURED
        (exit 3). Same disabled-when-empty convention as
        :class:`HealthchecksConfig`.
        """
        return bool(self.dsn)


class KlinesConfig(BaseModel):
    """Root of ``config/klines.yaml`` — historical-klines fetcher + storage.

    Consumed by the exchange klines adapter (``BinanceKlinesAdapter``) and
    the market-data store (``KlineStore``). Stored separately from the
    exchange endpoint config so a future multi-exchange fetcher factory
    (issue #28 follow-up) can key rate limits per source without
    reshuffling the Binance-specific YAML.

    Attributes:
        store_db_path: Path to the SQLite database that persists klines.
            Empty string is rejected (would resolve to CWD-relative
            ``./`` — a common misconfig). Point at an in-memory database
            (``":memory:"``) for local experimentation only.
        rate_limit_max_weight: Maximum tokens in the fetcher's
            token-bucket per :attr:`rate_limit_window_seconds`. Defaults
            in code buffer below Binance's published 6000 weight/min cap
            (ADR-003 §04).
        rate_limit_window_seconds: Length of the token-bucket refill
            window, in seconds.
    """

    model_config = _MODEL_CONFIG

    store_db_path: str = Field(min_length=1)
    rate_limit_max_weight: int = Field(gt=0, le=6_000)
    rate_limit_window_seconds: int = Field(gt=0, le=3_600)


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

    Two axes live under this model:

    - **PnL / concentration hygiene** — ``annual_realised_pnl_alert_eur``,
      ``wash_sale_window_days``, ``max_position_pct_portfolio``. Used by the
      annual-report + concentration guards.
    - **Recharacterization guard (ADR-002 §Decision 6)** — the rolling
      90-day window over trade count, distinct assets, and fee income that
      keeps the observable signals firmly on the "individual investor"
      side of any plausible AEAT recharacterization line. Crossing any
      threshold emits an ``AnomalyDetected`` and (per ADR-002) pauses
      new signal generation.

    Attributes:
        annual_realised_pnl_alert_eur: Absolute EUR gain in a calendar year
            above which the operator receives a Modelo-100 reminder.
        wash_sale_window_days: Rolling window used to flag potential
            wash-sale patterns (informational — Spain has no wash-sale rule
            per se, but the flag helps the operator explain positions to
            the gestor).
        max_position_pct_portfolio: Concentration cap; single-instrument
            exposure above this ratio blocks new entries.
        recharacterization_window_days: Rolling window (in days) over which
            the recharacterization guard aggregates trades. ADR-002 fixes
            this at 90; the field is configurable so a future tightening
            (e.g. 60 days) does not require a code change.
        trade_count_90d_max: Maximum trade count inside the rolling window
            before the guard fires. ADR-002 §6 sets the initial value at
            500 as an approximation of the informal AEAT "day trader"
            range.
        distinct_assets_90d_max: Maximum count of distinct base assets
            traded inside the rolling window. ADR-002 §6 sets 12 as the
            ceiling; 6 is the planned diversification target.
        fee_income_90d_max_eur: Maximum EUR-denominated fee income
            collected by the operator inside the rolling window. ADR-002
            §6 sets this to €0 (any nonzero value flags an infrastructure
            recharacterization signal). Values recorded in non-EUR
            currencies are counted separately (see the application-layer
            ``WindowStats.fees_by_currency`` field); this threshold ONLY
            gates the EUR bucket.
    """

    model_config = _MODEL_CONFIG

    annual_realised_pnl_alert_eur: Decimal = Field(gt=Decimal("0"))
    wash_sale_window_days: int = Field(ge=1, le=365)
    max_position_pct_portfolio: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))
    recharacterization_window_days: int = Field(ge=1, le=365)
    trade_count_90d_max: int = Field(ge=1)
    distinct_assets_90d_max: int = Field(ge=1)
    fee_income_90d_max_eur: Decimal = Field(ge=Decimal("0"))


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
