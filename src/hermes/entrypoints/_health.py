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

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

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

__all__ = [
    "EXIT_FAIL",
    "EXIT_NOT_CONFIGURED",
    "EXIT_OK",
    "HealthCheckResult",
    "HealthStatus",
    "aggregate_exit_code",
    "check_healthchecks",
    "check_sentry",
    "check_slack",
    "check_telegram",
    "get_check",
    "registered_integrations",
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


# Registry — every registered integration is walked by :func:`run_all`
# for the no-arg ``hermes health`` invocation. Sort order in the registry
# is not user-visible: :func:`registered_integrations` sorts before
# returning. Each check is a pure function of the env snapshot — it must
# not read module-level state, so tests can pin the outcome via a
# controlled env dict.
_REGISTRY: Final[dict[str, Callable[[dict[str, str]], HealthCheckResult]]] = {
    "healthchecks": check_healthchecks,
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
