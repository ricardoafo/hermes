"""Health-check registry + integration probes for ``hermes health``.

Each registered integration exposes a synchronous probe callable that
returns a :class:`HealthCheckResult`. The CLI (``entrypoints/cli.py``)
maps subcommand names (e.g. ``telegram``, ``slack``) to registry
entries and prints a status table.

Design notes
------------

- **Registry pattern (scaffold)**: PR E will consolidate the registry so
  ``hermes health`` (no arg) enumerates every registered check. Today
  the registry has two entries (``telegram``, ``slack``); the shape
  anticipates Healthchecks, Sentry, and the exchange adapters joining
  later.
- **Wiring layer**: this module lives under ``entrypoints/`` so it MAY
  import ``adapters``, ``application``, and ``infrastructure`` (per
  import-linter contract ``adapters-never-import-entrypoints`` — the
  arrow points the other way). Adapters + config get composed here;
  the CLI stays thin.
- **UTC-only timestamps**: every ``last_check`` field is built via
  :func:`datetime.now` with an explicit ``tz=UTC`` argument. Naive
  datetimes are a code-conventions §2 violation.
- **Exit codes**: distinguish "not configured" (3) from "adapter down"
  (1). Scripts wrapping ``hermes health`` can react differently — a
  fresh clone missing ``.env.local`` should not page an operator.
- **Read-only probes**: every probe hits a read-only third-party
  endpoint (Telegram ``getMe``, Slack ``auth.test``). NEVER call a
  send endpoint (``sendMessage``, ``chat.postMessage``) from a health
  path — a watchdog script would spam the channel on every tick.
"""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import httpx
from pydantic import ValidationError

from hermes.adapters.endpoint import EndpointConfigError
from hermes.adapters.healthchecks import (
    HealthchecksAdapter,
    HealthchecksAdapterConfig,
    HealthchecksError,
)
from hermes.adapters.notifier.slack_adapter import (
    SlackAdapter,
    SlackConfig,
    SlackNotifierError,
)
from hermes.adapters.notifier.telegram_adapter import (
    TelegramAdapter,
    TelegramConfig,
    TelegramNotifierError,
)
from hermes.domain.market_data.bar_schedule import (
    interval_duration,
    is_closed,
    previous_boundary,
)
from hermes.domain.market_data.kline import KlineInterval
from hermes.infrastructure.config.errors import ConfigError
from hermes.infrastructure.config.loader import DEFAULT_CONFIG_DIR, load_yaml_model
from hermes.infrastructure.config.models import MarketDataConfig

__all__ = [
    "EXIT_FAIL",
    "EXIT_NOT_CONFIGURED",
    "EXIT_OK",
    "HealthCheckResult",
    "HealthStatus",
    "aggregate_exit_code",
    "check_healthchecks",
    "check_market_data",
    "check_sentry",
    "check_slack",
    "check_telegram",
    "get_check",
    "registered_integrations",
    "render_json",
    "render_table",
    "render_table_multi",
    "run_all",
]

# Exit-code constants — kept as module-level so both the CLI dispatcher
# and the tests can import them without depending on magic numbers.
EXIT_OK: Final[int] = 0
EXIT_FAIL: Final[int] = 1
EXIT_NOT_CONFIGURED: Final[int] = 3


class HealthStatus:
    """String status labels used in printed tables.

    Constants — no need for a StrEnum. Two labels today; PR E may add
    ``DEGRADED`` when integrations grow richer.
    """

    OK: Final[str] = "OK"
    FAIL: Final[str] = "FAIL"


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    """Outcome of one integration probe.

    Attributes:
        integration: Short label — ``"telegram"``, ``"slack"``, etc.
        status: One of :class:`HealthStatus` labels.
        latency_ms: Wall-clock time the probe took, milliseconds.
        detail: Free-form context (bot username / workspace on OK,
            error message on FAIL). Included in the printed table.
        last_check: UTC-aware timestamp — when the probe ran.
        exit_code: Suggested process exit code (0 / 1 / 3).
    """

    integration: str
    status: str
    latency_ms: float
    detail: str
    last_check: datetime
    exit_code: int


def _now_utc() -> datetime:
    """Return the current UTC-aware datetime.

    Wrapped for testability — freezegun can patch the module-local
    attribute in a single monkeypatch call. Never returns a naive
    datetime.
    """
    return datetime.now(UTC)


def check_telegram(env: dict[str, str]) -> HealthCheckResult:
    """Probe the Telegram Bot API ``getMe`` endpoint.

    Args:
        env: Env-var snapshot. Pass ``os.environ`` in production; tests
            supply a controlled dict so the ambient shell never leaks in.

    Returns:
        A :class:`HealthCheckResult` describing the probe outcome. The
        function does NOT raise on failure — the ``exit_code`` field
        carries the intent instead so the caller can print a table
        before exiting.
    """
    # Config-missing path — distinct exit code (3) so wrapper scripts
    # can distinguish "not configured yet" from "adapter is down".
    try:
        config = TelegramConfig.from_env(env)
    except TelegramNotifierError as exc:
        detail = (
            f"{exc.msg} — populate HERMES_TELEGRAM__BOT_TOKEN and "
            f"HERMES_TELEGRAM__CHAT_ID in .env.local "
            f"(see .env.local.example line 27-28)"
        )
        return HealthCheckResult(
            integration="telegram",
            status=HealthStatus.FAIL,
            latency_ms=0.0,
            detail=detail,
            last_check=_now_utc(),
            exit_code=EXIT_NOT_CONFIGURED,
        )
    except EndpointConfigError as exc:
        return HealthCheckResult(
            integration="telegram",
            status=HealthStatus.FAIL,
            latency_ms=0.0,
            detail=str(exc),
            last_check=_now_utc(),
            exit_code=EXIT_NOT_CONFIGURED,
        )

    adapter = TelegramAdapter.create(config)
    start = time.perf_counter()
    try:
        result = adapter.get_me()
    except TelegramNotifierError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return HealthCheckResult(
            integration="telegram",
            status=HealthStatus.FAIL,
            latency_ms=elapsed_ms,
            detail=str(exc),
            last_check=_now_utc(),
            exit_code=EXIT_FAIL,
        )
    finally:
        adapter.close()

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    username = result.get("username", "<unknown>")
    return HealthCheckResult(
        integration="telegram",
        status=HealthStatus.OK,
        latency_ms=elapsed_ms,
        detail=f"bot=@{username}",
        last_check=_now_utc(),
        exit_code=EXIT_OK,
    )


def check_slack(env: dict[str, str]) -> HealthCheckResult:
    """Probe the Slack Web API ``auth.test`` endpoint.

    ``auth.test`` is the canonical READ-ONLY validation call: it
    returns the workspace name (``team``) and bot user (``user``) tied
    to the token WITHOUT sending anything to any channel. Never call
    ``chat.postMessage`` from a health path — a watchdog loop would
    spam the channel every tick.

    Args:
        env: Env-var snapshot. Pass ``os.environ`` in production; tests
            supply a controlled dict so the ambient shell never leaks in.

    Returns:
        A :class:`HealthCheckResult` describing the probe outcome. The
        function does NOT raise on failure — the ``exit_code`` field
        carries the intent instead so the caller can print a table
        before exiting.
    """
    # Config-missing path — distinct exit code (3) so wrapper scripts
    # can distinguish "not configured yet" from "adapter is down".
    try:
        config = SlackConfig.from_env(env)
    except SlackNotifierError as exc:
        detail = (
            f"{exc.msg} — populate HERMES_SLACK__BOT_TOKEN and "
            f"HERMES_SLACK__DEFAULT_CHANNEL in .env.local "
            f"(see .env.local.example)"
        )
        return HealthCheckResult(
            integration="slack",
            status=HealthStatus.FAIL,
            latency_ms=0.0,
            detail=detail,
            last_check=_now_utc(),
            exit_code=EXIT_NOT_CONFIGURED,
        )
    except EndpointConfigError as exc:
        return HealthCheckResult(
            integration="slack",
            status=HealthStatus.FAIL,
            latency_ms=0.0,
            detail=str(exc),
            last_check=_now_utc(),
            exit_code=EXIT_NOT_CONFIGURED,
        )

    adapter = SlackAdapter.create(config)
    start = time.perf_counter()
    try:
        result = adapter.auth_test()
    except SlackNotifierError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return HealthCheckResult(
            integration="slack",
            status=HealthStatus.FAIL,
            latency_ms=elapsed_ms,
            detail=str(exc),
            last_check=_now_utc(),
            exit_code=EXIT_FAIL,
        )
    finally:
        adapter.close()

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    team = result.get("team", "<unknown>")
    user = result.get("user", "<unknown>")
    return HealthCheckResult(
        integration="slack",
        status=HealthStatus.OK,
        latency_ms=elapsed_ms,
        detail=f"workspace={team} bot=@{user}",
        last_check=_now_utc(),
        exit_code=EXIT_OK,
    )


def check_healthchecks(env: dict[str, str]) -> HealthCheckResult:
    """Probe the Healthchecks.io ``/start`` endpoint (informational — no alert fires).

    Uses :meth:`HealthchecksAdapter.ping_start`. ``ping_start`` is the
    right choice for the health path — it is idempotent + informational,
    and does NOT fire the dead-man's-switch alert. Calling
    :meth:`HealthchecksAdapter.ping_fail` from a health probe would
    page the operator on every ``hermes health`` tick (documented
    anti-pattern in the adapter docstring).

    Args:
        env: Env-var snapshot. Pass ``os.environ`` in production; tests
            supply a controlled dict so the ambient shell never leaks in.

    Returns:
        A :class:`HealthCheckResult` describing the probe outcome. The
        function does NOT raise on failure — the ``exit_code`` field
        carries the intent instead so the caller can print a table
        before exiting.
    """
    # Config-missing path — distinct exit code (3) so wrapper scripts
    # can distinguish "not configured yet" from "adapter is down".
    try:
        config = HealthchecksAdapterConfig.from_env(env)
    except HealthchecksError as exc:
        detail = (
            f"{exc.msg} — populate HERMES_HEALTHCHECKS__PING_URL "
            f"in .env.local (see .env.local.example line 59-60)"
        )
        return HealthCheckResult(
            integration="healthchecks",
            status=HealthStatus.FAIL,
            latency_ms=0.0,
            detail=detail,
            last_check=_now_utc(),
            exit_code=EXIT_NOT_CONFIGURED,
        )

    adapter = HealthchecksAdapter.create(config)
    start = time.perf_counter()
    try:
        adapter.ping_start(details="hermes health probe")
    except HealthchecksError as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return HealthCheckResult(
            integration="healthchecks",
            status=HealthStatus.FAIL,
            latency_ms=elapsed_ms,
            detail=str(exc),
            last_check=_now_utc(),
            exit_code=EXIT_FAIL,
        )
    finally:
        adapter.close()

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return HealthCheckResult(
        integration="healthchecks",
        status=HealthStatus.OK,
        latency_ms=elapsed_ms,
        detail="ping accepted",
        last_check=_now_utc(),
        exit_code=EXIT_OK,
    )


def check_sentry(env: dict[str, str]) -> HealthCheckResult:
    """Parse the Sentry DSN — read-only, no network, no test event.

    Unlike Telegram / Slack / Healthchecks, we do NOT hit the Sentry
    wire here. Sentry's free tier caps at 5,000 events/month, and
    firing a test event on every ``hermes health`` invocation would
    burn the operator's quota within days. Instead we validate the
    DSN URL parses correctly via :class:`sentry_sdk.utils.Dsn`, which
    is what ``sentry_sdk.init`` itself does before opening the
    transport. A parse failure at this layer catches every misconfig
    the SDK itself would raise on ``init`` — enough signal to gate
    ``hermes health`` without wire cost.

    If ``HERMES_SENTRY__DSN`` is empty the integration is DISABLED —
    same convention as Healthchecks (empty ping URL) and matches
    :attr:`SentryConfig.enabled`. Reports NOT_CONFIGURED (exit 3) so
    wrapper scripts can distinguish a fresh clone from a real outage.

    Args:
        env: Env-var snapshot. Pass ``os.environ`` in production; tests
            supply a controlled dict so the ambient shell never leaks in.

    Returns:
        A :class:`HealthCheckResult` describing the probe outcome. The
        function does NOT raise on failure — the ``exit_code`` field
        carries the intent instead so the caller can print a table
        before exiting. ``latency_ms`` is always ``0.0`` because no
        wire call is made.
    """
    # Local import to keep sentry_sdk off the import path when the caller
    # never invokes this probe. The SDK is a runtime dep, so it is always
    # importable — but the import is ~50ms cold, and the CLI already pays
    # the httpx / structlog cost. Localising it here matches how the
    # notifier probes localise their httpx.Client construction.
    from sentry_sdk.utils import (  # noqa: PLC0415 — deliberate lazy import (see comment above)
        BadDsn,
        Dsn,
    )

    dsn = env.get("HERMES_SENTRY__DSN", "").strip()
    if not dsn:
        return HealthCheckResult(
            integration="sentry",
            status=HealthStatus.FAIL,
            latency_ms=0.0,
            detail=(
                "Sentry is disabled (empty DSN) — populate HERMES_SENTRY__DSN "
                "in .env.local (see .env.local.example line 62-66)"
            ),
            last_check=_now_utc(),
            exit_code=EXIT_NOT_CONFIGURED,
        )

    environment = env.get("HERMES_SENTRY__ENVIRONMENT", "dev").strip() or "dev"

    try:
        parsed = Dsn(dsn)
    except BadDsn as exc:
        # Malformed DSN — same class of error the SDK's own init would
        # raise. Surface it as FAIL (exit 1) so watchdogs distinguish
        # "disabled" (exit 3) from "misconfigured" (exit 1).
        return HealthCheckResult(
            integration="sentry",
            status=HealthStatus.FAIL,
            latency_ms=0.0,
            detail=f"malformed DSN: {exc}",
            last_check=_now_utc(),
            exit_code=EXIT_FAIL,
        )

    # DSN parses. Do NOT init the SDK, do NOT send a test event — see
    # the docstring for the free-tier-quota rationale.
    host = parsed.host
    project = parsed.project_id
    return HealthCheckResult(
        integration="sentry",
        status=HealthStatus.OK,
        latency_ms=0.0,
        detail=f"DSN parsed host={host} project={project} environment={environment}",
        last_check=_now_utc(),
        exit_code=EXIT_OK,
    )


# The probe follows one bar series: BTC/USDT 4h, the strategy's primary
# timeframe. Binance spells the symbol without a separator.
_PROBE_SYMBOL: Final[str] = "BTCUSDT"
_PROBE_INTERVAL: Final[KlineInterval] = KlineInterval.H4
_ONE_MS: Final[timedelta] = timedelta(milliseconds=1)
_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)


class _ProbeError(Exception):
    """The market-data endpoint answered, but not with something the probe can use."""


def _market_data_config(env: dict[str, str]) -> MarketDataConfig:
    """Load the market-data config: ``config/market_data.yaml`` plus env overrides.

    A missing file falls back to the built-in defaults, which equal the shipped
    file, so the probe works from any working directory. Env overrides apply
    either way.

    Raises:
        ConfigError: The file exists but is malformed, or the result fails validation.
    """
    path = DEFAULT_CONFIG_DIR / "market_data.yaml"
    if path.is_file():
        return load_yaml_model(path, MarketDataConfig, env_section="MARKET_DATA", env=env)
    prefix = "HERMES_MARKET_DATA__"
    overrides = {
        key.removeprefix(prefix).lower(): value
        for key, value in env.items()
        if key.startswith(prefix)
    }
    try:
        return MarketDataConfig.model_validate(overrides)
    except ValidationError as exc:
        raise ConfigError(f"validation failed for built-in market_data defaults: {exc}") from exc


def _probe_get_json(client: httpx.Client, url: str, params: dict[str, str] | None) -> object:
    """GET ``url`` and return the decoded JSON body.

    Raises:
        httpx.HTTPError: Transport failure.
        _ProbeError: Non-200 status or a non-JSON body.
    """
    response = client.get(url, params=params)
    if response.status_code != httpx.codes.OK:
        raise _ProbeError(f"HTTP {response.status_code} from {url}")
    try:
        decoded: object = response.json()
    except ValueError as exc:
        raise _ProbeError(f"non-JSON body from {url}") from exc
    return decoded


def _probe_server_time(client: httpx.Client, config: MarketDataConfig) -> datetime:
    """Return the exchange clock from ``GET /api/v3/time``."""
    payload = _probe_get_json(client, f"{config.base_url}/api/v3/time", None)
    server_time = payload.get("serverTime") if isinstance(payload, dict) else None
    if not isinstance(server_time, int) or isinstance(server_time, bool):
        raise _ProbeError(f"/api/v3/time returned no integer serverTime: {payload!r}")
    return _EPOCH + timedelta(milliseconds=server_time)


def _probe_open_times(client: httpx.Client, config: MarketDataConfig) -> list[datetime]:
    """Return the ``open_time`` of the newest BTC/USDT 4h rows (the last may be open)."""
    payload = _probe_get_json(
        client,
        f"{config.base_url}/api/v3/klines",
        {"symbol": _PROBE_SYMBOL, "interval": _PROBE_INTERVAL.value, "limit": "2"},
    )
    if not isinstance(payload, list):
        raise _ProbeError(f"/api/v3/klines returned a non-list body: {str(payload)[:200]}")
    open_times: list[datetime] = []
    for row in payload:
        open_ms = row[0] if isinstance(row, list) and row else None
        if not isinstance(open_ms, int) or isinstance(open_ms, bool):
            raise _ProbeError(f"klines row has no integer open_time: {row!r}")
        open_times.append(_EPOCH + timedelta(milliseconds=open_ms))
    return open_times


def _market_data_verdict(
    config: MarketDataConfig,
    *,
    local_now: datetime,
    exchange_now: datetime,
    open_times: list[datetime],
) -> tuple[str, int, str]:
    """Judge skew and staleness; return ``(status, exit_code, detail)``.

    Fails when the two clocks differ by more than the settle delay, or when
    the 4h bar that should have closed is still missing ``stale_after_seconds``
    after its close.
    """
    settle = timedelta(seconds=config.settle_delay_seconds)
    skew_ms = (local_now - exchange_now) // _ONE_MS
    if abs(local_now - exchange_now) > settle:
        detail = f"clock skew {skew_ms} ms exceeds settle delay {config.settle_delay_seconds:g} s"
        return HealthStatus.FAIL, EXIT_FAIL, detail
    now = min(local_now, exchange_now)
    duration = interval_duration(_PROBE_INTERVAL)
    expected_open = previous_boundary(now - settle, _PROBE_INTERVAL) - duration
    closed_opens = [
        open_time
        for open_time in open_times
        if is_closed(
            open_time,
            _PROBE_INTERVAL,
            local_now=local_now,
            exchange_now=exchange_now,
            settle=settle,
        )
    ]
    newest = max(closed_opens, default=None)
    if newest is not None and newest >= expected_open:
        detail = f"newest closed 4h bar opened {newest.isoformat()} skew_ms={skew_ms}"
        return HealthStatus.OK, EXIT_OK, detail
    overdue = (now - (expected_open + duration)).total_seconds()
    if overdue >= config.stale_after_seconds:
        detail = (
            f"stale: 4h bar opened {expected_open.isoformat()} is {overdue:.0f} s past its "
            f"close (limit {config.stale_after_seconds:g} s)"
        )
        return HealthStatus.FAIL, EXIT_FAIL, detail
    detail = (
        f"4h bar opened {expected_open.isoformat()} not visible yet, {overdue:.0f} s past close"
    )
    return HealthStatus.OK, EXIT_OK, detail


def check_market_data(env: dict[str, str]) -> HealthCheckResult:
    """Probe the public market-data host: reachable, clocks agree, feed not stale.

    Stateless by design: every check is a pure function of the env and the
    exchange's answers, so the probe cannot read the serving process's memory.
    "The service is alive" is the Healthchecks.io ping's job. Read-only: two
    GETs (``/api/v3/time`` and ``/api/v3/klines``), no credentials.

    Fails (exit 1) when the host is unreachable, the local and exchange clocks
    differ by more than the settle delay, or the newest closed BTC/USDT 4h bar
    is missing ``stale_after_seconds`` past its close.

    Args:
        env: Env-var snapshot. ``HERMES_MARKET_DATA__<FIELD>`` overrides
            ``config/market_data.yaml``.

    Returns:
        A :class:`HealthCheckResult`. The function does NOT raise on failure.
    """
    try:
        config = _market_data_config(env)
    except ConfigError as exc:
        return HealthCheckResult(
            integration="market_data",
            status=HealthStatus.FAIL,
            latency_ms=0.0,
            detail=f"invalid market_data config: {exc}",
            last_check=_now_utc(),
            exit_code=EXIT_FAIL,
        )
    start = time.perf_counter()
    try:
        with httpx.Client(timeout=config.timeout_sec) as client:
            local_now = _now_utc()
            exchange_now = _probe_server_time(client, config)
            open_times = _probe_open_times(client, config)
    except (httpx.HTTPError, _ProbeError) as exc:
        return HealthCheckResult(
            integration="market_data",
            status=HealthStatus.FAIL,
            latency_ms=(time.perf_counter() - start) * 1000.0,
            detail=f"market data source unreachable or unusable: {exc}",
            last_check=_now_utc(),
            exit_code=EXIT_FAIL,
        )
    status, exit_code, detail = _market_data_verdict(
        config, local_now=local_now, exchange_now=exchange_now, open_times=open_times
    )
    return HealthCheckResult(
        integration="market_data",
        status=status,
        latency_ms=(time.perf_counter() - start) * 1000.0,
        detail=detail,
        last_check=_now_utc(),
        exit_code=exit_code,
    )


# Registry — every registered integration is walked by :func:`run_all`
# for the no-arg ``hermes health`` invocation. Sort order in the registry
# is not user-visible: :func:`registered_integrations` sorts before
# returning. Each check is a pure function of the env snapshot — it must
# not read module-level state, so tests can pin the outcome via a
# controlled env dict.
_REGISTRY: Final[dict[str, Callable[[dict[str, str]], HealthCheckResult]]] = {
    "healthchecks": check_healthchecks,
    "market_data": check_market_data,
    "sentry": check_sentry,
    "slack": check_slack,
    "telegram": check_telegram,
}


def registered_integrations() -> tuple[str, ...]:
    """Return the sorted tuple of integration names known to the registry.

    Returns:
        Tuple of integration labels in stable (sorted) order.
    """
    return tuple(sorted(_REGISTRY))


def get_check(name: str) -> Callable[[dict[str, str]], HealthCheckResult] | None:
    """Return the probe callable for ``name`` or ``None`` if unregistered.

    Args:
        name: Integration label — lowercase, matches CLI argument
            (e.g. ``"telegram"``, ``"slack"``).

    Returns:
        The probe callable, or ``None`` when ``name`` is not registered.
    """
    return _REGISTRY.get(name)


def render_table(result: HealthCheckResult, stream: Any) -> None:
    """Print ``result`` as a two-column key/value table to ``stream``.

    Kept intentionally minimal — no ``rich`` dependency, no ANSI
    colours. The CLI is invoked by systemd + CI runners that would
    strip escapes anyway; a plain table stays readable in journald.

    Args:
        result: The health-check outcome to render.
        stream: File-like object with a ``.write`` method.
    """
    rows = [
        ("integration", result.integration),
        ("status", result.status),
        ("latency_ms", f"{result.latency_ms:.1f}"),
        ("detail", result.detail),
        ("last_check", result.last_check.isoformat()),
    ]
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        stream.write(f"  {key.ljust(width)}  {value}\n")


_SECRET_KEY = re.compile(r"TOKEN|SECRET|PASSWORD|DSN|KEY|PING_URL", re.IGNORECASE)
_REDACTED: Final[str] = "***"


def _redact(text: str, env: dict[str, str]) -> str:
    """Mask every secret-looking env value found inside ``text``."""
    secrets = sorted(
        (v for k, v in env.items() if v and _SECRET_KEY.search(k)),
        key=len,
        reverse=True,
    )
    for value in secrets:
        text = text.replace(value, _REDACTED)
    return text


def render_json(results: list[HealthCheckResult], env: dict[str, str], stream: Any) -> int:
    """Print ``results`` as one JSON document and return the aggregate exit code.

    The document is ``{"integrations": [{"name", "status", "detail",
    "latency_ms", "last_check", "exit_code"}, ...], "exit_code": N}``.
    Any env value whose key looks like a secret is masked in ``detail``,
    so a probe error that echoes a URL carrying a token cannot leak it.

    Args:
        results: The health-check outcomes to render.
        env: Env snapshot the probes ran with; source of the values to mask.
        stream: File-like object with a ``.write`` method.

    Returns:
        The aggregate exit code, also written as ``exit_code``.
    """
    exit_code = aggregate_exit_code(results)
    document = {
        "integrations": [
            {
                "name": r.integration,
                "status": r.status,
                "detail": _redact(r.detail, env),
                "latency_ms": round(r.latency_ms, 1),
                "last_check": r.last_check.isoformat(),
                "exit_code": r.exit_code,
            }
            for r in results
        ],
        "exit_code": exit_code,
    }
    stream.write(json.dumps(document) + "\n")
    return exit_code


def run_all(env: dict[str, str]) -> list[HealthCheckResult]:
    """Run every registered integration probe and return the outcomes.

    Pure function of the env snapshot — testable without stubbing the
    CLI dispatcher. Iteration order matches
    :func:`registered_integrations` (sorted) so the printed table is
    deterministic across runs.

    Args:
        env: Env-var snapshot. Pass ``os.environ`` in production; tests
            supply a controlled dict so the ambient shell never leaks
            in.

    Returns:
        A list of :class:`HealthCheckResult`, one per registered
        integration, in the order returned by
        :func:`registered_integrations`.
    """
    return [_REGISTRY[name](env) for name in registered_integrations()]


def aggregate_exit_code(results: list[HealthCheckResult]) -> int:
    """Aggregate a list of check results into a single process exit code.

    Rules (documented in the task spec):

    - :data:`EXIT_FAIL` (1) if ANY result is FAIL — an actual adapter
      failure trumps everything else.
    - :data:`EXIT_NOT_CONFIGURED` (3) if any result is NOT_CONFIGURED
      and none are FAIL — partial configuration is a fresh-clone
      state, not a system-down state, so it exits distinctly from OK
      but does not signal an outage.
    - :data:`EXIT_OK` (0) when every result is OK.

    Args:
        results: The outcomes to aggregate. Empty list yields
            :data:`EXIT_OK` (nothing to check → nothing to fail).

    Returns:
        The aggregated exit code.
    """
    any_fail = any(r.exit_code == EXIT_FAIL for r in results)
    if any_fail:
        return EXIT_FAIL
    any_not_configured = any(r.exit_code == EXIT_NOT_CONFIGURED for r in results)
    if any_not_configured:
        return EXIT_NOT_CONFIGURED
    return EXIT_OK


# Header labels for the multi-row table. Kept as a module-level constant
# so tests can assert on the exact column names without re-declaring
# them.
_MULTI_TABLE_HEADERS: Final[tuple[str, ...]] = (
    "integration",
    "status",
    "latency",
    "detail",
    "last_check",
)


def render_table_multi(results: list[HealthCheckResult], stream: Any) -> None:
    """Print ``results`` as a multi-row aligned table to ``stream``.

    Column widths adjust to the widest value per column so the output
    stays readable even when one integration reports a long ``detail``
    (e.g. an HTTP error excerpt) and another reports a short one.
    No ANSI colours, no ``rich`` dependency — same rationale as
    :func:`render_table` (journald / CI logs strip escapes anyway).

    An empty ``results`` list prints only the header row — an empty
    registry is a legitimate "nothing to check yet" state and should
    not raise.

    Args:
        results: The list of health-check outcomes to render.
        stream: File-like object with a ``.write`` method.
    """
    # Convert each row into strings up-front so width computation and
    # rendering both operate on the same cells.
    header_row = _MULTI_TABLE_HEADERS
    data_rows: list[tuple[str, ...]] = [
        (
            r.integration,
            r.status,
            f"{r.latency_ms:.0f}ms",
            r.detail,
            r.last_check.isoformat(),
        )
        for r in results
    ]

    all_rows: list[tuple[str, ...]] = [header_row, *data_rows]
    # Column widths = max cell width per column (across header + data).
    widths = [max(len(row[col]) for row in all_rows) for col in range(len(header_row))]

    def _format_row(row: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() + "\n"

    stream.write(_format_row(header_row))
    for row in data_rows:
        stream.write(_format_row(row))
